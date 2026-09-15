#!/usr/bin/env python3
"""Integrate official stable releases without executing candidate code on the host.

The host supplies a token in memory and serializes this coordinator with deploys.
Only a real three-way merge passing full CI may merge through GitHub's protected
PR API. The private cache contains Git objects and non-secret recovery state.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
MARKER = "securo-upstream-sync:"
DEFAULT_CHECKS = ("Backend Tests", "Frontend Checks", "Migration Chain", "Helm Checks", "Update Automation Tests")
TRUST_FILES = (
    "backend/scripts/upstream_release_sync.py",
    "backend/scripts/tests/test_upstream_release_sync.py",
    "backend/scripts/home_server_update.py",
    "backend/scripts/run_home_server_updates.py",
    "backend/scripts/tests/test_home_server_update.py",
    "backend/scripts/tests/test_home_server_orchestrator.py",
    "backend/scripts/verify_home_server_publication.py",
    "backend/scripts/tests/test_home_server_publication.py",
)


@dataclass(frozen=True)
class SyncConfig:
    cache_dir: Path
    repo: str = "tomerh2001/securo"
    upstream: str = "securo-finance/securo"
    required_checks: tuple[str, ...] = DEFAULT_CHECKS
    workflow: str = "ci.yml"
    dispatch_discovery_seconds: int = 600


class Blocked(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, path: str, data: Any = None) -> Any:
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(
            "https://api.github.com" + path, data=body, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json", "User-Agent": "securo-upstream-sync"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            # Never include response bodies, request headers, or the credential.
            raise Blocked("github_api", f"GitHub {method} {path} returned HTTP {exc.code}") from None

    def pages(self, path: str, key: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            payload = self.call("GET", f"{path}{separator}per_page=100&page={page}")
            batch = payload[key] if key else payload
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise Blocked("pagination", "GitHub result exceeds the bounded 10,000-row audit window")


class GitCache:
    def __init__(self, config: SyncConfig, token: str):
        self.path = config.cache_dir / "repository.git"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self.token = token
        self.config = config
        if not self.path.exists():
            subprocess.run(["git", "init", "--bare", str(self.path)], check=True, capture_output=True)

    def run(self, *args: str, data: str | None = None, network: bool = False,
            extra_env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        # Do not pass the host's credential to Git operations that need no network.
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_TRACE", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE"):
            env.pop(key, None)
        for key in list(env):
            if key.startswith("GIT_CONFIG_"):
                env.pop(key)
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        if network:
            encoded = base64.b64encode(("x-access-token:" + self.token).encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + encoded})
        env.update(extra_env or {})
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "--git-dir", str(self.path), *args],
                                input=data, text=True, capture_output=True, env=env, timeout=90)
        if check and result.returncode:
            raise Blocked("git", f"Git {args[0]} failed (exit {result.returncode}); inspect repository access or merge inputs")
        return result

    def fetch_main(self, sha: str) -> None:
        self.run("fetch", "--no-tags", f"https://github.com/{self.config.repo}.git",
                 "+refs/heads/main:refs/upstream-sync/main", network=True)
        if self.run("rev-parse", "refs/upstream-sync/main").stdout.strip() != sha:
            raise Blocked("base_changed", "Fork main advanced during fetch; the next poll will use its new base")

    def fetch_release(self, release: dict[str, Any]) -> None:
        ref = "refs/upstream-sync/releases/" + release["tag_object_sha"]
        self.run("fetch", "--no-tags", f"https://github.com/{self.config.upstream}.git",
                 f"+refs/tags/{release['tag']}:{ref}", network=True)
        actual = self.run("rev-parse", ref).stdout.strip()
        peeled = self.run("rev-parse", ref + "^{commit}").stdout.strip()
        if (actual, peeled) != (release["tag_object_sha"], release["release_sha"]):
            raise Blocked("release_changed", "The official release tag changed between API inspection and Git fetch")

    def ancestor(self, ancestor: str, descendant: str) -> bool:
        return self.run("merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0

    def candidate(self, base: str, release: dict[str, Any]) -> str:
        merged = self.run("merge-tree", "--write-tree", base, release["release_sha"], check=False)
        if merged.returncode:
            raise Blocked("merge_conflict", "Official release conflicts with fork changes. Create a branch from current fork main, merge the official release tag, resolve conflicts, and open a reviewed PR; do not reset the fork or use an ours merge.")
        tree = merged.stdout.splitlines()[0]
        changed = self.run("diff-tree", "--no-commit-id", "--name-only", "-r", base, tree).stdout.splitlines()
        if set(changed) & set(TRUST_FILES):
            raise Blocked("coordinator_changed", "The release changes the trusted host coordinator or its tests; review that collision explicitly")
        stamp = max(int(self.run("show", "-s", "--format=%ct", sha).stdout) for sha in (base, release["release_sha"]))
        env = {"GIT_AUTHOR_NAME": "Securo Upstream Sync", "GIT_COMMITTER_NAME": "Securo Upstream Sync",
               "GIT_AUTHOR_EMAIL": "upstream-sync@users.noreply.github.com", "GIT_COMMITTER_EMAIL": "upstream-sync@users.noreply.github.com",
               "GIT_AUTHOR_DATE": f"{stamp} +0000", "GIT_COMMITTER_DATE": f"{stamp} +0000"}
        return self.run("commit-tree", tree, "-p", base, "-p", release["release_sha"],
                        data=f"Merge official Securo {release['tag']} into customized fork\n", extra_env=env).stdout.strip()

    def push(self, sha: str, branch: str) -> None:
        self.run("push", f"https://github.com/{self.config.repo}.git", f"{sha}:refs/heads/{branch}", network=True)


def stable_release(api: GitHub, config: SyncConfig) -> dict[str, Any]:
    release = api.call("GET", f"/repos/{config.upstream}/releases/latest")
    tag = release.get("tag_name", "")
    if type(release.get("id")) is not int or release["id"] <= 0 or release.get("draft") or release.get("prerelease") or not TAG.fullmatch(tag):
        raise Blocked("invalid_release", "Latest official release is not a published vX.Y.Z stable release")
    ref = api.call("GET", f"/repos/{config.upstream}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")["object"]
    tag_object_sha = ref["sha"]
    for _ in range(8):
        if ref["type"] == "commit":
            break
        if ref["type"] != "tag":
            raise Blocked("invalid_release", "Release tag does not point to a commit")
        ref = api.call("GET", f"/repos/{config.upstream}/git/tags/{ref['sha']}")["object"]
    else:
        raise Blocked("invalid_release", "Release tag nesting exceeds the safety bound")
    if not SHA.fullmatch(tag_object_sha) or not SHA.fullmatch(ref["sha"]):
        raise Blocked("invalid_release", "Release contains a malformed Git object ID")
    return {"release_id": release["id"], "tag": tag, "tag_object_sha": tag_object_sha, "release_sha": ref["sha"]}


def branch_name(release: dict[str, Any], base: str) -> str:
    return f"sync/upstream-{release['tag']}-{release['release_sha'][:12]}-{base[:12]}"


def metadata(pr: dict[str, Any], config: SyncConfig) -> dict[str, Any] | None:
    match = re.search(r"<!-- " + MARKER + r"(\{[^\n]+\}) -->", pr.get("body") or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
        valid = (set(value) == {"schema", "release_id", "tag", "tag_object_sha", "release_sha", "base_sha", "candidate_sha"}
                 and type(value.get("release_id")) is int and value["release_id"] > 0
                 and value.get("schema") == 1 and TAG.fullmatch(value["tag"])
                 and all(SHA.fullmatch(value[k]) for k in ("tag_object_sha", "release_sha", "base_sha", "candidate_sha"))
                 and pr["head"]["repo"]["full_name"] == config.repo == pr["base"]["repo"]["full_name"]
                 and pr["base"]["ref"] == "main"
                 and pr["head"]["ref"] == branch_name(value, value["base_sha"])
                 and pr["user"]["login"] in (config.repo.split("/")[0], "github-actions[bot]"))
        return value if valid else None
    except (ValueError, KeyError, TypeError):
        return None


def full_ci_run(run: dict[str, Any], workflow_id: int, config: SyncConfig, sha: str, branch: str, main: bool) -> bool:
    return bool(run.get("workflow_id") == workflow_id
                and run.get("path") == ".github/workflows/" + config.workflow
                and run.get("head_sha") == sha
                and run.get("head_branch") == branch
                and run.get("repository", {}).get("full_name") == config.repo
                and run.get("head_repository", {}).get("full_name") == config.repo
                and run.get("event") in (("push", "workflow_dispatch") if main else ("workflow_dispatch",)))


def successful_jobs(jobs: list[dict[str, Any]], required: tuple[str, ...]) -> bool:
    return bool(required) and all(
        len(matches := [j for j in jobs if j.get("name") == name]) == 1
        and matches[0].get("status") == "completed" and matches[0].get("conclusion") == "success"
        for name in required
    )


class Coordinator:
    def __init__(self, config: SyncConfig, api: GitHub, git: GitCache, state: dict[str, Any], save: Any):
        self.config, self.api, self.git, self.state, self.save = config, api, git, state, save
        self.prefix = f"/repos/{config.repo}"

    def main_branch(self) -> dict[str, Any]:
        return self.api.call("GET", self.prefix + "/branches/main")

    def issue(self, kind: str, release: dict[str, Any] | None, message: str) -> None:
        identity = str((release or {}).get("release_id", "setup")) + ":" + kind
        marker = "<!-- securo-upstream-problem:" + identity + " -->"
        body = f"{message}\n\nOfficial release: {(release or {}).get('tag', 'not yet resolved')}\n\nThe customized deployment is unchanged. Resolve the condition and rerun the host coordinator.\n\n{marker}"
        issues = self.api.pages(self.prefix + "/issues?state=all")
        found = next((i for i in issues if marker in (i.get("body") or "") and "pull_request" not in i), None)
        if found:
            if found.get("state") == "open" and found.get("body") != body:
                self.api.call("PATCH", self.prefix + f"/issues/{found['number']}", {"body": body})
        else:
            self.api.call("POST", self.prefix + "/issues", {"title": f"Securo upstream update needs attention: {kind}", "body": body})

    def ensure_ci(self, sha: str, branch: str, *, main: bool = False) -> dict[str, Any] | None:
        workflow = self.api.call("GET", self.prefix + f"/actions/workflows/{self.config.workflow}")
        if workflow.get("path") != ".github/workflows/" + self.config.workflow or workflow.get("state") != "active":
            raise Blocked("ci_configuration", "The configured CI workflow must be active at the expected path")
        runs = self.api.pages(self.prefix + f"/actions/workflows/{workflow['id']}/runs?branch={urllib.parse.quote(branch, safe='')}&head_sha={sha}", "workflow_runs")
        suitable = [r for r in runs if full_ci_run(r, workflow["id"], self.config, sha, branch, main)]
        key = branch + ":" + sha
        if suitable:
            run = max(suitable, key=lambda r: (r["id"], r.get("run_attempt", 1)))
            self.state.setdefault("dispatches", {})[key] = {"status": "observed", "run_id": run["id"]}
            self.save()
            return run
        previous = self.state.setdefault("dispatches", {}).get(key)
        if previous and previous.get("status") == "observed":
            raise Blocked("ci_missing", "Previously observed exact-head CI disappeared; review its deletion before redispatch")
        if previous and time.time() - previous.get("time", 0) < self.config.dispatch_discovery_seconds:
            return None
        self.state["dispatches"][key] = {"status": "intent", "time": time.time()}
        self.save()
        self.api.call("POST", self.prefix + f"/actions/workflows/{self.config.workflow}/dispatches", {"ref": branch})
        self.state["dispatches"][key]["status"] = "sent"
        self.save()
        return None

    def run(self) -> dict[str, Any]:
        release: dict[str, Any] | None = None
        try:
            release = stable_release(self.api, self.config)
            seen = self.state.setdefault("releases", {})
            previous = seen.get(str(release["release_id"]))
            if previous is not None and previous != release:
                raise Blocked("release_changed", "An already observed official release tag was replaced. Verify the publisher's change before approving the new object")
            seen[str(release["release_id"])] = release
            self.save()
            main = self.main_branch()
            base = main["commit"]["sha"]
            self.git.fetch_main(base)
            self.git.fetch_release(release)
            pulls = self.api.pages(self.prefix + "/pulls?state=all")
            managed = [(p, m) for p in pulls if (m := metadata(p, self.config))]
            # Reconcile a successful merge before the already-current fast path.
            merged = [p for p, _ in managed if p.get("merged_at") and SHA.fullmatch(p.get("merge_commit_sha") or "") and self.git.ancestor(p["merge_commit_sha"], base)]
            if self.state.get("pending_main") or merged:
                run = self.ensure_ci(base, "main", main=True)
                if run is not None:
                    self.state.pop("pending_main", None)
                    self.save()
            if self.git.ancestor(release["release_sha"], base):
                return {"status": "current", "release": release["tag"], "main_sha": base}
            candidate = self.git.candidate(base, release)
            branch = branch_name(release, base)
            expected = {"schema": 1, **release, "base_sha": base, "candidate_sha": candidate}
            matching = [p for p in pulls if p.get("head", {}).get("ref") == branch]
            if matching:
                pr = matching[0]
                if metadata(pr, self.config) != expected or pr["head"]["sha"] != candidate:
                    raise Blocked("candidate_changed", "The reserved candidate PR or its exact merge commit was modified; no branch will be overwritten")
                if pr["state"] != "open":
                    return {"status": "candidate_closed", "pr": pr["number"]}
            else:
                self.git.push(candidate, branch)
                body = (f"Merge official Securo {release['tag']} into the customized fork.\n\n"
                        f"Source: https://github.com/{self.config.upstream}/releases/tag/{release['tag']}\n\n"
                        "Full candidate CI and unchanged main/release identities are required before merge.\n\n"
                        f"<!-- {MARKER}{json.dumps(expected, sort_keys=True, separators=(',', ':'))} -->")
                pr = self.api.call("POST", self.prefix + "/pulls", {"title": f"Merge official Securo {release['tag']}", "head": branch, "base": "main", "body": body})
            for stale, info in managed:
                if stale["state"] == "open" and stale["number"] != pr["number"] and info["release_id"] == release["release_id"] and info["base_sha"] != base and stale["head"]["sha"] == info["candidate_sha"]:
                    self.api.call("PATCH", self.prefix + f"/pulls/{stale['number']}", {"state": "closed"})
            run = self.ensure_ci(candidate, branch)
            if run is None or run["status"] != "completed":
                return {"status": "waiting_for_ci", "pr": pr["number"], "candidate_sha": candidate}
            if run["conclusion"] != "success":
                raise Blocked("ci_failed", f"Full candidate CI failed for PR #{pr['number']}: https://github.com/{self.config.repo}/actions/runs/{run['id']}. Review and explicitly rerun after fixing; scheduled polls will not rerun it")
            # Fetch current attempt again: an old completed webhook must not race a rerun.
            current_run = self.api.call("GET", self.prefix + f"/actions/runs/{run['id']}")
            if current_run.get("run_attempt", 1) != run.get("run_attempt", 1) or current_run.get("status") != "completed" or current_run.get("conclusion") != "success" or not full_ci_run(current_run, run["workflow_id"], self.config, candidate, branch, False):
                return {"status": "ci_changed", "pr": pr["number"]}
            jobs = self.api.pages(self.prefix + f"/actions/runs/{run['id']}/attempts/{run.get('run_attempt', 1)}/jobs", "jobs")
            if not successful_jobs(jobs, self.config.required_checks):
                raise Blocked("ci_incomplete", f"PR #{pr['number']} lacks successful, unique jobs for every required full CI check: {', '.join(self.config.required_checks)}")
            fresh_pr = self.api.call("GET", self.prefix + f"/pulls/{pr['number']}")
            if fresh_pr["state"] != "open" or metadata(fresh_pr, self.config) != expected or fresh_pr["head"]["sha"] != candidate:
                return {"status": "candidate_changed", "pr": pr["number"]}
            if stable_release(self.api, self.config) != release:
                return {"status": "release_changed", "pr": pr["number"]}
            fresh_main = self.main_branch()
            if fresh_main["commit"]["sha"] != base:
                return {"status": "base_changed", "pr": pr["number"]}
            if not fresh_main.get("protected"):
                raise Blocked("branch_protection", "Protect main with strict up-to-date required CI checks before enabling automatic integration; the coordinator never bypasses protection")
            protection = self.api.call("GET", self.prefix + "/branches/main/protection")
            required = protection.get("required_status_checks") or {}
            contexts = {c.get("context"): c.get("app_id") for c in required.get("checks", [])}
            if not required.get("strict") or not protection.get("enforce_admins", {}).get("enabled") or any(contexts.get(name) != 15368 for name in self.config.required_checks):
                raise Blocked("branch_protection", "Main protection must enforce administrators, strict up-to-date checks, and every configured check from GitHub Actions (app15368)")
            # Protection closes the base/head race after this final read, including
            # for the repository owner token used by the host orchestrator.
            if self.main_branch()["commit"]["sha"] != base:
                return {"status": "base_changed", "pr": pr["number"]}
            result = self.api.call("PUT", self.prefix + f"/pulls/{pr['number']}/merge", {"sha": candidate, "merge_method": "merge"})
            if not result.get("merged"):
                raise Blocked("merge_rejected", f"GitHub declined the protected merge for PR #{pr['number']}; review branch requirements")
            self.state["pending_main"] = result["sha"]
            self.save()
            # Re-read main because another legitimate change can land just after merge.
            updated_main = self.main_branch()["commit"]["sha"]
            self.ensure_ci(updated_main, "main", main=True)
            return {"status": "merged", "pr": pr["number"], "merge_sha": result["sha"], "release": release["tag"]}
        except Blocked as exc:
            result = {"status": "blocked" if exc.kind != "base_changed" else "base_changed", "reason": exc.kind, "message": str(exc)}
            if exc.kind != "base_changed":
                try:
                    self.issue(exc.kind, release, str(exc))
                except Blocked as report_error:
                    result["issue_reporting_error"] = str(report_error)
                except Exception as report_error:
                    result["issue_reporting_error"] = type(report_error).__name__
            return result


def run_sync(config: SyncConfig, github_token: str | None = None, *, read_only: bool = False) -> dict[str, Any]:
    token = github_token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("A GitHub credential must be supplied in memory")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.repo) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.upstream):
        raise ValueError("Invalid repository name")
    if not config.cache_dir.is_absolute():
        raise ValueError("The private Git cache path must be absolute")
    config.cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config.cache_dir.chmod(0o700)
    api, git = GitHub(token), GitCache(config, token)
    if read_only:
        release = stable_release(api, config)
        main = api.call("GET", f"/repos/{config.repo}/branches/main")
        base = main["commit"]["sha"]
        git.fetch_main(base)
        git.fetch_release(release)
        if git.ancestor(release["release_sha"], base):
            return {"status": "current", "release": release["tag"], "main_sha": base, "read_only": True}
        return {"status": "candidate_preview", "candidate_sha": git.candidate(base, release), "release": release["tag"], "read_only": True}
    path = config.cache_dir / "coordinator-state.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    def save() -> None:
        temporary = path.with_suffix(".writing")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    try:
        result = Coordinator(config, api, git, state, save).run()
    except Blocked as exc:
        # An API outage can prevent issue reporting too; retain its safe status
        # locally so quiet cron never hides the failure from the operator.
        result = {"status": "blocked", "reason": exc.kind, "message": str(exc)}
    except Exception as exc:
        result = {"status": "error", "reason": type(exc).__name__}
    state["last_result"] = result
    state["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save()
    return result


def config_from_file(path: Path) -> SyncConfig:
    config = json.loads(path.read_text())
    section = config.get("upstream_sync", {})
    cache = section.get("cache_dir")
    if cache is None:
        cache = str(Path(config["state_dir"]) / "upstream-sync")
    return SyncConfig(cache_dir=Path(cache), repo=section.get("repo", "tomerh2001/securo"),
                      upstream=section.get("upstream", "securo-finance/securo"),
                      required_checks=tuple(section.get("required_checks", DEFAULT_CHECKS)),
                      dispatch_discovery_seconds=int(section.get("dispatch_discovery_seconds", 600)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--read-only", action="store_true", help="Inspect ancestry and preview a local merge without GitHub writes")
    args = parser.parse_args()
    result = run_sync(config_from_file(args.config), read_only=args.read_only)
    print(json.dumps(result))
    return 1 if result["status"] in ("blocked", "error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
