"""Host deployment safeguards, exercised without a daemon, network, or bank."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import fcntl
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("home_server_update", Path(__file__).parents[1] / "home_server_update.py")
assert spec is not None and spec.loader is not None
update = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = update
spec.loader.exec_module(update)

OLD = "a" * 40
NEW = "b" * 40
NEXT = "c" * 40


class FakeGitHub(update.GitHub):
    def __init__(self):
        self.main = NEW
        self.main_reads = []
        self.complete = True
        self.promote = True
        self.promote_step = True
        self.head_repository = update.REPO
        self.issues = []
        self.calls = []

    def request(self, path, method="GET", data=None):
        self.calls.append((path, method, data))
        if path.endswith("/releases/latest"):
            return {"tag_name": "v1.2.3"}
        if path.endswith("/commits/main"):
            return {"sha": self.main_reads.pop(0) if self.main_reads else self.main}
        if "/actions/workflows/" in path:
            return {"workflow_runs": [{"id": 10, "name": update.WORKFLOW_NAME,
                    "status": "completed" if self.complete else "in_progress",
                    "conclusion": "success", "head_branch": "main", "head_sha": self.main,
                    "event": "workflow_run", "head_repository": {"full_name": self.head_repository}}]}
        if "/actions/runs/10/jobs" in path:
            return {"jobs": [{"name": "promote", "status": "completed",
                    "conclusion": "success" if self.promote else "skipped",
                    "steps": [{"name": "Promote the verified backend and frontend together",
                               "conclusion": "success" if self.promote_step else "skipped"}]}]}
        if path.endswith("issues?state=open&per_page=100"):
            return [i for i in self.issues if i["state"] == "open"]
        if path.endswith("/issues") and method == "POST":
            assert data is not None
            issue = {"number": len(self.issues) + 1, "state": "open", **data}
            self.issues.append(issue)
            return issue
        if "/issues/" in path and method == "PATCH":
            assert data is not None
            number = int(path.rsplit("/", 1)[1])
            issue = next(i for i in self.issues if i["number"] == number)
            issue.update(data)
            return issue
        raise AssertionError((path, method))


class FakeHost(update.Host):
    def __init__(self):
        self.revisions = {name: OLD for name in update.SERVICES}
        self.running = {name: True for name in update.SERVICES}
        self.latest = {"backend": NEW, "frontend": NEW}
        self.disabled = True
        self.commands = []
        self.fail_backup = False
        self.invalid_backup = False
        self.fail_restore_list = False
        self.fail_health = False
        self.fail_frontend = False
        self.fail_worker = False
        self.fail_bundle = False
        self.public_checks = 0
        self.fail_pull = False
        self.interrupt_backend = False

    def json(self, args):
        self.commands.append(args)
        if args[:2] == ["docker", "inspect"]:
            return [{"Name": "/" + name, "Image": "image-" + name,
                     "Config": {"Image": update.IMAGES["frontend" if name == "securo" else "backend"] + ":latest",
                                "Labels": {"com.centurylinklabs.watchtower.enable": "false" if self.disabled else "true"}},
                     "State": {"Running": self.running[name]}} for name in update.SERVICES]
        if args[:3] == ["docker", "image", "inspect"]:
            ref = args[-1]
            if ref.startswith("image-"):
                revision = self.revisions[ref.removeprefix("image-")]
                repository = ""
            else:
                component = next(k for k, v in update.IMAGES.items() if ref == v + ":latest")
                repository = update.IMAGES[component]
                revision = self.latest[component]
            return [{"Config": {"Labels": {"org.opencontainers.image.revision": revision,
                    "org.opencontainers.image.source": "https://github.com/" + update.REPO}},
                     "RepoDigests": [repository + "@sha256:" + revision[0] * 64]}]
        raise AssertionError(args)

    def run(self, args, *, stdin_file=None, stdout_file=None, timeout=60):
        self.commands.append(args)
        if "pg_dump" in args[-1]:
            if self.fail_backup:
                raise update.UpdateError("command_failed")
            assert stdout_file is not None
            stdout_file.write_bytes(b"invalid" if self.invalid_backup else b"PGDMP-example-backup")
            return b""
        if "pg_restore" in args:
            if self.fail_restore_list:
                raise update.UpdateError("command_failed")
            return b"; verified TOC"
        if "compose" in args:
            if "pull" in args and self.fail_pull:
                raise update.UpdateError("command_failed")
            if "stop" in args:
                for name in args:
                    if name in self.running:
                        self.running[name] = False
            if "up" in args:
                name = args[-1]
                assert name in update.SERVICES
                assert "--no-deps" in args and args[args.index("--pull") + 1] == "never"
                # The temporary override binds each rollout to the already-verified paired digest.
                override = Path(args[args.index("-f", args.index("-f") + 1) + 1])
                pinned = json.loads(override.read_text())["services"][name]["image"]
                assert "@sha256:" in pinned
                self.revisions[name] = self.latest["frontend" if name == "securo" else "backend"]
                self.running[name] = True
                if name == "securo-backend" and self.interrupt_backend:
                    raise KeyboardInterrupt()
            return b""
        if args[:2] == ["docker", "cp"]:
            Path(args[-1]).write_text("invalid python !!!" if self.fail_bundle else "VALUE = 1\n")
            return b""
        if update.API_HEALTH in args and self.fail_health:
            raise update.UpdateError("command_failed")
        if update.WORKER_PING in args and self.fail_worker:
            raise update.UpdateError("command_failed")
        if "redis-cli" in args:
            return b"PONG\n"
        return b""

    def public_health(self, url):
        self.public_checks += 1
        if self.fail_frontend:
            raise update.UpdateError("frontend_http_unhealthy")


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = update.UpdateConfig(state_dir=Path(self.directory.name) / "state",
                                          stack_dir=Path(self.directory.name) / "stack", health_timeout=0)
        self.host = FakeHost()
        self.github = FakeGitHub()

    def run_update(self):
        return update.run_update(self.config, host=self.host, github=self.github)

    def stops(self):
        return [c for c in self.host.commands if "compose" in c and "stop" in c]

    def test_success_orders_backup_migration_health_workers_and_bundle(self):
        result = self.run_update()
        self.assertEqual(result["status"], "updated")
        commands = self.host.commands
        dump = next(i for i, c in enumerate(commands) if "pg_dump" in c[-1])
        verify = next(i for i, c in enumerate(commands) if "pg_restore" in c)
        stop = next(i for i, c in enumerate(commands) if "stop" in c)
        self.assertLess(dump, verify)
        self.assertLess(verify, stop)
        self.assertEqual(self.stops()[0][-2:], ["securo-beat", "securo-worker"])
        self.assertEqual(self.stops()[1][-2:], ["securo", "securo-backend"])
        ups = [c[-1] for c in commands if "compose" in c and "up" in c]
        self.assertEqual(ups, ["securo-backend", "securo", "securo-worker", "securo-beat"])
        self.assertNotIn("securo-postgres", ups)
        self.assertNotIn("securo-redis", ups)
        current = self.config.state_dir / "current"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve().name, NEW)
        self.assertEqual({p.name for p in current.iterdir()}, set(update.BUNDLE))
        self.assertEqual((self.config.state_dir / "status.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(result["backup"]["path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.github.issues, [])

    def test_same_runtime_revision_is_a_noop(self):
        self.host.revisions = {name: NEW for name in update.SERVICES}
        result = self.run_update()
        self.assertEqual(result["status"], "current")
        self.assertFalse(any("compose" in c for c in self.host.commands))

    def test_incomplete_publication_never_pulls_or_stops(self):
        self.github.complete = False
        self.assertEqual(self.run_update()["status"], "waiting_for_publication")
        self.assertFalse(any("compose" in c for c in self.host.commands))

    def test_skipped_promote_job_is_not_a_publication(self):
        self.github.promote = False
        self.assertEqual(self.run_update()["status"], "waiting_for_publication")
        self.assertEqual(self.stops(), [])

    def test_skipped_promote_step_is_not_a_publication(self):
        self.github.promote_step = False
        self.assertEqual(self.run_update()["status"], "waiting_for_publication")
        self.assertEqual(self.stops(), [])

    def test_foreign_repository_run_is_rejected(self):
        self.github.head_repository = "someone/else"
        self.assertEqual(self.run_update()["status"], "waiting_for_publication")
        self.assertEqual(self.stops(), [])

    def test_mixed_latest_images_leave_current_runtime_alone(self):
        self.host.latest["frontend"] = OLD
        self.assertEqual(self.run_update()["status"], "waiting_for_matching_images")
        self.assertEqual(self.stops(), [])
        self.assertFalse(any("pg_dump" in c[-1] for c in self.host.commands))

    def test_main_race_after_pull_prevents_backup_or_downtime(self):
        self.github.main_reads = [NEW, NEXT]
        self.assertEqual(self.run_update()["status"], "main_changed_before_deployment")
        self.assertEqual(self.stops(), [])
        self.assertFalse(any("pg_dump" in c[-1] for c in self.host.commands))

    def test_main_race_after_backup_prevents_downtime(self):
        self.github.main_reads = [NEW, NEW, NEXT]
        result = self.run_update()
        self.assertEqual(result["status"], "main_changed_before_deployment")
        self.assertIsNotNone(result["backup"])
        self.assertEqual(self.stops(), [])

    def test_backup_failure_never_stops_services_and_backs_off_before_retry(self):
        self.host.fail_backup = True
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "backup")
        self.assertIsNone(result["backup"])
        self.assertEqual(self.stops(), [])
        count = len(self.host.commands)
        self.assertEqual(self.run_update()["status"], "retry_deferred")
        self.assertFalse(any("compose" in c for c in self.host.commands[count:]))
        self.assertEqual(len(self.github.issues), 1)
        self.host.fail_backup = False
        with patch.object(update.time, "time", return_value=result["retry"]["not_before"] + 1):
            self.assertEqual(self.run_update()["status"], "updated")
        self.assertEqual(len(self.github.issues), 1)
        self.assertEqual(self.github.issues[0]["state"], "closed")

    def test_invalid_backup_and_restore_listing_failure_both_prevent_changes(self):
        for failure in ["invalid_backup", "fail_restore_list"]:
            with self.subTest(failure=failure):
                self.setUp()
                setattr(self.host, failure, True)
                self.assertEqual(self.run_update()["status"], "failed")
                self.assertEqual(self.stops(), [])

    def test_migration_failure_retains_backup_and_never_restores_or_downgrades(self):
        self.host.fail_health = True
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "migration_and_backend")
        self.assertTrue(Path(result["backup"]["path"]).exists())
        ups = [c[-1] for c in self.host.commands if "compose" in c and "up" in c]
        self.assertEqual(ups, ["securo-backend"])
        self.assertFalse(any("pg_restore" in c and "--list" not in c for c in self.host.commands))
        self.assertEqual(len(self.github.issues), 1)
        self.assertNotIn(result["backup"]["path"], self.github.issues[0]["body"])

    def test_worker_failure_keeps_beat_stopped(self):
        self.host.fail_worker = True
        self.assertEqual(self.run_update()["phase"], "worker")
        self.assertFalse(self.host.running["securo-beat"])

    def test_frontend_failure_prevents_worker_and_beat_start(self):
        self.host.fail_frontend = True
        self.assertEqual(self.run_update()["phase"], "frontend")
        self.assertFalse(self.host.running["securo-worker"])
        self.assertFalse(self.host.running["securo-beat"])

    def test_new_tested_revision_can_succeed_and_close_same_issue(self):
        self.host.fail_backup = True
        self.run_update()
        self.host.fail_backup = False
        self.github.main = NEXT
        self.host.latest = {"frontend": NEXT, "backend": NEXT}
        result = self.run_update()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(len(self.github.issues), 1)
        self.assertEqual(self.github.issues[0]["state"], "closed")

    def test_bundle_failure_never_switches_current_pointer(self):
        self.host.fail_bundle = True
        self.assertEqual(self.run_update()["phase"], "updater_bundle")
        self.assertFalse((self.config.state_dir / "current").exists())
        count = len(self.host.commands)
        self.host.fail_bundle = False
        self.assertEqual(self.run_update()["status"], "failed_revision_blocked")
        self.assertEqual(self.host.commands[count:], [])
        self.assertEqual(self.github.issues[0]["state"], "open")

    def test_temporary_pull_failure_retries_same_release_after_backoff(self):
        self.host.fail_pull = True
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.stops(), [])
        self.assertNotIn(NEW, result.get("failed_revisions", {}))
        self.host.fail_pull = False
        with patch.object(update.time, "time", return_value=result["retry"]["not_before"] + 1):
            self.assertEqual(self.run_update()["status"], "updated")

    def test_temporary_discovery_failure_does_not_block_valid_release(self):
        with patch.object(self.github, "request", side_effect=update.UpdateError("github_unavailable")):
            result = self.run_update()
        self.assertEqual(self.host.commands, [])
        self.assertEqual(result["notification_error"], "github_issue_update_failed")
        with patch.object(update.time, "time", return_value=result["retry"]["not_before"] + 1):
            self.assertEqual(self.run_update()["status"], "updated")

    def test_interrupted_rollout_preserves_intent_and_blocks_same_revision(self):
        self.host.interrupt_backend = True
        with self.assertRaises(KeyboardInterrupt):
            self.run_update()
        state_path = self.config.state_dir / "status.json"
        interrupted = json.loads(state_path.read_text())
        self.assertEqual(interrupted["deployment_intent"]["state"], "active")
        self.assertEqual(interrupted["phase"], "migration_and_backend")
        count = len(self.host.commands)
        self.host.interrupt_backend = False
        result = self.run_update()
        self.assertEqual(result["error_code"], "interrupted_deployment")
        self.assertEqual(result["backup"], interrupted["backup"])
        self.assertEqual(self.run_update()["status"], "failed_revision_blocked")
        self.assertEqual(self.host.commands[count:], [])
        self.assertEqual(len(self.github.issues), 1)

    def test_current_runtime_cannot_close_issue_with_missing_bundle(self):
        self.host.fail_pull = True
        failure = self.run_update()
        self.host.revisions = {name: NEW for name in update.SERVICES}
        with patch.object(update.time, "time", return_value=failure["retry"]["not_before"] + 1):
            result = self.run_update()
        self.assertEqual(result["error_code"], "current_updater_bundle_unverified")
        self.assertEqual(self.github.issues[0]["state"], "open")

    def test_close_issue_api_failure_does_not_turn_healthy_rollout_into_failed_migration(self):
        self.host.fail_pull = True
        failure = self.run_update()
        self.host.fail_pull = False
        original = self.github.request
        def fail_close(path, method="GET", data=None):
            if method == "PATCH" and data is not None and data.get("state") == "closed":
                raise update.UpdateError("github_unavailable")
            return original(path, method, data)
        with patch.object(self.github, "request", side_effect=fail_close), patch.object(
                update.time, "time", return_value=failure["retry"]["not_before"] + 1):
            result = self.run_update()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["deployment_intent"]["state"], "complete")
        self.assertNotIn(NEW, result.get("failed_revisions", {}))
        self.assertEqual(self.run_update()["status"], "current")
        self.assertEqual(self.github.issues[0]["state"], "closed")

    def test_pause_file_and_nonblocking_lock_prevent_any_calls(self):
        self.config.state_dir.mkdir()
        pause = self.config.state_dir / "pause"
        pause.touch()
        self.assertEqual(self.run_update()["status"], "paused")
        self.assertEqual(self.github.calls, [])
        pause.unlink()
        with (self.config.state_dir / "updater.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(update, "read_state", side_effect=AssertionError("state read before lock")):
                self.assertEqual(self.run_update()["status"], "already_running")
        self.assertEqual(self.github.calls, [])

    def test_missing_watchtower_exclusions_prevent_backup_or_stop(self):
        self.host.disabled = False
        self.assertEqual(self.run_update()["error_code"], "watchtower_exclusion_missing")
        self.assertEqual(self.stops(), [])

    def test_host_subprocess_failure_does_not_leak_stderr(self):
        host = update.Host()
        with patch.object(update.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b"secret financial data"
            with self.assertRaisesRegex(update.UpdateError, "^command_failed$"):
                host.run(["example"])

    def test_docker_child_inherits_lock_but_not_github_credentials(self):
        host = update.Host()
        host.lock_fd = 42
        with patch.object(update.subprocess, "run") as run, patch.dict(
                update.os.environ, {"GH_TOKEN": "fake-secret", "GITHUB_TOKEN": "fake-secret"}):
            run.return_value.returncode = 0
            run.return_value.stdout = b""
            host.run(["docker", "inspect", "securo"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["pass_fds"], (42,))
        self.assertNotIn("GH_TOKEN", kwargs["env"])
        self.assertNotIn("GITHUB_TOKEN", kwargs["env"])


if __name__ == "__main__":
    unittest.main()
