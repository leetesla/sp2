"""Exercise sp2 against an isolated Supervisor, never the host's services."""

import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SP2 = Path(__file__).resolve().parents[1] / "sp2"
CTL = shutil.which("supervisorctl")


class TranslationTests(unittest.TestCase):
    def test_help_and_no_command_work_without_supervisor_or_sudo(self):
        env = {**os.environ, "PATH": "", "SP2_SUPERVISORCTL": "/missing/supervisorctl"}
        outputs = []
        for args in [(), ("help",), ("--help",), ("--dry-run",),
                     ("--sudo", "help"), ("-c", "/missing/config", "help")]:
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(SP2), *args], env=env,
                                        text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn("restart", result.stdout)
                self.assertIn("显示命令使用帮助", result.stdout)
                outputs.append(result.stdout)
        self.assertTrue(all(output == outputs[0] for output in outputs))

    def run_sp2(self, *arguments):
        env = {**os.environ, "SP2_SUPERVISORCTL": "supervisorctl", "SP2_CONFIG": ""}
        return subprocess.run([sys.executable, str(SP2), "--dry-run", *arguments],
                              env=env, text=True, capture_output=True, timeout=5)

    def test_connection_options_and_group_remain_distinct_arguments(self):
        result = self.run_sp2("--sudo", "-c", "/tmp/config with spaces.conf", "restart", "batch:*")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(shlex.split(result.stdout), ["sudo", "supervisorctl", "-c",
                         "/tmp/config with spaces.conf", "restart", "batch:*"])

    def test_pid_defaults_to_children_and_reload_never_restarts_daemon(self):
        for args, expected in [(("ls",), ["status"]), (("pid",), ["pid", "all"]),
                               (("reload", "worker"), ["update", "worker"]),
                               (("sendSignal", "SIGUSR1", "api"), ["signal", "SIGUSR1", "api"])]:
            with self.subTest(args=args):
                result = self.run_sp2(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(shlex.split(result.stdout), ["supervisorctl", *expected])

    def test_invalid_commands_and_log_options_do_not_call_supervisor(self):
        for args in [("delete", "api"), ("logs", "all"), ("logs", "batch:*"),
                     ("logs", "api", "--lines", "10"), ("logs", "api", "--bytes", "10"),
                     ("logs", "api", "--nostream", "--bytes", "0"),
                     ("stop", "api worker"), ("ctl",), ("help", "api")]:
            with self.subTest(args=args):
                result = self.run_sp2(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")


@unittest.skipUnless(CTL and importlib.util.find_spec("supervisor"), "Supervisor is required")
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sp2-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "supervisord.conf"
        (self.root / "programs").mkdir()
        self.worker = self.root / "worker.py"
        self.worker.write_text(
            "import os, sys, time\n"
            "print('stdout-' + os.environ.get('SP2_MARKER', 'before'), flush=True)\n"
            "print('stderr-ready', file=sys.stderr, flush=True)\n"
            "while True: time.sleep(0.05)\n"
        )
        for name in ["api", "worker", "batch"]:
            text = (
                "[program:{name}]\ncommand={python} {script}\n"
                "autostart=true\nautorestart=false\nstartsecs=0\nstopwaitsecs=2\n"
                "environment=SP2_MARKER=\"before\"\n"
                "stdout_logfile={root}/{name}.out.log\nstderr_logfile={root}/{name}.err.log\n"
            ).format(name=name, python=shlex.quote(sys.executable),
                     script=shlex.quote(str(self.worker)), root=self.root)
            if name == "batch":
                text += "numprocs=2\nprocess_name=%(program_name)s_%(process_num)02d\n"
                text = text.replace("batch.out.log", "batch_%(process_num)02d.out.log")
                text = text.replace("batch.err.log", "batch_%(process_num)02d.err.log")
            (self.root / "programs" / (name + ".conf")).write_text(text)
        self.config.write_text(
            "[supervisord]\nlogfile={root}/daemon.log\npidfile={root}/daemon.pid\n"
            "childlogdir={root}\n"
            "[unix_http_server]\nfile={root}/supervisor.sock\nchmod=0700\n"
            "[supervisorctl]\nserverurl=unix://{root}/supervisor.sock\n"
            "[rpcinterface:supervisor]\n"
            "supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n"
            "[include]\nfiles={root}/programs/*.conf\n".format(root=self.root)
        )
        self.env = {**os.environ, "SP2_CONFIG": str(self.config), "SP2_SUPERVISORCTL": CTL}
        output = (self.root / "foreground.log").open("w")
        self.addCleanup(output.close)
        self.daemon = subprocess.Popen([sys.executable, "-m", "supervisor.supervisord",
                                       "-n", "-c", str(self.config)], stdout=output, stderr=output)
        self.addCleanup(self.stop_daemon)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.daemon.poll() is not None:
                self.fail((self.root / "foreground.log").read_text())
            if (self.root / "api.out.log").exists() and "stdout-before" in (self.root / "api.out.log").read_text():
                break
            time.sleep(0.05)
        else:
            self.fail("Temporary Supervisor did not become ready")

    def stop_daemon(self):
        if self.daemon.poll() is None:
            self.daemon.terminate()
            self.daemon.wait(timeout=10)

    def run_sp2(self, *arguments):
        return subprocess.run([sys.executable, str(SP2), *arguments], env=self.env,
                              text=True, capture_output=True, timeout=10)

    def assert_ok(self, *arguments):
        result = self.run_sp2(*arguments)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_lifecycle_groups_and_status_exit_code(self):
        self.assertIn("RUNNING", self.assert_ok("ls"))
        self.assert_ok("stop", "batch:*")
        result = self.run_sp2("status", "batch:*")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("STOPPED"), 2)
        self.assert_ok("start", "batch:*")
        previous_pid = self.assert_ok("pid", "api").strip()
        self.assert_ok("restart", "api")
        self.assertNotEqual(self.assert_ok("pid", "api").strip(), previous_pid)
        self.assert_ok("stop", "api", "worker")
        self.assertIn("STOPPED", self.run_sp2("status", "api").stdout)
        self.assert_ok("start", "api", "worker")
        self.assertNotEqual(self.run_sp2("restart", "missing-app").returncode, 0)

    def test_logs_and_flush(self):
        self.assertIn("stdout-before", self.assert_ok("logs", "api", "--nostream"))
        self.assertIn("stderr-ready", self.assert_ok("logs", "api", "--err", "--nostream"))
        self.assertIn("before", self.assert_ok("logs", "api", "--nostream", "--bytes", "7"))
        self.assert_ok("flush", "api")
        self.assertEqual((self.root / "api.out.log").read_text(), "")
        self.assertEqual((self.root / "api.err.log").read_text(), "")
        self.assertIn("stdout-before", (self.root / "worker.out.log").read_text())

    def test_log_stream_interrupt_leaves_app_running(self):
        stream = subprocess.Popen([sys.executable, str(SP2), "logs", "api"], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, start_new_session=True)
        try:
            time.sleep(0.4)
            os.killpg(stream.pid, signal.SIGINT)
            stream.communicate(timeout=5)
            self.assertIn(stream.returncode, [130, -signal.SIGINT])
            self.assertIn("RUNNING", self.assert_ok("status", "api"))
        finally:
            if stream.poll() is None:
                os.killpg(stream.pid, signal.SIGKILL)
                stream.communicate(timeout=5)

    def test_reload_applies_only_target_group(self):
        api_pid = self.assert_ok("pid", "api").strip()
        worker_pid = self.assert_ok("pid", "worker").strip()
        conf = self.root / "programs/worker.conf"
        conf.write_text(conf.read_text().replace('SP2_MARKER="before"', 'SP2_MARKER="after"'))
        self.assertIn("changed", self.assert_ok("reread"))
        self.assertEqual(self.assert_ok("pid", "worker").strip(), worker_pid)
        self.assert_ok("reload", "worker")
        self.assertEqual(self.assert_ok("pid", "api").strip(), api_pid)
        self.assertNotEqual(self.assert_ok("pid", "worker").strip(), worker_pid)

    def test_connection_failure_is_not_success(self):
        result = self.run_sp2("-s", "unix://" + str(self.root / "missing.sock"), "ls")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
