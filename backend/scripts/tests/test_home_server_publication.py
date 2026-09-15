import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "publication_guard", Path(__file__).parents[1] / "verify_home_server_publication.py"
)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PublicationGateTest(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.ci_run = {
            "workflow_id": 42, "path": ".github/workflows/ci.yml",
            "head_repository": {"full_name": "tomerh2001/securo"},
            "head_branch": "main", "event": "push", "status": "completed",
            "conclusion": "success", "head_sha": self.sha,
        }
        self.jobs = [{"name": name, "status": "completed", "conclusion": "success"}
                     for name in guard.CHECKS]

    def test_push_and_explicit_full_dispatch_are_eligible(self):
        for event in ("push", "workflow_dispatch"):
            self.ci_run["event"] = event
            self.assertTrue(guard.eligible(self.ci_run, self.jobs, self.sha, 42))

    def test_old_head_pr_candidate_other_workflow_and_failures_cannot_publish(self):
        for key, value in [
            ("head_sha", "b" * 40), ("head_branch", "update/example"),
            ("event", "pull_request"), ("workflow_id", 43),
            ("path", ".github/workflows/other.yml"), ("conclusion", "failure"),
            ("status", "in_progress"), ("head_repository", {"full_name": "someone/securo"}),
        ]:
            with self.subTest(key=key):
                run = {**self.ci_run, key: value}
                self.assertFalse(guard.eligible(run, self.jobs, self.sha, 42))

    def test_missing_skipped_duplicated_or_pending_check_blocks_publication(self):
        for state in ("missing", "skipped", "duplicate", "in_progress"):
            jobs = copy.deepcopy(self.jobs)
            if state == "missing":
                jobs.pop()
            elif state == "duplicate":
                jobs.append(jobs[0])
            elif state == "skipped":
                jobs[0]["conclusion"] = "skipped"
            else:
                jobs[0]["status"] = state
            self.assertFalse(guard.eligible(self.ci_run, jobs, self.sha, 42))


if __name__ == "__main__":
    unittest.main()
