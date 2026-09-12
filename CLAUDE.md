# PR Review Agent — context for Claude Code

## What this is
An agent that reviews a GitHub PR automatically when it's opened/updated: fetches the
diff, sends it to Claude for review, posts findings back as a PR review (summary +
inline comments).

## Decisions already made — don't relitigate these without discussion
- **Language:** Python.
- **Trigger/runtime:** GitHub Actions (`.github/workflows/pr-review.yml`), not a
  hosted webhook server — no infra to stand up, free compute on a public repo.
- **Structured output via forced tool-use** (`tool_choice: {"type": "tool", ...}`),
  not free-text JSON parsing — guarantees parseable output.
- **GitHub's line/side review comment fields**, not the legacy diff-position system.
- **Comment-only.** `event` is always `"COMMENT"` — never auto-approve or
  request-changes. A human still makes the merge decision.
- **`review_pr()` in `src/review_agent.py` has zero GitHub API calls in it** —
  deliberately kept pure so it can be reused later by a local eval script fed
  synthetic diffs. Keep it that way.
- Model in use: `claude-sonnet-5`.

## Current status
- Repo is set up, `.env` has real credentials, code has been pushed to GitHub.
- `ANTHROPIC_API_KEY` secret set on the GitHub repo. Demo PR (#1, `demo/planted-bugs`)
  opened, workflow ran end-to-end successfully, agent correctly flagged both planted
  bugs with valid suggestions, PR merged — `demo/stats.py` now lives in `main` as a
  reusable fixture for future demos.
- `eval_harness.py` built and run: 5/5 planted bugs caught, 0/2 false positives on
  clean diffs. One case (hardcoded_secret) surfaced a real, reproducible model bug
  (see below), now fixed via a prompt change; `review_pr()` also still validates the
  output shape and fails soft as a safety net.

## Next steps, in order — validate each before moving to the next
1. ~~`pip install -r requirements.txt`, then `python local_test.py`.~~ Done.
2. ~~Test `src/github_client.py` directly against a real PR; confirm `ANTHROPIC_API_KEY`
   secret is set; open a real PR and confirm the workflow runs end-to-end.~~ Done.
3. Decide on the two "Not yet built" items below.

## Not yet built (flag these, don't just build them unprompted)
- The `read_file` tool in `github_client.py` exists but isn't wired into the agent's
  tool-use loop — the agent currently only ever sees diff hunks, not full files.
  Whether to add this is an open decision, not yet made.
- Comment deduplication across repeated pushes to the same PR — not yet built,
  discussed as a possible next step, not yet started.

## Known model bug found via eval_harness.py (fixed)
When a diff contained a string shaped like a live secret (e.g. `sk_live_...`), the
model reliably returned malformed JSON for the `comments` field (stray
`<parameter name="...">` syntax) instead of a proper array — even under forced
`tool_choice`. Fixed by adding an explicit instruction to `SYSTEM_PROMPT`
(`src/prompts.py`) telling the model never to emit tool-call/parameter-tag syntax
inside a field's value. 5/5 clean runs after the change vs. 3/3 failures before —
not conclusively root-caused (why secret-shaped strings specifically triggered this
is still unclear), but the fix has held up under repeated testing. `review_pr()`
still validates the output shape and fails soft as a defense-in-depth safety net.
See README's Known limitations for more detail.

## Known limitations (already documented in README — keep it updated as more surface)
No incremental review (re-reviews whole diff every push), no comment dedup, no
chunking for very large diffs, fork PRs untested (read-only GITHUB_TOKEN issue).
