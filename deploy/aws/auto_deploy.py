#!/usr/bin/env python3
"""Deploy the latest main commit only after its GitHub CI run succeeds."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen


REPOSITORY = "angellashin/ai-innovators-challenge"
ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = Path.home() / ".local/state/replan/last-deployed-sha"
HEALTH_URLS = ("http://127.0.0.1:8000/health", "http://127.0.0.1:3000/")


def git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=ROOT, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def latest_ci_approved(runs: list[dict], sha: str) -> bool:
    matching = [
        run
        for run in runs
        if run.get("name") == "CI"
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("head_sha") == sha
    ]
    if not matching:
        return False
    latest = max(
        matching,
        key=lambda run: (
            run.get("run_number", 0),
            run.get("run_attempt", 0),
            run.get("id", 0),
        ),
    )
    return latest.get("status") == "completed" and latest.get("conclusion") == "success"


def ci_approved(sha: str) -> bool:
    url = (
        f"https://api.github.com/repos/{REPOSITORY}/actions/runs"
        f"?head_sha={sha}&event=push&per_page=10"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "replan-ec2-auto-deploy",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=20) as response:
        runs = json.load(response).get("workflow_runs", [])
    return latest_ci_approved(runs, sha)


def wait_for_health() -> None:
    for url in HEALTH_URLS:
        deadline = time.monotonic() + 180
        while True:
            try:
                with urlopen(url, timeout=5) as response:
                    if response.status == 200:
                        break
            except Exception as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Health check failed: {url}") from error
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Health check failed: {url}")
            time.sleep(5)


def main() -> None:
    if git("branch", "--show-current") != "main":
        raise RuntimeError("Deployment checkout must be on main")
    if git("status", "--porcelain"):
        raise RuntimeError("Deployment checkout has local changes; refusing to overwrite them")

    git("fetch", "origin", "main")
    target = git("rev-parse", "FETCH_HEAD")
    current = git("rev-parse", "HEAD")
    deployed = STATE_FILE.read_text(encoding="utf-8").strip() if STATE_FILE.exists() else ""
    if current == target and deployed == target:
        print(f"Already deployed {target}", flush=True)
        return
    if not ci_approved(target):
        print(f"Waiting for successful main CI on {target}", flush=True)
        return

    if current != target:
        git("merge", "--ff-only", "FETCH_HEAD")
    print(f"Deploying {target}", flush=True)
    subprocess.run(("docker", "compose", "up", "-d", "--build"), cwd=ROOT, check=True)
    wait_for_health()
    subprocess.run(("docker", "compose", "ps"), cwd=ROOT, check=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(target + "\n", encoding="utf-8")
    print(f"Deployment healthy: {target}", flush=True)


if __name__ == "__main__":
    main()
