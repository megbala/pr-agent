"""
The actual "agent" logic: take diff data in, get structured review findings out.

Deliberately has zero GitHub API calls in it. That means:
  - You can test it completely locally with a fake diff (see local_test.py).
  - If you later add an eval harness, it can import review_pr() directly and feed it
    synthetic test cases instead of live PR data -- no refactor needed.
"""

from typing import Callable

import anthropic

from prompts import SYSTEM_PROMPT, REVIEW_TOOL, READ_FILE_TOOL, build_diff_context

MODEL = "claude-sonnet-5"
MAX_READ_FILE_CALLS = 4  # cap tool round-trips so a confused model can't loop forever


def review_pr(
    files: list[dict],
    client: anthropic.Anthropic | None = None,
    read_file: Callable[[str], str] | None = None,
) -> dict:
    """
    files: GitHub's "list PR files" API shape -- each item needs at least
           {"filename": str, "status": str, "patch": str}.
    read_file: optional callable(path) -> full file content, letting the model see beyond
               the diff hunk when it asks to. If omitted, the read_file tool isn't offered
               at all and this behaves exactly like a single forced structured-output call.

    Returns a dict shaped like:
        {"summary": str, "comments": [{"file", "line", "severity", "comment", "suggestion"}, ...]}
    """
    client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env by default

    diff_context = build_diff_context(files)
    if not diff_context:
        return {"summary": "No reviewable text changes found in this PR.", "comments": []}

    diff_paths = {f["filename"] for f in files}
    tools = [REVIEW_TOOL] + ([READ_FILE_TOOL] if read_file else [])
    messages = [
        {"role": "user", "content": f"Review this pull request diff:\n\n{diff_context}"},
    ]

    reads_used = 0
    while True:
        # Once the read_file budget is spent (or it was never offered), force the final
        # answer so this loop is guaranteed to terminate.
        force_submit = not read_file or reads_used >= MAX_READ_FILE_CALLS

        message = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            tools=[REVIEW_TOOL] if force_submit else tools,
            tool_choice={"type": "tool", "name": "submit_review"} if force_submit else {"type": "auto"},
            messages=messages,
        )

        submit = next(
            (b for b in message.content if b.type == "tool_use" and b.name == "submit_review"), None
        )
        if submit:
            return _validate_result(submit.input)

        reads = [b for b in message.content if b.type == "tool_use" and b.name == "read_file"]
        if not reads:
            # Shouldn't happen with tool_choice forcing a call, but fail soft rather than crash.
            return {"summary": "Review agent did not return structured output.", "comments": []}

        messages.append({"role": "assistant", "content": message.content})
        messages.append({
            "role": "user",
            "content": [_run_read_file(block, diff_paths, read_file) for block in reads],
        })
        reads_used += len(reads)


def _run_read_file(block, diff_paths: set[str], read_file: Callable[[str], str]) -> dict:
    path = block.input.get("path", "")
    if path not in diff_paths:
        content = f"Error: '{path}' is not a file in this PR's diff."
    else:
        try:
            content = read_file(path)
        except Exception as e:
            content = f"Error reading '{path}': {e}"
    return {"type": "tool_result", "tool_use_id": block.id, "content": content}


def _validate_result(result: dict) -> dict:
    """
    Forced tool_choice guarantees the model calls submit_review, but not that every
    field inside it actually matches the schema's declared types. Observed failure
    modes so far: `comments` coming back as a malformed string instead of a list, and
    (rarer, seen in longer multi-turn read_file conversations) stray tool-call-like
    tags such as `<parameter name="...">` leaking into `summary` while `comments`
    still happens to be a technically-valid empty list. Neither is fully eliminated by
    prompting alone -- fail soft here rather than let a bad shape crash main.py or
    github_client.py downstream with a confusing traceback.
    """
    summary = result.get("summary", "")
    comments = result.get("comments", [])
    malformed = (
        not isinstance(summary, str)
        or not isinstance(comments, list)
        or not all(isinstance(c, dict) for c in comments)
        or "<parameter" in summary
    )
    if malformed:
        return {
            "summary": "Review agent returned malformed output; comments were dropped.",
            "comments": [],
        }
    return {"summary": summary, "comments": comments}
