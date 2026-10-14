import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OfflineEntrypointTests(unittest.TestCase):
    def run_isolated(self, arguments):
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "scripts"
            scripts.mkdir()
            target = scripts / "verify_all.sh"
            shutil.copyfile(ROOT / "scripts/verify_all.sh", target)
            return subprocess.run(["bash", str(target), *arguments], capture_output=True,
                                  text=True, timeout=10)

    def test_missing_training_environment_fails_before_other_checks(self):
        result = self.run_isolated(["--with-training"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("Training environment missing", result.stderr)
        self.assertNotIn("No such file", result.stderr)

    def test_unknown_and_extra_arguments_fail(self):
        for arguments in (["--unknown"], ["--with-training", "--unknown"]):
            with self.subTest(arguments=arguments):
                result = self.run_isolated(arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage:", result.stderr)
