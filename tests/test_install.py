"""Install into a sandbox; package managers and OS detection are simulated."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="sp2-install-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        shutil.copy2(ROOT / "sp2", self.repo / "sp2")
        # Redirect only the OS release file so Ubuntu can be tested on macOS.
        release = self.root / "os-release"
        release.write_text('ID=ubuntu\n')
        (self.repo / "install.sh").write_text(
            (ROOT / "install.sh").read_text().replace("/etc/os-release", str(release))
        )
        for name in ("bash", "cat", "dirname", "mkdir", "mktemp", "install", "mv", "rm"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env = {**os.environ, "PATH": str(self.bin), "HOME": str(self.root / "home"),
                    "MOCK_OS": "Darwin", "MOCK_ROOT": str(self.root)}
        self.command("uname", 'echo "$MOCK_OS"')
        self.command("id", "echo 1000")
        self.command("sudo", 'echo "sudo $*" >> "$MOCK_ROOT/calls"\nexec "$@"')
        self.command("python3", '[ ! -f "$MOCK_ROOT/no-python" ] || exit 1\n'
                     'exec ' + shlex.quote(sys.executable) + ' "$@"')
        self.command("supervisorctl", "exit 0")
        self.command("supervisord", "exit 0")
        install_deps = '''
echo "$0 $*" >> "$MOCK_ROOT/calls"
[ ! -f "$MOCK_ROOT/fail-package" ] || exit 9
[[ "$1" == install ]] || exit 0
rm -f "$MOCK_ROOT/no-python"
printf '#!/bin/bash\\nexit 0\\n' > "$MOCK_ROOT/bin/supervisorctl"
printf '#!/bin/bash\\nexit 0\\n' > "$MOCK_ROOT/bin/supervisord"
/bin/chmod +x "$MOCK_ROOT/bin/supervisorctl" "$MOCK_ROOT/bin/supervisord"
'''
        self.command("brew", 'if [[ "$1" == --prefix ]]; then echo "$MOCK_ROOT"; exit; fi\n'
                     + install_deps)
        self.command("apt-get", install_deps)

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\nset -e\n" + body + "\n")
        path.chmod(0o755)

    def run_install(self, *args):
        return subprocess.run(["/bin/bash", str(self.repo / "install.sh"), *args],
                              cwd=self.root, env=self.env, text=True,
                              capture_output=True, timeout=15)

    def missing_deps(self):
        (self.root / "no-python").touch()
        (self.bin / "supervisorctl").unlink()
        (self.bin / "supervisord").unlink()

    def assert_installed(self, path):
        self.assertEqual(path.read_bytes(), (ROOT / "sp2").read_bytes())
        self.assertTrue(os.access(path, os.X_OK))
        result = subprocess.run([str(path), "--dry-run", "restart", "api"],
                                env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "supervisorctl restart api")

    def test_macos_default_install_and_repeat_without_package_changes(self):
        for _ in range(2):
            result = self.run_install()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed(self.root / "home/.local/bin/sp2")
        self.assertFalse((self.root / "calls").exists())
        self.assertIn("export PATH=", result.stdout)
        self.assertIn("supervisord.conf", result.stdout)

    def test_ubuntu_installs_missing_dependencies_with_sudo(self):
        self.env["MOCK_OS"] = "Linux"
        self.missing_deps()
        prefix = self.root / "custom prefix"
        result = self.run_install("--prefix", str(prefix))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_installed(prefix / "bin/sp2")
        calls = (self.root / "calls").read_text()
        self.assertIn("sudo apt-get update", calls)
        self.assertIn("sudo apt-get install -y python3 supervisor", calls)
        self.assertIn("sp2 --sudo ls", result.stdout)

    def test_macos_installs_only_missing_supervisor(self):
        (self.bin / "supervisord").unlink()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertIn("brew install supervisor", calls)
        self.assertNotIn("sudo", calls)
        self.assertNotIn("install python", calls)

    def test_skip_deps_and_package_failure_leave_destination_absent(self):
        self.missing_deps()
        result = self.run_install("--skip-deps")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "calls").exists())
        (self.root / "fail-package").touch()
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "home/.local/bin/sp2").exists())

    def test_replaces_symlink_without_overwriting_its_target(self):
        target = self.root / "original"
        target.write_text("keep this file")
        destination = self.root / "home/.local/bin/sp2"
        destination.parent.mkdir(parents=True)
        destination.symlink_to(target)
        result = self.run_install("--skip-deps")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), "keep this file")
        self.assertFalse(destination.is_symlink())
        self.assert_installed(destination)

    def test_rejects_invalid_arguments_and_unsupported_platforms(self):
        for args in [("--prefix",), ("--prefix", "relative"), ("--unknown",)]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_install(*args).returncode, 0)
        self.env["MOCK_OS"] = "FreeBSD"
        self.assertNotEqual(self.run_install().returncode, 0)
        self.env["MOCK_OS"] = "Linux"
        (self.root / "os-release").write_text("ID=debian\n")
        self.assertNotEqual(self.run_install().returncode, 0)
        self.assertFalse((self.root / "calls").exists())


if __name__ == "__main__":
    unittest.main()
