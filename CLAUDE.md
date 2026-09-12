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
- `eval_harness.py`: 10/10 planted bugs caught, 0/2 false positives, across categories
  from obvious (SQL injection) to genuinely hard (N+1 queries, mutable default args,
  race conditions, silent format-contract breaks, cross-file argument-type mismatches).
  The model held up on every category tried so far, including ones designed to require
  real reasoning with zero visible red flags in the diff.
- **`read_file` is now wired into a real multi-turn tool-use loop** (`review_pr()` in
  `src/review_agent.py`, capped at `MAX_READ_FILE_CALLS = 4` to guarantee termination).
  Can fetch **any file in the repo**, gated by `DENIED_PATH_PATTERNS` (a deny-list —
  `.env`, credentials, private keys, `.ssh/`, `.git/`, etc.) rather than an allow-list
  restricted to diff paths — that was the initial version, deliberately loosened once
  the diff-only restriction turned out to block legitimate cross-file investigation
  (e.g. reading a permissions helper a PR relies on but doesn't touch). Validated: (1)
  synthetic same-file distant-usage bug, locally, with a fake read_file; (2) a real
  fork of `tqdm/tqdm` (`megbala/tqdm`) with a genuine one-line attribute rename planted
  in `tqdm/std.py` (`self.total` → `self.total_count`, breaking `__bool__`/`__len__`/
  `format_dict`/`reset` elsewhere in the file) — real PR at `megbala/tqdm#1`, reviewed
  via the real GitHub API, review posted for real; (3) `eval_harness.py`'s
  `cross_file_ownership_type_mismatch` case now actually supplies a second file
  (`app/permissions.py`) that is NOT part of the diff via `repo_files`, and the model
  correctly fetches it and cites its exact implementation. Before `read_file` in all
  three: model hedges from general/memorized knowledge. After: confirmed, specific,
  cites the actual broken code by name.
- Aside worth remembering: under `tool_choice: {"type": "auto"}` (which the read_file
  loop uses while budget remains), the model spontaneously emits a `thinking` content
  block even without the `thinking` API parameter being set. Not visible under the
  forced `tool_choice` used everywhere else in this codebase.

## Next steps, in order — validate each before moving to the next
1. ~~`pip install -r requirements.txt`, then `python local_test.py`.~~ Done.
2. ~~Test `src/github_client.py` directly against a real PR; confirm `ANTHROPIC_API_KEY`
   secret is set; open a real PR and confirm the workflow runs end-to-end.~~ Done.
3. ~~Decide on read_file wiring.~~ Done — built, tested, validated against a real fork.
4. ~~Decide whether to extend read_file beyond diff-only files.~~ Done — switched to a
   deny-list policy, see above.
5. Comment deduplication across repeated pushes — still open, not started.

## Not yet built (flag these, don't just build them unprompted)
- Comment deduplication across repeated pushes to the same PR — not yet built,
  discussed as a possible next step, not yet started.
- `read_file` has no search/discovery capability — it can fetch any named path, but
  can't find a relevant file it doesn't already know the name of (e.g. in a huge
  codebase with no obvious import/reference pointing at it). Would need a repo-wide
  code search tool, a genuinely bigger feature than "read one more file" — flagged as
  an open idea, not started.
- Token/cost tracking not yet added to `eval_harness.py` -- no visibility into how much
  more a read_file-enabled review costs vs. a single-call one. Discussed, not started.
- No size guard on `read_file` -- `github_client.read_file()` returns a file's full
  content unconditionally; a very large file would dump a lot of tokens into the
  conversation. Discussed, not started.

## Known model bug found via eval_harness.py (mitigated, not eliminated)
When a diff contained a string shaped like a live secret (e.g. `sk_live_...`), the
model reliably returned malformed JSON for the `comments` field (stray
`<parameter name="...">` syntax) instead of a proper array — even under forced
`tool_choice`. An explicit instruction added to `SYSTEM_PROMPT` (`src/prompts.py`)
telling the model never to emit tool-call/parameter-tag syntax inside a field's value
made that specific case pass 5/5 clean runs vs. 3/3 failures before.

However: the same failure shape (leaked `<parameter name="...">` tags) resurfaced
later, unprompted by any secret-like content, during `read_file` loop testing against
the real tqdm PR — nondeterministically (2 of ~5 runs on the identical input were
clean before one failed). So this is NOT root-caused or eliminated; it looks like a
broader structured-output fragility in longer/more complex multi-turn generations,
which the secret-string case happened to trigger reliably while other inputs trigger
it only rarely. `_validate_result()` in `review_agent.py` was tightened as a result:
it now also rejects a `summary` containing the literal substring `"<parameter"`, since
the leak can land there while `comments` still happens to validate as a (technically
valid, empty) list -- the original check only looked at `comments`'s shape and missed
this. Still fails soft (drops the finding, returns a generic "malformed output"
summary) rather than crashing or silently posting garbled text.

## Known limitations (already documented in README — keep it updated as more surface)
No incremental review (re-reviews whole diff every push), no comment dedup, no
chunking for very large diffs, fork PRs untested (read-only GITHUB_TOKEN issue).
