"""Local Git DAG and mocked REST state-machine tests; no network or app database."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("tested_upstream_release_sync", Path(__file__).resolve().parents[1] / "upstream_release_sync.py")
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)
BASE, RELEASE, CANDIDATE, MERGED, OTHER = (letter * 40 for letter in "abcde")


class FakeGit:
    def __init__(self):
        self.pushes = []
        self.fail = None
        self.current = False

    def fetch_main(self, sha):
        pass

    def fetch_release(self, release):
        pass

    def ancestor(self, ancestor, descendant):
        return self.current or descendant == MERGED or ancestor == descendant

    def candidate(self, base, release):
        if self.fail:
            raise sync.Blocked(self.fail, "Resolve source conflict")
        return CANDIDATE if base == BASE else OTHER

    def push(self, sha, branch):
        self.pushes.append((sha, branch))


class FakeAPI:
    def __init__(self, config):
        self.config = config
        self.base = BASE
        self.release = {"id": 1, "tag_name": "v1.0.0", "draft": False, "prerelease": False}
        self.tag = {"type": "commit", "sha": RELEASE}
        self.pulls = []
        self.runs = []
        self.issues = []
        self.writes = []
        self.protected = True
        self.protection: dict[str, Any] = {"required_status_checks": {"strict": True, "checks": [{"context": n, "app_id": 15368} for n in config.required_checks]}, "enforce_admins": {"enabled": True}}
        self.jobs = [{"name": n, "status": "completed", "conclusion": "success"} for n in config.required_checks]
        self.fail_main_dispatch = False
        self.reread_run = None
        self.reread_pr = None
        self.final_base = None
        self.main_reads = 0

    def run(self, sha=CANDIDATE, branch=None, status="completed", conclusion="success", event="workflow_dispatch", attempt=1):
        branch = branch or sync.branch_name({"tag": "v1.0.0", "release_sha": RELEASE}, BASE)
        row = {"id": len(self.runs) + 10, "workflow_id": 7, "path": ".github/workflows/ci.yml", "event": event, "head_sha": sha, "head_branch": branch,
               "repository": {"full_name": self.config.repo}, "head_repository": {"full_name": self.config.repo},
               "status": status, "conclusion": conclusion, "run_attempt": attempt}
        self.runs.append(row)
        return row

    def pages(self, path, key=None):
        if "/pulls?" in path:
            return copy.deepcopy(self.pulls)
        if "/issues?" in path:
            return copy.deepcopy(self.issues)
        if "/attempts/" in path:
            return copy.deepcopy(self.jobs)
        if "/runs?" in path:
            return copy.deepcopy(self.runs)
        raise AssertionError(path)

    def call(self, method: str, path: str, data: Any = None) -> Any:
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(data)))
        if path.endswith("/releases/latest"):
            return copy.deepcopy(self.release)
        if "/git/ref/tags/" in path:
            return {"object": copy.deepcopy(self.tag)}
        if "/git/tags/" in path:
            return {"object": {"type": "commit", "sha": RELEASE}}
        if path.endswith("/branches/main"):
            self.main_reads += 1
            base = self.final_base if self.final_base and self.main_reads > 1 else self.base
            return {"commit": {"sha": base}, "protected": self.protected}
        if path.endswith("/branches/main/protection"):
            return copy.deepcopy(self.protection)
        if path.endswith("/actions/workflows/ci.yml"):
            return {"id": 7, "path": ".github/workflows/ci.yml", "state": "active"}
        if path.endswith("/dispatches"):
            if data["ref"] == "main" and self.fail_main_dispatch:
                raise sync.Blocked("github_api", "Dispatch acknowledgement lost")
            return None
        if "/actions/runs/" in path:
            run_id = int(path.rsplit("/", 1)[1])
            return copy.deepcopy(self.reread_run or next(r for r in self.runs if r["id"] == run_id))
        if path.endswith("/pulls") and method == "POST":
            meta = json.loads(data["body"].split(sync.MARKER)[1].split(" -->")[0])
            row = {"number": len(self.pulls) + 1, "body": data["body"], "state": "open", "user": {"login": self.config.repo.split("/")[0]},
                   "head": {"ref": data["head"], "sha": meta["candidate_sha"], "repo": {"full_name": self.config.repo}},
                   "base": {"ref": "main", "repo": {"full_name": self.config.repo}}, "merged_at": None}
            self.pulls.append(row)
            return copy.deepcopy(row)
        if path.endswith("/merge"):
            row = self.pulls[int(path.split("/")[-2]) - 1]
            row.update(state="closed", merged_at="now", merge_commit_sha=MERGED)
            self.base = MERGED
            return {"merged": True, "sha": MERGED}
        if "/pulls/" in path:
            row = self.pulls[int(path.rsplit("/", 1)[1]) - 1]
            if method == "PATCH":
                row.update(data)
            return copy.deepcopy(self.reread_pr or row)
        if path.endswith("/issues") and method == "POST":
            self.issues.append({"number": len(self.issues) + 1, "state": "open", **data})
            return self.issues[-1]
        if "/issues/" in path and method == "PATCH":
            self.issues[int(path.rsplit("/", 1)[1]) - 1].update(data)
            return None
        raise AssertionError((method, path, data))


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.config = sync.SyncConfig(cache_dir=Path("/unused"))
        self.api = FakeAPI(self.config)
        self.git = FakeGit()
        self.state = {}
        self.coordinator = sync.Coordinator(self.config, self.api, self.git, self.state, lambda: None)

    def prepare(self):
        self.assertEqual(self.coordinator.run()["status"], "waiting_for_ci")
        return self.api.pulls[0]

    def merge_calls(self):
        return [w for w in self.api.writes if w[1].endswith("/merge")]

    def dispatches(self, ref=None):
        return [w for w in self.api.writes if w[1].endswith("/dispatches") and (ref is None or w[2]["ref"] == ref)]

    def test_already_current_records_release_without_mutations(self):
        self.git.current = True
        self.assertEqual(self.coordinator.run()["status"], "current")
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.state["releases"]["1"]["release_sha"], RELEASE)

    def test_poll_and_queued_run_do_not_repeat_dispatch(self):
        self.prepare()
        self.coordinator.run()
        self.api.run(status="queued", conclusion=None)
        self.coordinator.run()
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(len(self.git.pushes), 1)
        self.assertEqual(len(self.api.pulls), 1)

    def test_success_merges_once_and_dispatches_main_once(self):
        self.prepare()
        self.api.run()
        self.assertEqual(self.coordinator.run()["status"], "merged")
        self.assertEqual(self.merge_calls()[0][2], {"sha": CANDIDATE, "merge_method": "merge"})
        self.coordinator.run()
        self.assertEqual(len(self.merge_calls()), 1)
        self.assertEqual(len(self.dispatches("main")), 1)

    def test_failed_run_is_reported_once_and_not_redispatched(self):
        self.prepare()
        self.api.run(conclusion="failure")
        self.assertEqual(self.coordinator.run()["reason"], "ci_failed")
        self.coordinator.run()
        self.assertEqual(len(self.api.issues), 1)
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(self.merge_calls(), [])

    def test_latest_failed_run_overrides_old_success(self):
        self.prepare()
        self.api.run()
        self.api.run(conclusion="failure")
        self.assertEqual(self.coordinator.run()["reason"], "ci_failed")
        self.assertEqual(self.merge_calls(), [])

    def test_attempt_changed_after_list_blocks_merge(self):
        self.prepare()
        run = self.api.run()
        self.api.reread_run = {**run, "run_attempt": 2, "status": "queued", "conclusion": None}
        self.assertEqual(self.coordinator.run()["status"], "ci_changed")
        self.assertEqual(self.merge_calls(), [])

    def test_skipped_missing_and_duplicate_named_jobs_block(self):
        for alteration in ("skip", "missing", "duplicate"):
            with self.subTest(alteration=alteration):
                self.setUp()
                self.prepare()
                self.api.run()
                if alteration == "skip":
                    self.api.jobs[0]["conclusion"] = "skipped"
                elif alteration == "missing":
                    self.api.jobs.pop()
                else:
                    self.api.jobs.append(copy.deepcopy(self.api.jobs[0]))
                self.assertEqual(self.coordinator.run()["reason"], "ci_incomplete")
                self.assertEqual(self.merge_calls(), [])

    def test_spoofed_runs_do_not_suppress_full_dispatch(self):
        for key, value in (("workflow_id", 9), ("path", ".github/workflows/spoof.yml"), ("head_sha", OTHER), ("event", "pull_request"), ("head_repository", {"full_name": "attacker/fork"}), ("repository", {"full_name": "attacker/fork"})):
            with self.subTest(key=key):
                self.setUp()
                self.api.run()[key] = value
                self.prepare()
                self.assertEqual(len(self.dispatches()), 1)
                self.assertEqual(self.merge_calls(), [])

    def test_existing_full_run_avoids_duplicate_dispatch(self):
        self.api.run(status="queued", conclusion=None)
        self.prepare()
        self.assertEqual(len(self.dispatches()), 0)

    def test_pr_identity_and_head_modifications_are_rejected(self):
        for changed in ("sha", "repository", "owner", "body"):
            with self.subTest(changed=changed):
                self.setUp()
                pr = self.prepare()
                if changed == "sha":
                    pr["head"]["sha"] = OTHER
                elif changed == "repository":
                    pr["head"]["repo"]["full_name"] = "attacker/fork"
                elif changed == "owner":
                    pr["user"]["login"] = "attacker"
                else:
                    pr["body"] = "unrelated"
                self.assertEqual(self.coordinator.run()["reason"], "candidate_changed")
                self.assertEqual(self.merge_calls(), [])

    def test_malformed_marker_without_release_id_is_ignored(self):
        pr = self.prepare()
        value = sync.metadata(pr, self.config)
        value.pop("release_id")
        pr["body"] = "<!-- " + sync.MARKER + json.dumps(value) + " -->"
        self.assertIsNone(sync.metadata(pr, self.config))
        self.assertEqual(self.coordinator.run()["reason"], "candidate_changed")

    def test_fresh_head_change_cannot_merge(self):
        pr = self.prepare()
        self.api.run()
        self.api.reread_pr = copy.deepcopy(pr)
        self.api.reread_pr["head"]["sha"] = OTHER
        self.assertEqual(self.coordinator.run()["status"], "candidate_changed")
        self.assertEqual(self.merge_calls(), [])

    def test_base_race_cannot_merge(self):
        self.prepare()
        self.api.run()
        self.api.main_reads = 0
        self.api.final_base = OTHER
        self.assertEqual(self.coordinator.run()["status"], "base_changed")
        self.assertEqual(self.merge_calls(), [])

    def test_advanced_base_creates_new_candidate_and_closes_stale(self):
        self.prepare()
        self.api.base = OTHER
        self.assertEqual(self.coordinator.run()["status"], "waiting_for_ci")
        self.assertEqual(len(self.api.pulls), 2)
        self.assertEqual(self.api.pulls[0]["state"], "closed")
        self.assertNotEqual(self.git.pushes[0][1], self.git.pushes[1][1])

    def test_manually_closed_candidate_is_not_reopened(self):
        self.prepare()["state"] = "closed"
        self.assertEqual(self.coordinator.run()["status"], "candidate_closed")
        self.assertEqual(len(self.api.pulls), 1)
        self.assertEqual(len(self.dispatches()), 1)

    def test_moved_release_tag_is_blocked_even_when_name_stays(self):
        self.prepare()
        self.api.tag["sha"] = OTHER
        self.assertEqual(self.coordinator.run()["reason"], "release_changed")
        self.assertEqual(len(self.git.pushes), 1)

    def test_conflicts_never_push_and_issue_is_deduplicated(self):
        self.git.fail = "merge_conflict"
        self.assertEqual(self.coordinator.run()["reason"], "merge_conflict")
        self.coordinator.run()
        self.assertEqual(self.git.pushes, [])
        self.assertEqual(len(self.api.issues), 1)

    def test_issue_reporting_failure_keeps_original_blocked_reason(self):
        self.git.fail = "merge_conflict"
        pages = self.api.pages
        def without_issues(path, key=None):
            if "/issues?" in path:
                raise sync.Blocked("github_api", "Issues are disabled")
            return pages(path, key)
        with patch.object(self.api, "pages", side_effect=without_issues):
            result = self.coordinator.run()
        self.assertEqual(result["reason"], "merge_conflict")
        self.assertIn("Issues are disabled", result["issue_reporting_error"])
        self.assertEqual(self.git.pushes, [])

    def test_missing_strict_admin_or_app_protection_blocks_merge(self):
        for changed in ("protected", "strict", "admins", "app", "missing"):
            with self.subTest(changed=changed):
                self.setUp()
                self.prepare()
                self.api.run()
                if changed == "protected":
                    self.api.protected = False
                elif changed == "strict":
                    self.api.protection["required_status_checks"]["strict"] = False
                elif changed == "admins":
                    self.api.protection["enforce_admins"]["enabled"] = False
                elif changed == "app":
                    self.api.protection["required_status_checks"]["checks"][0]["app_id"] = None
                else:
                    self.api.protection["required_status_checks"]["checks"].pop()
                self.assertEqual(self.coordinator.run()["reason"], "branch_protection")
                self.assertEqual(self.merge_calls(), [])

    def test_merge_recovers_lost_main_dispatch_without_remerging(self):
        self.prepare()
        self.api.run()
        self.api.fail_main_dispatch = True
        self.assertEqual(self.coordinator.run()["reason"], "github_api")
        self.assertEqual(self.state["pending_main"], MERGED)
        self.api.fail_main_dispatch = False
        self.state["dispatches"]["main:" + MERGED]["time"] = 0
        self.assertEqual(self.coordinator.run()["status"], "current")
        self.assertEqual(len(self.merge_calls()), 1)
        self.assertEqual(len(self.dispatches("main")), 2)  # first failed acknowledgement, one recovery
        self.coordinator.run()
        self.assertEqual(len(self.dispatches("main")), 2)

    def test_recovery_of_merged_pr_survives_newer_release_and_lost_local_marker(self):
        self.prepare()
        self.api.run()
        self.coordinator.run()
        self.state.pop("pending_main", None)
        self.state["dispatches"].pop("main:" + MERGED)
        self.api.release.update(id=2, tag_name="v1.1.0")
        self.coordinator.run()
        self.assertEqual(len(self.dispatches("main")), 2)
        self.assertEqual(len(self.merge_calls()), 1)

    def test_existing_main_push_ci_suppresses_recovery_dispatch(self):
        self.prepare()
        self.api.run()
        self.coordinator.run()
        self.state["dispatches"].pop("main:" + MERGED)
        self.api.run(sha=MERGED, branch="main", event="push", status="queued", conclusion=None)
        self.coordinator.run()
        self.assertEqual(len(self.dispatches("main")), 1)

    def test_lightweight_and_annotated_stable_tags(self):
        light = sync.stable_release(self.api, self.config)
        self.assertEqual(light["release_sha"], RELEASE)
        self.api.tag = {"type": "tag", "sha": OTHER}
        annotated = sync.stable_release(self.api, self.config)
        self.assertEqual(annotated["release_sha"], RELEASE)
        self.assertEqual(annotated["tag_object_sha"], OTHER)

    def test_unstable_and_malformed_release_is_rejected(self):
        for change in ({"draft": True}, {"prerelease": True}, {"tag_name": "main"}, {"tag_name": "v1.2.3;echo bad"}):
            with self.subTest(change=change):
                self.api.release.update(change)
                with self.assertRaises(sync.Blocked):
                    sync.stable_release(self.api, self.config)
                self.api.release = {"id": 1, "tag_name": "v1.0.0", "draft": False, "prerelease": False}


class HostInterfaceTests(unittest.TestCase):
    def test_shared_config_and_private_status_persistence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.json"
            path.write_text(json.dumps({"state_dir": str(root / "state"), "upstream_sync": {"dispatch_discovery_seconds": 300}}))
            config = sync.config_from_file(path)
            self.assertEqual(config.cache_dir, root / "state" / "upstream-sync")
            self.assertEqual(config.dispatch_discovery_seconds, 300)
            self.assertIn("Update Automation Tests", config.required_checks)
            api, git = FakeAPI(config), FakeGit()
            git.current = True
            with patch.object(sync, "GitHub", return_value=api), patch.object(sync, "GitCache", return_value=git):
                result = sync.run_sync(config, "private-token")
            saved_path = config.cache_dir / "coordinator-state.json"
            saved = json.loads(saved_path.read_text())
            self.assertEqual(saved["last_result"], result)
            self.assertRegex(saved["checked_at"], r"^\d{4}-\d{2}-\d{2}T")
            self.assertEqual(saved_path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private-token", saved_path.read_text())
            self.assertEqual(api.writes, [])

    def test_read_only_mode_has_no_remote_mutations_or_dispatch_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = sync.SyncConfig(cache_dir=Path(temporary) / "cache")
            api, git = FakeAPI(config), FakeGit()
            with patch.object(sync, "GitHub", return_value=api), patch.object(sync, "GitCache", return_value=git):
                result = sync.run_sync(config, "private-token", read_only=True)
            self.assertEqual(result["status"], "candidate_preview")
            self.assertEqual(api.writes, [])
            self.assertEqual(git.pushes, [])
            self.assertFalse((config.cache_dir / "coordinator-state.json").exists())

    def test_api_failure_is_recorded_even_when_issue_reporting_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = sync.SyncConfig(cache_dir=Path(temporary))
            api, git = FakeAPI(config), FakeGit()
            with patch.object(api, "call", side_effect=sync.Blocked("github_api", "API unavailable")), patch.object(sync, "GitHub", return_value=api), patch.object(sync, "GitCache", return_value=git):
                result = sync.run_sync(config, "private-token")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(json.loads((config.cache_dir / "coordinator-state.json").read_text())["last_result"]["reason"], "github_api")


class RealGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "upstream"
        self.repo.mkdir()
        self.command("init", "-b", "main")
        self.command("config", "user.name", "Test")
        self.command("config", "user.email", "test@example.invalid")
        self.write("base.txt", "base\n")
        self.base = self.commit("base")
        self.command("branch", "fork")
        self.write("official.txt", "official release\n")
        self.release = self.commit("official")
        self.command("checkout", "fork")
        self.write("custom.txt", "custom account UI\n")
        self.fork = self.commit("custom")
        self.cache = sync.GitCache(sync.SyncConfig(cache_dir=self.root / "cache"), "not-a-real-token")
        self.cache.run("fetch", str(self.repo), "+refs/heads/*:refs/heads/*")

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True, capture_output=True, check=True).stdout.strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message):
        self.command("add", ".")
        self.command("commit", "-m", message)
        return self.command("rev-parse", "HEAD")

    def release_data(self):
        return {"tag": "v1.0.0", "release_sha": self.release}

    def test_real_three_way_merge_preserves_both_histories_and_is_deterministic(self):
        first = self.cache.candidate(self.fork, self.release_data())
        self.assertEqual(first, self.cache.candidate(self.fork, self.release_data()))
        parents = self.cache.run("show", "-s", "--format=%P", first).stdout.split()
        self.assertEqual(parents, [self.fork, self.release])
        self.assertTrue(self.cache.ancestor(self.fork, first))
        self.assertTrue(self.cache.ancestor(self.release, first))
        self.assertEqual(self.cache.run("show", first + ":custom.txt").stdout, "custom account UI\n")
        self.assertEqual(self.cache.run("show", first + ":official.txt").stdout, "official release\n")

    def test_real_merge_conflict_stops_without_candidate(self):
        self.write("base.txt", "fork version\n")
        fork = self.commit("fork conflict")
        self.command("checkout", "main")
        self.write("base.txt", "upstream version\n")
        self.release = self.commit("upstream conflict")
        self.cache.run("fetch", str(self.repo), "+refs/heads/*:refs/heads/*")
        with self.assertRaises(sync.Blocked) as error:
            self.cache.candidate(fork, self.release_data())
        self.assertEqual(error.exception.kind, "merge_conflict")

    def test_upstream_workflow_change_is_preserved_with_host_credentials(self):
        self.command("checkout", "main")
        self.write(".github/workflows/ci.yml", "name: New upstream workflow\n")
        self.release = self.commit("workflow")
        self.cache.run("fetch", str(self.repo), "+refs/heads/*:refs/heads/*")
        candidate = self.cache.candidate(self.fork, self.release_data())
        self.assertEqual(self.cache.run("show", candidate + ":.github/workflows/ci.yml").stdout, "name: New upstream workflow\n")

    def test_upstream_collision_with_coordinator_is_not_silently_accepted(self):
        self.command("checkout", "main")
        self.write("backend/scripts/upstream_release_sync.py", "unrelated coordinator\n")
        self.release = self.commit("coordinator collision")
        self.cache.run("fetch", str(self.repo), "+refs/heads/*:refs/heads/*")
        with self.assertRaises(sync.Blocked) as error:
            self.cache.candidate(self.fork, self.release_data())
        self.assertEqual(error.exception.kind, "coordinator_changed")

    def test_git_network_auth_does_not_enter_argv_or_local_operations(self):
        with patch.object(sync.subprocess, "run") as call:
            call.return_value = subprocess.CompletedProcess([], 0, "", "")
            with patch.dict(os.environ, {"GH_TOKEN": "parent-secret", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_VALUE_0": "old-secret"}):
                self.cache.run("show", self.fork)
                local = call.call_args
                self.assertNotIn("GH_TOKEN", local.kwargs["env"])
                self.assertNotIn("GIT_CONFIG_COUNT", local.kwargs["env"])
                self.assertEqual(local.kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull)
                self.cache.run("fetch", "https://github.com/example/repo.git", network=True)
                network = call.call_args
                self.assertNotIn("not-a-real-token", str(network.args))
                self.assertEqual(network.kwargs["env"]["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
                self.assertFalse(self.cache.path.joinpath("config").read_text().find("not-a-real-token") >= 0)


if __name__ == "__main__":
    unittest.main()
