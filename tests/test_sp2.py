"""Exercise sp2 against an isolated Supervisor, never the host's services."""

import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from contextlib import nullcontext, redirect_stderr
from types import SimpleNamespace


SP2 = Path(__file__).resolve().parents[1] / "sp2"
CTL = shutil.which("supervisorctl")
LOADER = SourceFileLoader("sp2_under_test", str(SP2))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
SP2_MODULE = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(SP2_MODULE)


class TranslationTests(unittest.TestCase):
    def test_soft_delete_explains_config_lookup_failure(self):
        stderr = io.StringIO()
        with patch.object(SP2_MODULE, "locate_config", return_value=None), \
                patch.object(sys, "argv", ["sp2", "del", "api"]), \
                patch.object(SP2_MODULE.subprocess, "run") as backend, redirect_stderr(stderr):
            self.assertEqual(SP2_MODULE.main(), 1)
        self.assertIn("未找到 Supervisor 主配置", stderr.getvalue())
        backend.assert_not_called()

    def test_config_lookup_supports_homebrew_prefixes_and_ubuntu(self):
        args = SimpleNamespace(config=None)
        for executable, expected in [
                ("/opt/homebrew/bin/supervisorctl", "/opt/homebrew/etc/supervisord.conf"),
                ("/usr/local/bin/supervisorctl", "/usr/local/etc/supervisord.conf"),
                ("/usr/bin/supervisorctl", "/etc/supervisor/supervisord.conf")]:
            with self.subTest(executable=executable), \
                    patch.object(SP2_MODULE.shutil, "which", return_value=executable), \
                    patch.object(Path, "is_file", lambda path: str(path) == expected):
                self.assertEqual(SP2_MODULE.locate_config(args), Path(expected))

    def test_memory_aggregates_group_rss_and_handles_missing_samples(self):
        args = SimpleNamespace(config=None, serverurl="unix:///tmp/test.sock", sudo=False)
        groups = {"workers": [{"pid": "100"}, {"pid": "101"}],
                  "stopped": [{"pid": "-"}], "gone": [{"pid": "102"}]}
        sample = subprocess.CompletedProcess([], 1, "  100  10240\n  101  20480\n", "")
        with patch.object(SP2_MODULE.subprocess, "run", return_value=sample):
            memory = SP2_MODULE.group_memory(args, groups)
        self.assertEqual(memory, {"workers": "30.0 MiB", "stopped": "0 B", "gone": "-"})
        with patch.object(SP2_MODULE.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(SP2_MODULE.group_memory(args, groups)["workers"], "-")

    def test_remote_memory_never_looks_up_remote_pids_locally(self):
        args = SimpleNamespace(config=None, serverurl="http://remote.example:9001", sudo=False)
        with patch.object(SP2_MODULE.subprocess, "run") as sample:
            self.assertEqual(SP2_MODULE.group_memory(args, {"api": [{"pid": "100"}]}), {"api": "-"})
        sample.assert_not_called()

    def test_id_for_group_named_all_never_expands_to_every_group(self):
        groups = {"all": [{"name": "all"}]}
        registry = {"groups": {"all": 4}}
        for command, mode, expected in [("status", "lifecycle", ["all:*"]),
                                         ("sendSignal", "lifecycle", ["all:*"]),
                                         ("pid", "processes", ["all:all"]),
                                         ("flush", "processes", ["all:all"]),
                                         ("logs", "processes", ["all:all"])]:
            args = SimpleNamespace(command=command)
            with self.subTest(command=command), \
                    patch.object(SP2_MODULE, "read_status", return_value=(groups, 0)), \
                    patch.object(SP2_MODULE, "id_registry", return_value=nullcontext(registry)):
                self.assertEqual(SP2_MODULE.resolve_command_targets(args, ["4"], mode), expected)
        with patch.object(SP2_MODULE, "read_status", return_value=(groups, 0)), \
                patch.object(SP2_MODULE, "id_registry", return_value=nullcontext(registry)):
            with self.assertRaises(SP2_MODULE.Sp2Error):
                SP2_MODULE.resolve_command_targets(SimpleNamespace(command="reload"), ["4"])

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
                               (("start", "api"), ["start", "api:*"]),
                               (("stop", "all"), ["stop", "all"]),
                               (("restart", "name:123"), ["restart", "123:*"]),
                               (("stop", "batch:batch_00"), ["stop", "batch:batch_00"]),
                               (("ctl", "status", "0"), ["status", "0"]),
                               (("sendSignal", "SIGUSR1", "api"), ["signal", "SIGUSR1", "api:*"])]:
            with self.subTest(args=args):
                result = self.run_sp2(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(shlex.split(result.stdout), ["supervisorctl", *expected])

    def test_invalid_commands_and_log_options_do_not_call_supervisor(self):
        for args in [("scale", "api"), ("del",), ("logs", "all"), ("logs", "batch:*"),
                     ("logs", "api", "--lines", "10"), ("logs", "api", "--bytes", "10"),
                     ("logs", "api", "--nostream", "--bytes", "0"),
                     ("stop", "api worker"), ("del", "api worker"), ("ctl",), ("help", "api")]:
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
        self.env = {**os.environ, "SP2_CONFIG": str(self.config), "SP2_SUPERVISORCTL": CTL,
                    "HOME": str(self.root / "home")}
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

    def group_ids(self, output=None):
        output = self.assert_ok("ps") if output is None else output
        rows = [line.split("│") for line in output.splitlines() if line.startswith("│")]
        return {row[2].strip(): int(row[1].strip()) for row in rows if row[1].strip().isdecimal()}

    def test_table_ids_survive_removal_new_groups_and_restore(self):
        original = self.group_ids()
        self.assertEqual(set(original), {"api", "batch", "worker"})
        self.assertEqual(self.group_ids(self.assert_ok("ls")), original)
        api = self.root / "programs/api.conf"
        original_config = api.read_text()
        worker_pid = self.assert_ok("pid", "worker").strip()
        self.assert_ok("del", str(original["api"]))
        disabled = Path(str(api) + ".sp2.disabled")
        self.assertFalse(api.exists())
        self.assertEqual(disabled.read_text(), original_config)
        self.assertEqual(self.assert_ok("pid", "worker").strip(), worker_pid)
        self.assertEqual(self.group_ids(), {key: value for key, value in original.items() if key != "api"})
        self.assertNotEqual(self.run_sp2("del", str(original["api"])).returncode, 0)
        self.assert_ok("reload")
        self.assertNotIn("api", self.group_ids())
        new_config = original_config.replace("[program:api]", "[program:aardvark]")
        new_config = new_config.replace("/api.", "/aardvark.")
        (api.parent / "aardvark.conf").write_text(new_config)
        self.assert_ok("reload", "aardvark")
        added = self.group_ids()
        self.assertGreater(added["aardvark"], max(original.values()))
        disabled.rename(api)
        self.assert_ok("reload", str(original["api"]))
        self.assertEqual(self.group_ids()["api"], original["api"])

    def test_logs_id_resolves_single_process_with_a_different_process_name(self):
        ids = self.group_ids()
        config = self.root / "programs/worker.conf"
        config.write_text(config.read_text() + "numprocs=1\nprocess_name=%(program_name)s_%(process_num)02d\n")
        self.assert_ok("reload", "worker")
        target = str(ids["worker"])
        self.assertIn("stdout-before", self.assert_ok("logs", target, "--nostream"))
        self.assertIn("stderr-ready", self.assert_ok("logs", target, "--err", "--nostream"))
        preview = self.assert_ok("--dry-run", "logs", target)
        self.assertEqual(shlex.split(preview)[-4:], ["tail", "-f", "worker:worker_00", "stdout"])
        self.assertIn("stdout-before", self.assert_ok("logs", "worker", "--nostream"))

    def test_logs_id_for_process_pool_shows_every_log_and_flush_clears_only_that_pool(self):
        ids = self.group_ids()
        target = str(ids["batch"])
        output = self.assert_ok("logs", target, "--nostream")
        self.assertIn("==> batch:batch_00 <==", output)
        self.assertIn("==> batch:batch_01 <==", output)
        self.assertEqual(output.count("stdout-before"), 2)
        output = self.assert_ok("logs", target, "--err", "--nostream")
        self.assertEqual(output.count("stderr-ready"), 2)
        self.assert_ok("flush", target)
        for number in ("00", "01"):
            self.assertEqual((self.root / ("batch_" + number + ".out.log")).read_text(), "")
            self.assertEqual((self.root / ("batch_" + number + ".err.log")).read_text(), "")
        self.assertIn("stdout-before", (self.root / "api.out.log").read_text())

    def test_streaming_logs_id_merges_process_pool_and_interrupt_preserves_apps(self):
        ids = self.group_ids()
        stream = subprocess.Popen([sys.executable, str(SP2), "logs", str(ids["batch"])], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(stream.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline and output.count(b"stdout-before") < 2:
                    for key, _ in selector.select(timeout=0.1):
                        data = os.read(key.fileobj.fileno(), 4096)
                        if not data:
                            self.fail("Log stream exited before both process logs arrived: " + output.decode())
                        output.extend(data)
            self.assertGreaterEqual(output.count(b"stdout-before"), 2, output.decode())
            self.assertIn(b"[batch:batch_00]", output)
            self.assertIn(b"[batch:batch_01]", output)
            os.killpg(stream.pid, signal.SIGINT)
            stream.communicate(timeout=6)
            self.assertIn(stream.returncode, [130, -signal.SIGINT])
            self.assertEqual(self.assert_ok("status", "batch:*").count("RUNNING"), 2)
        finally:
            if stream.poll() is None:
                os.killpg(stream.pid, signal.SIGKILL)
                stream.communicate(timeout=5)

    def test_ids_work_for_status_aliases_pid_signal_and_config_update(self):
        ids = self.group_ids()
        target = str(ids["batch"])
        for command in ("status", "describe", "show", "info", "ls", "list", "ps"):
            with self.subTest(command=command):
                self.assertEqual(self.assert_ok(command, target).count("RUNNING"), 2)
        expected = [self.assert_ok("pid", name).strip() for name in ("batch:batch_00", "batch:batch_01")]
        self.assertEqual(self.assert_ok("pid", target).splitlines(), expected)
        self.assertEqual(self.assert_ok("pid", "batch:*").splitlines(), expected)
        self.assertEqual(self.assert_ok("sendSignal", "SIGCONT", target).count("signalled"), 2)
        api_pid = self.assert_ok("pid", "api").strip()
        previous = self.assert_ok("pid", "worker").strip()
        config = self.root / "programs/worker.conf"
        config.write_text(config.read_text().replace('SP2_MARKER="before"', 'SP2_MARKER="after"'))
        self.assert_ok("update", str(ids["worker"]))
        self.assertNotEqual(self.assert_ok("pid", "worker").strip(), previous)
        self.assertEqual(self.assert_ok("pid", "api").strip(), api_pid)

    def test_unknown_ids_are_rejected_consistently_before_target_operations(self):
        self.group_ids()
        original_log = (self.root / "api.out.log").read_bytes()
        for args in [("logs", "999999", "--nostream"), ("pid", "999999"),
                     ("flush", "api", "999999"), ("status", "999999"),
                     ("sendSignal", "SIGCONT", "999999"), ("reload", "999999")]:
            with self.subTest(args=args):
                result = self.run_sp2(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("未知 ID", result.stderr)
        self.assertEqual((self.root / "api.out.log").read_bytes(), original_log)

    def test_start_stop_restart_by_id_control_every_process_in_the_group(self):
        ids = self.group_ids()
        target = str(ids["batch"])
        unrelated_pid = self.assert_ok("pid", "api").strip()
        self.assert_ok("stop", target)
        stopped = self.run_sp2("status", "batch:*")
        self.assertEqual(stopped.stdout.count("STOPPED"), 2)
        self.assert_ok("start", target)
        self.assertEqual(self.assert_ok("status", "batch:*").count("RUNNING"), 2)
        names = ["batch:batch_00", "batch:batch_01"]
        previous = [self.assert_ok("pid", name).strip() for name in names]
        self.assert_ok("restart", target)
        current = [self.assert_ok("pid", name).strip() for name in names]
        self.assertEqual(len(current), 2)
        self.assertTrue(all(before != after for before, after in zip(previous, current)))
        self.assertEqual(self.assert_ok("pid", "api").strip(), unrelated_pid)

    def test_lifecycle_id_preview_and_unknown_ids_do_not_mutate_processes(self):
        ids = self.group_ids()
        registry = self.root / "home/.local/sp2/ids.json"
        original_registry = registry.read_bytes()
        original_pid = self.assert_ok("pid", "api").strip()
        preview = self.assert_ok("--dry-run", "restart", str(ids["batch"]))
        self.assertEqual(shlex.split(preview)[-2:], ["restart", "batch:*"])
        self.assertEqual(registry.read_bytes(), original_registry)
        result = self.run_sp2("stop", "api", "999999")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未知 ID", result.stderr)
        self.assertEqual(self.assert_ok("pid", "api").strip(), original_pid)
        self.assertIn("RUNNING", self.assert_ok("status", "api"))
        self.assert_ok("del", str(ids["worker"]))
        result = self.run_sp2("start", str(ids["worker"]))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("已不在 Supervisor 中", result.stderr)

    def test_lifecycle_accepts_mixed_ids_names_and_individual_process_names(self):
        ids = self.group_ids()
        self.assert_ok("stop", str(ids["batch"]), "api")
        self.assertNotEqual(self.run_sp2("status", "api").returncode, 0)
        self.assert_ok("start", "batch", str(ids["api"]))
        self.assert_ok("stop", "batch:batch_00")
        self.assertIn("RUNNING", self.assert_ok("status", "batch:batch_01"))
        self.assert_ok("start", "batch:batch_00")

    def test_soft_delete_overwrites_backup_and_removes_stopped_group_by_name(self):
        self.assert_ok("stop", "batch:*")
        source = self.root / "programs/batch.conf"
        disabled = Path(str(source) + ".sp2.disabled")
        disabled.write_text("old disabled configuration")
        expected = source.read_text()
        self.assert_ok("delete", "batch")
        self.assertEqual(disabled.read_text(), expected)
        self.assertFalse(source.exists())
        self.assertNotIn("batch", self.group_ids())
        self.assertIn("worker", self.group_ids())

    def test_delete_removes_stale_groups_whose_configs_are_gone(self):
        ids = self.group_ids()
        self.assert_ok("stop", "worker")
        other_pid = self.assert_ok("pid", "batch:batch_00").strip()
        (self.root / "programs/worker.conf").unlink()
        (self.root / "programs/api.conf").unlink()
        preview = self.assert_ok("--dry-run", "del", str(ids["worker"]), "api")
        self.assertIn("当前 include 中已无配置", preview)
        self.assertIn("update worker api", preview)
        self.assertIn("STOPPED", self.run_sp2("status", "worker").stdout)
        self.assertIn("RUNNING", self.assert_ok("status", "api"))
        self.assert_ok("del", str(ids["worker"]))
        self.assertIn("RUNNING", self.assert_ok("status", "api"))
        self.assertEqual(set(self.group_ids()), {"api", "batch"})
        self.assert_ok("del", "api")
        self.assertEqual(set(self.group_ids()), {"batch"})
        self.assertEqual(self.assert_ok("pid", "batch:batch_00").strip(), other_pid)

    def test_missing_client_definition_does_not_claim_still_configured_group_was_deleted(self):
        client = self.root / "client.conf"
        client.write_text(self.config.read_text().split("[include]")[0])
        source = self.root / "programs/worker.conf"
        pid = self.assert_ok("pid", "worker").strip()
        result = self.run_sp2("-c", str(client), "del", "worker")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("仍在 Supervisor 中注册", result.stderr)
        self.assertIn(str(client), result.stderr)
        self.assertTrue(source.exists())
        self.assertEqual(self.assert_ok("pid", "worker").strip(), pid)

    def test_delete_handles_disabled_config_and_active_config_together(self):
        ids = self.group_ids()
        source = self.root / "programs/api.conf"
        disabled = Path(str(source) + ".sp2.disabled")
        source.rename(disabled)
        expected = disabled.read_bytes()
        self.assert_ok("del", str(ids["api"]), "worker")
        self.assertEqual(disabled.read_bytes(), expected)
        self.assertTrue((source.parent / "worker.conf.sp2.disabled").is_file())
        self.assertEqual(set(self.group_ids()), {"batch"})

    def test_stale_cleanup_failure_does_not_claim_config_was_renamed(self):
        (self.root / "programs/api.conf").unlink()
        (self.root / "programs/invalid.conf").write_text("[program:invalid]\n")
        result = self.run_sp2("del", "api")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("按组更新失败", result.stderr)
        self.assertNotIn("配置已加", result.stderr)
        self.assertIn("RUNNING", self.assert_ok("status", "api"))

    def test_delete_preflights_all_targets_and_shared_config(self):
        self.group_ids()
        result = self.run_sp2("del", "api", "999999")
        self.assertNotEqual(result.returncode, 0)
        api = self.root / "programs/api.conf"
        self.assertTrue(api.exists())
        worker = self.root / "programs/worker.conf"
        api.write_text(api.read_text() + "\n" + worker.read_text())
        worker.unlink()
        result = self.run_sp2("del", "api")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("拆分", result.stderr)
        self.assertTrue(api.exists())
        self.assertIn("RUNNING", self.assert_ok("status", "api"))
        self.assert_ok("del", "api", "worker")
        self.assertFalse(api.exists())
        self.assertEqual(set(self.group_ids()), {"batch"})

    def test_soft_delete_rejects_broad_include_and_unmatched_local_socket(self):
        source = self.root / "programs/api.conf"
        original = self.config.read_text()
        self.config.write_text(original.replace("programs/*.conf", "programs/*"))
        result = self.run_sp2("del", "api")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("include", result.stderr)
        self.assertTrue(source.exists())
        self.config.write_text(original.replace("file={}/supervisor.sock".format(self.root),
                                                "file={}/wrong.sock".format(self.root)))
        result = self.run_sp2("del", "api")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unix socket", result.stderr)
        self.assertTrue(source.exists())

    def test_delete_dry_run_preserves_files_ids_and_running_processes(self):
        ids = self.group_ids()
        registry = self.root / "home/.local/sp2/ids.json"
        previous = registry.read_bytes()
        source = self.root / "programs/worker.conf"
        pid = self.assert_ok("pid", "worker").strip()
        output = self.assert_ok("--dry-run", "del", str(ids["worker"]))
        self.assertIn("mv -f --", output)
        self.assertIn(".sp2.disabled", output)
        self.assertIn("update worker", output)
        self.assertTrue(source.exists())
        self.assertEqual(registry.read_bytes(), previous)
        self.assertEqual(self.assert_ok("pid", "worker").strip(), pid)

    def test_id_file_corruption_fails_closed_and_instances_are_isolated(self):
        first = self.group_ids()
        registry = self.root / "home/.local/sp2/ids.json"
        explicit = self.assert_ok("-s", "unix://{}/supervisor.sock".format(self.root), "ls")
        self.assertEqual(self.group_ids(explicit), first)
        self.assertEqual(len(json.loads(registry.read_text())["instances"]), 1)
        other = SupervisorTests("test_connection_failure_is_not_success")
        self.addCleanup(other.doCleanups)
        other.setUp()
        other.env["HOME"] = self.env["HOME"]
        other.group_ids()
        self.assertEqual(len(json.loads(registry.read_text())["instances"]), 2)
        registry.write_text("{broken")
        result = self.run_sp2("del", "api")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ID 文件无效", result.stderr)
        self.assertEqual(registry.read_text(), "{broken")
        self.assertTrue((self.root / "programs/api.conf").exists())

    def test_default_config_lookup_matches_explicit_homebrew_style_config(self):
        prefix = self.root / "brew prefix"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "etc").mkdir()
        config = prefix / "etc/supervisord.conf"
        config.write_text(self.config.read_text())
        wrapper = prefix / "bin/supervisorctl"
        wrapper.write_text("#!/bin/sh\nif [ \"$1\" = -c ]; then exec {} \"$@\"; fi\n"
                           "exec {} -c {} \"$@\"\n".format(
                               shlex.quote(CTL), shlex.quote(CTL), shlex.quote(str(config))))
        wrapper.chmod(0o755)
        self.env["SP2_SUPERVISORCTL"] = str(wrapper)
        self.env["SP2_CONFIG"] = ""
        original = self.group_ids()
        self.env["SP2_CONFIG"] = str(config)
        self.assertEqual(self.group_ids(), original)
        registry = self.root / "home/.local/sp2/ids.json"
        self.assertEqual(len(json.loads(registry.read_text())["instances"]), 1)
        self.env["SP2_CONFIG"] = ""
        self.assert_ok("del", str(original["worker"]))
        self.assertFalse((self.root / "programs/worker.conf").exists())
        self.assertTrue((self.root / "programs/worker.conf.sp2.disabled").is_file())

    def test_memory_column_reads_live_processes_and_zero_for_stopped_group(self):
        output = self.assert_ok("ps")
        self.assertIn("memory", output)
        rows = [line.split("│") for line in output.splitlines() if line.startswith("│")]
        for row in rows:
            if row[1].strip().isdecimal():
                self.assertRegex(row[6].strip(), r"^[0-9]+\.[0-9] (KiB|MiB|GiB)$")
                self.assertGreater(float(row[6].split()[0]), 0)
        self.assert_ok("stop", "batch:*")
        result = self.run_sp2("ls")
        self.assertEqual(result.returncode, 3)
        row = next(line.split("│") for line in result.stdout.splitlines()
                   if line.startswith("│") and line.split("│")[2].strip() == "batch")
        self.assertEqual(row[6].strip(), "0 B")

    def test_sudo_delete_uses_current_users_ids_and_overwrites_backup(self):
        ids = self.group_ids()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        sudo = bin_dir / "sudo"
        sudo.write_text('#!/bin/sh\nexec "$@"\n')
        sudo.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        source = self.root / "programs/worker.conf"
        backup = Path(str(source) + ".sp2.disabled")
        backup.write_text("outdated")
        expected = source.read_text()
        self.assert_ok("--sudo", "del", str(ids["worker"]))
        self.assertFalse(source.exists())
        self.assertEqual(backup.read_text(), expected)
        self.assertNotIn("worker", self.group_ids())

    def test_delete_group_spanning_config_files(self):
        grouping = self.root / "programs/combined.conf"
        grouping.write_text("[group:combined]\nprograms=api,worker\n")
        self.assert_ok("reload")
        ids = self.group_ids()
        self.assertIn("combined", ids)
        self.assertNotIn("api", ids)
        self.assertNotIn("worker", ids)
        batch_pid = self.assert_ok("pid", "batch:batch_00").strip()
        self.assert_ok("del", str(ids["combined"]))
        for name in ("api", "worker", "combined"):
            self.assertFalse((grouping.parent / (name + ".conf")).exists())
            self.assertTrue((grouping.parent / (name + ".conf.sp2.disabled")).is_file())
        self.assertEqual(self.assert_ok("pid", "batch:batch_00").strip(), batch_pid)

    def test_numeric_group_name_and_empty_table(self):
        source = self.root / "programs/api.conf"
        source.write_text(source.read_text().replace("[program:api]", "[program:123]"))
        self.assert_ok("reload")
        self.group_ids()
        self.assert_ok("stop", "name:123")
        self.assertIn("STOPPED", self.run_sp2("status", "name:123").stdout)
        self.assert_ok("start", "name:123")
        self.assertIn("RUNNING", self.assert_ok("status", "name:123"))
        self.assertIn("stdout-before", self.assert_ok("logs", "name:123", "--nostream"))
        self.assert_ok("del", "name:123", "batch", "worker")
        self.assertEqual(self.group_ids(), {})

    def test_update_failure_leaves_latest_configuration_recoverable(self):
        self.group_ids()
        source = self.root / "programs/api.conf"
        expected = source.read_bytes()
        (source.parent / "invalid.conf").write_text("[program:invalid]\n")
        result = self.run_sp2("del", "api")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("应用失败", result.stderr)
        backup = Path(str(source) + ".sp2.disabled")
        self.assertEqual(backup.read_bytes(), expected)
        self.assertIn("RUNNING", self.assert_ok("status", "api"))

    def test_concurrent_lists_allocate_the_same_ids(self):
        commands = [subprocess.Popen([sys.executable, str(SP2), "ps"], env=self.env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(4)]
        mappings = []
        for command in commands:
            stdout, stderr = command.communicate(timeout=10)
            self.assertEqual(command.returncode, 0, stderr)
            mappings.append(self.group_ids(stdout))
        self.assertTrue(all(mapping == mappings[0] for mapping in mappings))

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
