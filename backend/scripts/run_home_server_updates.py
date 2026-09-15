"""Five-minute host entrypoint; credentials stay in memory, never in GitHub secrets."""

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def write_state(path, value):
    temporary = path.with_suffix(".writing")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def run_child(command, environment, timeout):
    process = subprocess.Popen(
        command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Terminate the child's process group, including a still-running Git or
        # Docker CLI. The updater's saved rollout intent blocks an unsafe retry.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # The parent can exit while a grandchild survives and retains the
            # update lock. Clean up that group even when communicate returned.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=30)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def run(config_path):
    os.umask(0o077)
    config_path = Path(config_path).resolve(strict=True)
    config = json.loads(config_path.read_text())
    directory = Path(config["state_dir"])
    if not directory.is_absolute():
        raise ValueError("state_dir must be an absolute private directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "orchestrator.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        status = {"checked_at": datetime.now(timezone.utc).isoformat(), "phases": {}}
        state_path = directory / "orchestrator-status.json"
        command = config["credential_command"]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError("credential_command must be an explicit argument list")
        try:
            credential = subprocess.run(command, capture_output=True, text=True, timeout=45, check=True)
            token = credential.stdout.strip()
            if not token or "\n" in token:
                raise ValueError("Credential resolver returned no usable token")
        except (subprocess.SubprocessError, OSError, ValueError):
            status["status"] = "credential_unavailable"
            write_state(state_path, status)
            return 1
        environment = dict(os.environ)
        environment["GH_TOKEN"] = token
        # Pin this invocation to one installed bundle even if deployment advances current.
        bundle = Path(__file__).resolve().parent
        failed = False
        for filename, timeout in [("upstream_release_sync.py", 240), ("home_server_update.py", 3600)]:
            try:
                result = run_child(
                    [sys.executable, str(bundle / filename), "--config", str(config_path)],
                    environment, timeout,
                )
                status["phases"][filename] = {"exit_code": result.returncode}
                failed |= result.returncode != 0
            except (subprocess.SubprocessError, OSError):
                # Children keep their own sanitized diagnostics. Never echo subprocess
                # output: credential-helper or Git errors can contain authentication data.
                status["phases"][filename] = {"error": "execution_failed_or_timed_out"}
                failed = True
            write_state(state_path, status)
        status["status"] = "attention_required" if failed else "complete"
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_state(state_path, status)
        return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.config))
