"""Writes release notes based on merged PRs."""

import json
import os
import subprocess
import sys
from pathlib import Path


def gh(*args: str) -> str:
    """Run a gh command and return its stdout, stripped of trailing whitespace."""
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


LATEST_TAG = gh("release", "list", "-L", "1", "--json", "tagName", "-q", ".[0].tagName")


def release_types() -> list[str]:
    """Return the PR types that appear in release notes."""
    pr_types_file = Path(__file__).parent.parent / "pr-types.json"
    return json.loads(pr_types_file.read_text())["release"]


def merged_prs() -> list[dict]:
    """Return the number and title of PRs merged since the latest release."""
    repo = os.environ["GITHUB_REPOSITORY"]
    release_date = gh("api", f"repos/{repo}/releases/tags/{LATEST_TAG}", "--jq", ".created_at")
    prs = gh(
        "pr",
        "list",
        "--state",
        "merged",
        "--base",
        "main",
        "--json",
        "number,title",
        "--search",
        f"merged:>{release_date}",
        "--limit",
        "50",
    )
    return json.loads(prs)[::-1]  # Put oldest merged first.


def release_notes(prs: list[dict], new_tag: str) -> str:
    """Return the release notes body for the given PRs.

    Groups PRs by title prefix, in the order given by pr-types.json.
    """
    entries = ""
    for pr_type in release_types():
        prefix = f"{pr_type}:"
        for pr in prs:
            if pr["title"].startswith(prefix):
                entries += f"- {pr['title']} (#{pr['number']})\n"
    if not entries:
        entries = "This is a maintenance release with no fixes or new features.\n"
    repo = os.environ["GITHUB_REPOSITORY"]
    return f"{entries}\nChangelog: https://github.com/{repo}/compare/{LATEST_TAG}...{new_tag}"


if __name__ == "__main__":
    print(release_notes(merged_prs(), sys.argv[1]))
