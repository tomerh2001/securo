import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

spec = importlib.util.spec_from_file_location(
    "update_orchestrator", Path(__file__).parents[1] / "run_home_server_updates.py"
)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = self.directory / "config.json"
        self.config.write_text(json.dumps({
            "state_dir": str(self.directory), "credential_command": ["fake-resolver", "get", "alias"],
        }))

    def test_failed_release_sync_still_checks_deployment_without_leaking_credentials(self):
        credential = subprocess.CompletedProcess([], 0, "private-example-token\n", "")
        responses = [
            subprocess.CompletedProcess([], 1, "", "private-example-token"),
            subprocess.CompletedProcess([], 0, "{}", ""),
        ]
        with patch.object(runner.subprocess, "run", return_value=credential), patch.object(runner, "run_child", side_effect=responses) as execute:
            self.assertEqual(runner.run(self.config), 1)
        self.assertEqual(execute.call_count, 2)
        for call in execute.call_args_list:
            self.assertEqual(call.args[1]["GH_TOKEN"], "private-example-token")
            self.assertNotIn("private-example-token", str(call.args[0]))
        saved = (self.directory / "orchestrator-status.json").read_text()
        self.assertNotIn("private-example-token", saved)
        self.assertEqual(json.loads(saved)["status"], "attention_required")

    def test_credential_failure_never_runs_either_child(self):
        with patch.object(runner.subprocess, "run", side_effect=subprocess.TimeoutExpired("resolver", 45)) as execute:
            self.assertEqual(runner.run(self.config), 1)
        self.assertEqual(execute.call_count, 1)
        status = json.loads((self.directory / "orchestrator-status.json").read_text())
        self.assertEqual(status["status"], "credential_unavailable")

    def test_timeout_terminates_entire_child_group_before_returning(self):
        process = MagicMock(pid=1234)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("child", 3600),
            subprocess.TimeoutExpired("child", 30),
            ("", ""),
        ]
        with patch.object(runner.subprocess, "Popen", return_value=process) as start, patch.object(runner.os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run_child(["child"], {}, 3600)
        self.assertTrue(start.call_args.kwargs["start_new_session"])
        self.assertEqual(kill.call_args_list[0].args, (1234, runner.signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (1234, runner.signal.SIGKILL))

    def test_timeout_cleans_remaining_group_when_parent_exits_promptly(self):
        process = MagicMock(pid=1234)
        process.communicate.side_effect = [subprocess.TimeoutExpired("child", 3600), ("", ""), ("", "")]
        with patch.object(runner.subprocess, "Popen", return_value=process), patch.object(runner.os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run_child(["child"], {}, 3600)
        self.assertEqual(kill.call_args_list[-1].args, (1234, runner.signal.SIGKILL))


if __name__ == "__main__":
    unittest.main()
