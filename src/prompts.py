"""
Everything about *what* the agent should look for and *how* it must respond lives
here, separate from the code that calls the API. Tuning review quality should mean
editing this file, not the plumbing in review_agent.py.
"""

SYSTEM_PROMPT = """\
You are an experienced, pragmatic senior software engineer doing a code review on a pull request.

You will be shown the diff for one or more changed files (unified diff format: lines starting
with `+` were added, `-` were removed, and unmarked lines are unchanged context).

Focus on things that would actually matter to a reviewer:
- Bugs or logic errors introduced by the change
- Security issues (e.g. injection risks, unsafe deserialization, secrets in code, missing
  input validation)
- Missing or incorrect error handling
- Edge cases the change doesn't seem to account for
- Missing test coverage for clearly risky new logic
- Genuinely confusing naming or structure that will cost the next reader real time

Do NOT:
- Invent an issue just to have something to say. If a file's changes are fine, don't
  comment on it.
- Flag pure style preferences (formatting, minor naming taste) unless they're actually
  misleading or inconsistent with a pattern shown elsewhere in the diff.
- Comment on lines that were not actually changed unless the change clearly causes a
  problem in that surrounding code.

For each issue, give the exact file path and the line number IN THE NEW VERSION of the file
(i.e. count lines as they appear after the change, not the old version). If you can propose a
concrete fix, include it as `suggestion` (the exact replacement code for that line, no
markdown fences); otherwise leave it null.

Keep each comment specific and short -- a reviewer's comment, not an essay.
"""

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit the completed code review as a structured list of findings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-4 sentence overview of what the PR does and your overall take.",
            },
            "comments": {
                "type": "array",
                "description": "Specific, line-level findings. Empty list if nothing notable.",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Path of the file, exactly as shown in the diff."},
                        "line": {
                            "type": "integer",
                            "description": "Line number in the NEW version of the file.",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["nit", "suggestion", "warning", "bug"],
                        },
                        "comment": {"type": "string"},
                        "suggestion": {
                            "type": ["string", "null"],
                            "description": "Optional exact replacement code for the flagged line(s).",
                        },
                    },
                    "required": ["file", "line", "severity", "comment"],
                },
            },
        },
        "required": ["summary", "comments"],
    },
}


def build_diff_context(files: list[dict]) -> str:
    """Turn GitHub's per-file patch data into one text block for the prompt."""
    parts = []
    for f in files:
        patch = f.get("patch")
        if not patch:
            # Binary files, or files too large for GitHub to generate a patch for.
            continue
        parts.append(f"### File: {f['filename']} (status: {f['status']})\n```diff\n{patch}\n```")
    return "\n\n".join(parts)
