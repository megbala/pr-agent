"""
Entry point run inside the GitHub Actions job. Reads the PR event, fetches the diff,
runs the review agent, posts the result back to the PR.
"""

import json
import os

from github_client import get_pr_files, post_review
from review_agent import review_pr


def get_pr_number_from_event() -> int:
    event_path = os.environ["GITHUB_EVENT_PATH"]
    with open(event_path) as f:
        event = json.load(f)
    return event["pull_request"]["number"]


def main() -> None:
    token = os.environ["GITHUB_TOKEN"]
    owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
    pr_number = get_pr_number_from_event()

    print(f"Reviewing {owner}/{repo} PR #{pr_number}...")

    files = get_pr_files(owner, repo, pr_number, token)
    print(f"Found {len(files)} changed file(s).")

    result = review_pr(files)
    summary = result.get("summary", "")
    comments = result.get("comments", [])
    print(f"Agent produced {len(comments)} inline comment(s).")

    if not summary and not comments:
        print("Nothing to post.")
        return

    post_review(owner, repo, pr_number, token, summary, comments)
    print("Review posted.")


if __name__ == "__main__":
    main()
