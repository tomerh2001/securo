"""Deploy one tested Securo publication. Host stdlib only; no bank refresh or schema rollback."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import urllib.error
import urllib.request

REPO = "tomerh2001/securo"
UPSTREAM = "securo-finance/securo"
WORKFLOW = "home-server-images.yml"
WORKFLOW_NAME = "Publish Home Server Images"
SERVICES = ("securo-backend", "securo", "securo-worker", "securo-beat")
IMAGES = {"backend": "ghcr.io/tomerh2001/securo-backend", "frontend": "ghcr.io/tomerh2001/securo-frontend"}
BUNDLE = ("home_server_update.py", "upstream_release_sync.py", "run_home_server_updates.py")
ISSUE_TITLE = "Automatic Securo deployment needs attention"
ISSUE_MARKER = "<!-- securo-home-server-update -->"
SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class UpdateConfig:
    state_dir: Path = Path("/mnt/Pool/Services/Data/securo/updater")
    stack_dir: Path = Path("/mnt/Pool/Services/Stacks/securo")
    public_url: str = "https://securo.tomerh2001.com/"
    health_timeout: int = 180
    self_update: bool = True


class UpdateError(RuntimeError):
    """Only fixed, sanitized codes cross the subprocess/API boundary."""


class Host:
    lock_fd: int | None = None

    def run(self, args: list[str], *, stdin_file: Path | None = None,
            stdout_file: Path | None = None, timeout: int = 60) -> bytes:
        try:
            with contextlib.ExitStack() as stack:
                source = stack.enter_context(stdin_file.open("rb")) if stdin_file else None
                target = stack.enter_context(stdout_file.open("wb")) if stdout_file else subprocess.PIPE
                environment = {key: value for key, value in os.environ.items()
                               if key not in {"GH_TOKEN", "GITHUB_TOKEN"}}
                result = subprocess.run(args, stdin=source, stdout=target, stderr=subprocess.PIPE,
                                        timeout=timeout, check=False,
                                        env=environment,
                                        pass_fds=() if self.lock_fd is None else (self.lock_fd,))
            if result.returncode:
                raise UpdateError("command_failed")
            return result.stdout or b""
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError("command_unavailable_or_timed_out") from exc

    def json(self, args: list[str]) -> Any:
        try:
            return json.loads(self.run(args))
        except (ValueError, TypeError) as exc:
            raise UpdateError("invalid_command_response") from exc

    def public_health(self, url: str) -> None:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Securo-home-updater/1"})
            with urllib.request.urlopen(request, timeout=15) as response:
                if response.status != 200 or response.url != url:
                    raise UpdateError("frontend_http_unhealthy")
                if b"<html" not in response.read(65536).lower():
                    raise UpdateError("frontend_html_missing")
        except (OSError, urllib.error.URLError) as exc:
            raise UpdateError("frontend_http_unhealthy") from exc


class GitHub:
    def __init__(self, token: str | None = None):
        self.token = token

    def request(self, path: str, method: str = "GET", data: dict | None = None) -> Any:
        if not path.startswith((f"/repos/{REPO}/", f"/repos/{UPSTREAM}/")):
            raise UpdateError("github_scope_rejected")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Securo-home-updater/1",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request("https://api.github.com" + path, data=body,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read(2_000_001)
                if len(content) > 2_000_000:
                    raise UpdateError("github_response_too_large")
                return json.loads(content)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise UpdateError("github_unavailable") from exc


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError) as exc:
        raise UpdateError("invalid_saved_state") from exc


def main_revision(github: GitHub) -> str:
    revision = github.request(f"/repos/{REPO}/commits/main").get("sha", "")
    if not SHA.fullmatch(revision):
        raise UpdateError("invalid_main_revision")
    return revision


def publication(github: GitHub, revision: str) -> dict | None:
    data = github.request(f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch=main&status=completed&per_page=20")
    matches = [run for run in data.get("workflow_runs", []) if
               run.get("name") == WORKFLOW_NAME and run.get("status") == "completed"
               and run.get("conclusion") == "success" and run.get("head_branch") == "main"
               and run.get("head_sha") == revision and run.get("event") == "workflow_run"
               and run.get("head_repository", {}).get("full_name") == REPO]
    if not matches:
        return None
    candidate = max(matches, key=lambda run: run["id"])
    jobs = github.request(f"/repos/{REPO}/actions/runs/{candidate['id']}/jobs?per_page=100")
    promoted = any(job.get("name") == "promote" and job.get("status") == "completed"
                   and job.get("conclusion") == "success"
                   and any(step.get("name") == "Promote the verified backend and frontend together"
                           and step.get("conclusion") == "success" for step in job.get("steps", []))
                   for job in jobs.get("jobs", []))
    return candidate if promoted else None


def runtime(host: Host) -> dict[str, dict]:
    containers = host.json(["docker", "inspect", *SERVICES])
    if not isinstance(containers, list) or len(containers) != len(SERVICES):
        raise UpdateError("unexpected_runtime")
    result = {}
    for container in containers:
        name = container.get("Name", "").removeprefix("/")
        if name not in SERVICES:
            raise UpdateError("unexpected_runtime")
        component = "frontend" if name == "securo" else "backend"
        configured = container.get("Config", {}).get("Image", "")
        if not configured.startswith((IMAGES[component] + ":", IMAGES[component] + "@")):
            raise UpdateError("unexpected_runtime_image")
        image = host.json(["docker", "image", "inspect", container["Image"]])[0]
        labels = image.get("Config", {}).get("Labels") or {}
        if labels.get("org.opencontainers.image.source") != f"https://github.com/{REPO}":
            raise UpdateError("unexpected_runtime_source")
        result[name] = {"revision": labels.get("org.opencontainers.image.revision"),
                        "image_id": container["Image"], "running": container.get("State", {}).get("Running", False),
                        "watchtower_disabled": container.get("Config", {}).get("Labels", {}).get(
                            "com.centurylinklabs.watchtower.enable") == "false"}
    return result


def compose(config: UpdateConfig, *args: str, override: Path | None = None) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(config.stack_dir),
               "-f", str(config.stack_dir / "compose.yml")]
    if override:
        command += ["-f", str(override)]
    return [*command, *args]


def pulled_images(host: Host, revision: str) -> dict[str, str] | None:
    images = {}
    for component, repository in IMAGES.items():
        info = host.json(["docker", "image", "inspect", repository + ":latest"])[0]
        labels = info.get("Config", {}).get("Labels") or {}
        if (labels.get("org.opencontainers.image.revision") != revision
                or labels.get("org.opencontainers.image.source") != f"https://github.com/{REPO}"):
            return None
        digest = next((value for value in info.get("RepoDigests", [])
                       if re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", value)), None)
        if not digest:
            raise UpdateError("image_digest_missing")
        images[component] = digest
    return images


def backup_database(host: Host, config: UpdateConfig, revision: str) -> dict:
    directory = config.state_dir / "backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, name = tempfile.mkstemp(prefix=now().replace(":", "-") + "-" + revision[:12] + "-",
                                suffix=".dump", dir=directory)
    os.close(fd)
    path = Path(name)
    os.chmod(path, 0o600)
    host.run(["docker", "exec", "securo-postgres", "sh", "-ec",
              'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'],
             stdout_file=path, timeout=300)
    with path.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise UpdateError("invalid_database_backup")
    toc = host.run(["docker", "exec", "-i", "securo-postgres", "pg_restore", "--list"],
                   stdin_file=path, timeout=120)
    if not toc.strip():
        raise UpdateError("database_backup_verification_failed")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size, "verified_at": now()}


API_HEALTH = "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).status == 200"
DB_HEALTH = """import asyncio
from sqlalchemy import text
from app.core.database import async_session_maker
async def check():
 async with async_session_maker() as session:
  assert (await session.execute(text('SELECT 1'))).scalar_one() == 1
asyncio.run(check())
"""
WORKER_PING = """import socket
from app.worker import celery_app
reply = celery_app.control.ping(destination=['celery@' + socket.gethostname()], timeout=5)
assert any(value.get('ok') == 'pong' for item in reply for value in item.values())
"""


def wait_for(check, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            check()
            return
        except UpdateError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(3, max(0, deadline - time.monotonic())))


def backend_health(host: Host) -> None:
    host.run(["docker", "exec", "securo-backend", "python", "-c", API_HEALTH], timeout=15)
    host.run(["docker", "exec", "securo-backend", "python", "-c", DB_HEALTH], timeout=15)
    if host.run(["docker", "exec", "securo-redis", "redis-cli", "ping"], timeout=15).strip() != b"PONG":
        raise UpdateError("redis_unhealthy")


def stage_bundle(host: Host, config: UpdateConfig, revision: str) -> None:
    versions = config.state_dir / "versions"
    versions.mkdir(mode=0o700, exist_ok=True)
    os.chmod(versions, 0o700)
    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=versions))
    try:
        for name in BUNDLE:
            destination = staging / name
            host.run(["docker", "cp", f"securo-backend:/app/scripts/{name}", str(destination)])
            content = destination.read_bytes()
            if not content or len(content) > 1_000_000:
                raise UpdateError("invalid_updater_bundle")
            compile(content, name, "exec")
            os.chmod(destination, 0o600)
        target = versions / revision
        if target.exists():
            if any((target / name).read_bytes() != (staging / name).read_bytes() for name in BUNDLE):
                raise UpdateError("existing_updater_bundle_conflicts")
        else:
            os.replace(staging, target)
        link = config.state_dir / ("current-next-" + str(os.getpid()))
        link.symlink_to(Path("versions") / revision)
        os.replace(link, config.state_dir / "current")
    except (OSError, SyntaxError) as exc:
        raise UpdateError("updater_bundle_install_failed") from exc
    finally:
        if staging.exists():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()


def bundle_valid(config: UpdateConfig, revision: str) -> bool:
    """Only an installed, complete version bundle can resolve an earlier failure."""
    if not config.self_update:
        return True
    current = config.state_dir / "current"
    try:
        if not current.is_symlink() or current.resolve(strict=True) != config.state_dir / "versions" / revision:
            return False
        for name in BUNDLE:
            content = (current / name).read_bytes()
            if not content or len(content) > 1_000_000:
                return False
            compile(content, name, "exec")
        return True
    except (OSError, SyntaxError):
        return False


def deployment_issue(github: GitHub, state: dict, *, resolved: bool = False) -> None:
    issue_number = state.get("issue_number")
    if not issue_number:
        issues = github.request(f"/repos/{REPO}/issues?state=open&per_page=100")
        match = next((item for item in issues if not item.get("pull_request")
                      and item.get("title") == ISSUE_TITLE and ISSUE_MARKER in (item.get("body") or "")), None)
        if match:
            issue_number = match["number"]
    if resolved:
        if issue_number:
            github.request(f"/repos/{REPO}/issues/{int(issue_number)}", "PATCH",
                           {"state": "closed", "state_reason": "completed"})
        state["issue_number"] = None
        return
    revision = state.get("target_revision") or "unknown"
    retry_note = ("This revision will not be retried automatically; a newer tested revision may proceed."
                  if state.get("rollout_attempted") else
                  "Existing services were left running. Preflight checks will retry with bounded backoff.")
    body = (f"{ISSUE_MARKER}\nAutomatic deployment stopped during `{state['phase']}` with "
            f"`{state['error_code']}`.\n\nTested revision: `{revision}`.\n\n"
            "Inspect the private host updater status and retained backup before retrying. "
            "The updater did not restore a database or downgrade a migrated schema. "
            + retry_note)
    fingerprint = hashlib.sha256(body.encode()).hexdigest()
    if issue_number and state.get("issue_fingerprint") == fingerprint:
        return
    if issue_number:
        github.request(f"/repos/{REPO}/issues/{int(issue_number)}", "PATCH",
                       {"state": "open", "title": ISSUE_TITLE, "body": body})
    else:
        created = github.request(f"/repos/{REPO}/issues", "POST", {"title": ISSUE_TITLE, "body": body})
        issue_number = created["number"]
    state["issue_number"] = issue_number
    state["issue_fingerprint"] = fingerprint


def notify_failure(github: GitHub, state: dict) -> None:
    try:
        deployment_issue(github, state)
        state.pop("notification_error", None)
    except Exception:
        state["notification_error"] = "github_issue_update_failed"


def resolve_issue(github: GitHub, state: dict) -> None:
    if not state.get("issue_number"):
        return
    try:
        deployment_issue(github, state, resolved=True)
        state.pop("notification_error", None)
        state.pop("issue_fingerprint", None)
    except Exception:
        # A GitHub outage after healthy deployment must not label that migration failed.
        state["notification_error"] = "github_issue_update_failed"


def run_update(config: UpdateConfig, github_token: str | None = None, *,
               host: Host | None = None, github: GitHub | None = None) -> dict:
    """Caller may serialize a complete coordinator/update cycle with its own outer lock."""
    host = host or Host()
    github = github or GitHub(github_token)
    config.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config.state_dir, 0o700)
    state_path = config.state_dir / "status.json"
    with (config.state_dir / "updater.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running"}
        # Child Docker clients retain this lock if the Python process is interrupted.
        host.lock_fd = lock.fileno()
        state = read_state(state_path)
        interrupted = state.get("deployment_intent")
        if interrupted and interrupted.get("state") == "active":
            target = interrupted["revision"]
            state.update(status="failed", rollout_attempted=True,
                         target_revision=target, error_code="interrupted_deployment", failed_at=now())
            state.setdefault("failed_revisions", {})[target] = {
                "phase": state.get("phase", "stop"), "error_code": "interrupted_deployment", "at": now()}
            interrupted["state"] = "interrupted"
            atomic_json(state_path, state)
            notify_failure(github, state)
            atomic_json(state_path, state)
            return state
        state.update(last_check=now(), phase="discovery", error_code=None, rollout_attempted=False)
        stopped = False
        target = None
        attempted = False
        try:
            if (config.state_dir / "pause").exists():
                state["status"] = "paused"
                return state
            retry = state.get("retry", {})
            if retry and retry.get("revision") is None and time.time() < retry["not_before"]:
                state.update(status="retry_deferred", phase=retry["phase"], error_code=retry["error_code"])
                return state
            release = github.request(f"/repos/{UPSTREAM}/releases/latest")
            state["latest_release"] = release.get("tag_name")
            target = main_revision(github)
            state.update(latest_main=target, target_revision=target)
            if target in state.get("failed_revisions", {}):
                state["status"] = "failed_revision_blocked"
                state["error_code"] = state["failed_revisions"][target]["error_code"]
                state["phase"] = state["failed_revisions"][target]["phase"]
                state["rollout_attempted"] = True
                notify_failure(github, state)
                return state
            if retry.get("revision") == target and time.time() < retry.get("not_before", 0):
                state.update(status="retry_deferred", phase=retry["phase"], error_code=retry["error_code"])
                return state
            state["rollout_attempted"] = False
            live = runtime(host)
            state["deployed_revisions"] = {name: row["revision"] for name, row in live.items()}
            if all(row["revision"] == target and row["running"] for row in live.values()):
                state["status"] = "current"
                if state.get("issue_number"):
                    backend_health(host)
                    host.public_health(config.public_url)
                    host.run(["docker", "exec", "securo-worker", "python", "-c", WORKER_PING], timeout=15)
                    if not bundle_valid(config, target):
                        raise UpdateError("current_updater_bundle_unverified")
                    resolve_issue(github, state)
                state.pop("retry", None)
                return state
            published = publication(github, target)
            if published is None:
                state["status"] = "waiting_for_publication"
                return state
            state["publication_run_id"] = published["id"]
            if not all(row["watchtower_disabled"] for row in live.values()):
                raise UpdateError("watchtower_exclusion_missing")
            state["phase"] = "pull"
            host.run(compose(config, "pull", *SERVICES), timeout=600)
            images = pulled_images(host, target)
            if images is None:
                state["status"] = "waiting_for_matching_images"
                return state
            if main_revision(github) != target:
                state["status"] = "main_changed_before_deployment"
                return state
            state["phase"] = "backup"
            state["backup"] = None
            atomic_json(state_path, state)
            state["backup"] = backup_database(host, config, target)
            atomic_json(state_path, state)
            # Recheck after the potentially slow backup, before any service is stopped.
            if main_revision(github) != target:
                state["status"] = "main_changed_before_deployment"
                return state
            override = config.state_dir / "deployment-images.json"
            atomic_json(override, {"services": {name: {"image": images["frontend" if name == "securo" else "backend"]} for name in SERVICES}})
            attempted = True
            state.update(phase="stop", status="updating", rollout_attempted=True,
                         deployment_intent={"revision": target, "state": "active", "started_at": now(),
                                            "images": images, "backup": state["backup"]})
            atomic_json(state_path, state)
            host.run(compose(config, "stop", "--timeout", "120", "securo-beat", "securo-worker"), timeout=180)
            stopped = True
            host.run(compose(config, "stop", "--timeout", "30", "securo", "securo-backend"), timeout=90)
            state["phase"] = "migration_and_backend"
            atomic_json(state_path, state)
            host.run(compose(config, "up", "-d", "--no-deps", "--pull", "never", "securo-backend", override=override), timeout=120)
            wait_for(lambda: backend_health(host), config.health_timeout)
            state["phase"] = "frontend"
            atomic_json(state_path, state)
            host.run(compose(config, "up", "-d", "--no-deps", "--pull", "never", "securo", override=override), timeout=120)
            wait_for(lambda: host.public_health(config.public_url), config.health_timeout)
            state["phase"] = "worker"
            atomic_json(state_path, state)
            host.run(compose(config, "up", "-d", "--no-deps", "--pull", "never", "securo-worker", override=override), timeout=120)
            wait_for(lambda: host.run(["docker", "exec", "securo-worker", "python", "-c", WORKER_PING], timeout=15), config.health_timeout)
            state["phase"] = "beat"
            atomic_json(state_path, state)
            host.run(compose(config, "up", "-d", "--no-deps", "--pull", "never", "securo-beat", override=override), timeout=120)
            final = runtime(host)
            if not all(row["revision"] == target and row["running"] for row in final.values()):
                raise UpdateError("runtime_revision_or_process_mismatch")
            state["phase"] = "updater_bundle"
            atomic_json(state_path, state)
            if config.self_update:
                stage_bundle(host, config, target)
            state.update(status="updated", phase="complete", deployed_revision=target,
                         deployed_revisions={name: target for name in SERVICES}, last_success=now(), error_code=None)
            state["deployment_intent"]["state"] = "complete"
            state.pop("retry", None)
            resolve_issue(github, state)
            return state
        except Exception as exc:
            code = str(exc) if isinstance(exc, UpdateError) else "unexpected_update_error"
            state.update(status="failed", error_code=code, services_stopped=stopped, failed_at=now())
            if target and attempted:
                failures = state.setdefault("failed_revisions", {})
                failures[target] = {"error_code": code, "phase": state["phase"], "at": state["failed_at"]}
                state["deployment_intent"]["state"] = "failed"
            else:
                prior = state.get("retry", {})
                attempts = prior.get("attempts", 0) + 1 if prior.get("revision") == target else 1
                state["retry"] = {"revision": target, "attempts": attempts,
                                  "not_before": time.time() + min(3600, 300 * 2 ** min(attempts - 1, 4)),
                                  "error_code": code, "phase": state["phase"]}
            atomic_json(state_path, state)
            notify_failure(github, state)
            return state
        finally:
            atomic_json(state_path, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    shared = json.loads(args.config.read_text()) if args.config else {}
    options = shared.get("updater", {})
    config = UpdateConfig(state_dir=Path(shared.get("state_dir", UpdateConfig.state_dir)),
                          stack_dir=Path(options.get("stack_dir", UpdateConfig.stack_dir)),
                          public_url=options.get("public_url", UpdateConfig.public_url),
                          health_timeout=int(options.get("health_timeout", 180)),
                          self_update=bool(options.get("self_update", True)))
    result = run_update(config, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    return 1 if result.get("status") in {"failed", "failed_revision_blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
