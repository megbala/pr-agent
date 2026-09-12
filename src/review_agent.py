"""
The actual "agent" logic: take diff data in, get structured review findings out.

Deliberately has zero GitHub API calls in it. That means:
  - You can test it completely locally with a fake diff (see local_test.py).
  - If you later add an eval harness, it can import review_pr() directly and feed it
    synthetic test cases instead of live PR data -- no refactor needed.
"""

import anthropic

from prompts import SYSTEM_PROMPT, REVIEW_TOOL, build_diff_context

MODEL = "claude-sonnet-5"


def review_pr(files: list[dict], client: anthropic.Anthropic | None = None) -> dict:
    """
    files: GitHub's "list PR files" API shape -- each item needs at least
           {"filename": str, "status": str, "patch": str}.

    Returns a dict shaped like:
        {"summary": str, "comments": [{"file", "line", "severity", "comment", "suggestion"}, ...]}
    """
    client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env by default

    diff_context = build_diff_context(files)
    if not diff_context:
        return {"summary": "No reviewable text changes found in this PR.", "comments": []}

    message = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[REVIEW_TOOL],
        tool_choice={"type": "tool", "name": "submit_review"},  # force structured output
        messages=[
            {"role": "user", "content": f"Review this pull request diff:\n\n{diff_context}"},
        ],
    )

    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_review":
            return block.input  # already a plain dict matching the schema in prompts.py

    # Shouldn't happen with tool_choice forcing the call, but fail soft rather than crash.
    return {"summary": "Review agent did not return structured output.", "comments": []}
