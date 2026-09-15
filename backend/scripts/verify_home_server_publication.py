"""Only a complete, successful full CI run for current fork main can publish."""

import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

REPOSITORY = "tomerh2001/securo"
CHECKS = {
    "Backend Tests", "Frontend Checks", "Migration Chain", "Helm Checks",
    "Update Automation Tests",
}


def eligible(run, jobs, current_main, workflow_id):
    return (
        run.get("workflow_id") == workflow_id
        and run.get("path") == ".github/workflows/ci.yml"
        and run.get("head_repository", {}).get("full_name") == REPOSITORY
        and run.get("head_branch") == "main"
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")) is not None
        and run["head_sha"] == current_main
        and all(
            len(matching := [job for job in jobs if job.get("name") == name]) == 1
            and matching[0].get("status") == "completed"
            and matching[0].get("conclusion") == "success"
            for name in CHECKS
        )
    )


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    run_id = int(event["workflow_run"]["id"])
    token = os.environ["GH_TOKEN"]

    def get(path):
        request = Request(
            f"https://api.github.com/repos/{REPOSITORY}/{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    run = get(f"actions/runs/{run_id}")
    workflow = get("actions/workflows/ci.yml")
    jobs = get(f"actions/runs/{run_id}/attempts/{int(run['run_attempt'])}/jobs?per_page=100")
    if jobs["total_count"] > 100:
        raise RuntimeError("CI job inventory exceeds the reviewed publication gate")
    current_main = get("git/ref/heads/main")["object"]["sha"]
    publish = eligible(run, jobs["jobs"], current_main, workflow["id"])
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"publish={str(publish).lower()}\n")
        if publish:
            output.write(f"sha={run['head_sha']}\n")
    print("Current main passed all full CI checks." if publish else "Skipped: this is not current main's successful full CI run.")


if __name__ == "__main__":
    main()
