# AI PR Review Agent

An agent that automatically reviews a pull request when it's opened or updated: it reads
the diff, flags potential issues (bugs, security concerns, missing error handling, etc.),
and posts the findings back to the PR as inline review comments.

## How it works

```
PR opened/updated
      │
      ▼
GitHub Actions workflow triggers  (.github/workflows/pr-review.yml)
      │
      ▼
src/main.py
  1. Reads the PR number from the Actions event payload
  2. Fetches the changed files + diffs via the GitHub API   (src/github_client.py)
  3. Sends the diff to Claude with a review prompt. Claude can optionally call a
     read_file tool (capped at 4 calls) to see a full file beyond the diff hunk's
     limited context, then finishes by calling a forced submit_review tool for
     structured JSON output                                 (src/review_agent.py, src/prompts.py)
  4. Posts the result back as a single PR review: one summary comment
     + inline comments on specific lines                    (src/github_client.py)
```

The review logic (`review_pr()` in `review_agent.py`) has no GitHub API calls in it —
it just takes diff data in and returns structured findings out. That's deliberate: it
can be tested locally with a fake diff (see `local_test.py`), and could later be reused
by an evaluation script that feeds it synthetic test cases instead of live PRs.

## Setup

1. **Clone this repo** and create a Python virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Get an Anthropic API key** from the developer console, and copy `.env.example`
   to `.env`, filling in `ANTHROPIC_API_KEY`.

3. **Test locally first, without touching GitHub at all:**
   ```
   python local_test.py
   ```
   This runs the agent against a hardcoded fake diff (an obvious SQL injection bug) and
   prints what it finds. If this doesn't produce sensible output, nothing downstream will
   either — fix it here before going further.

4. **Wire up GitHub Actions:**
   - In this repo's Settings → Secrets and variables → Actions, add `ANTHROPIC_API_KEY`.
     (`GITHUB_TOKEN` is provided automatically by Actions — no setup needed.)
   - The workflow at `.github/workflows/pr-review.yml` triggers on `opened`,
     `synchronize`, and `reopened` PR events.

5. **Open a test PR** against this repo (e.g. edit one of the demo files with an
   intentional bug) and watch the Actions tab — the review should appear on the PR
   within a minute or so of the workflow completing.

## Key decisions

- **GitHub Actions over a hosted webhook server.** No infrastructure to stand up or
  pay for — GitHub provides both the trigger (the `pull_request` event) and the
  compute (a free runner on public repos).
- **Forced structured output via tool use**, rather than asking the model to "please
  respond in JSON." This guarantees parseable output instead of hoping the model
  doesn't wrap it in prose or markdown fences.
- **GitHub's newer line/side review comment fields**, instead of the legacy
  diff-position system — simpler, and GitHub validates the line is actually part of
  the diff for us.
- **Comment-only, never auto-approve or request-changes.** The agent's `event` type
  is always `COMMENT` — a human still makes the actual merge decision.
- **The core review function takes no GitHub API calls.** Keeps it testable in
  isolation and reusable if an eval harness gets built later.
- **`read_file` can fetch any file in the repo, gated by a deny-list, not an
  allow-list.** Earlier it only allowed files already in the PR's diff; that was safe
  but couldn't help with a bug that depends on a genuinely separate file (e.g. a
  permissions helper the diff doesn't touch). It's now open to any path, with obviously
  sensitive patterns blocked (`.env`, credentials, private keys, `.ssh/`, `.git/`,
  etc. -- see `DENIED_PATH_PATTERNS` in `review_agent.py`) as a defense against a
  malicious diff using the agent as an arbitrary-file-read/exfiltration primitive.
  This is a partial safety net, not a complete one: a file with an innocuous name that
  happens to contain a secret isn't caught by a filename-based deny-list.

## Known limitations

- **No incremental review.** Every push to the PR re-reviews the entire diff from
  scratch, rather than just what changed since the last review.
- **No deduplication.** Repeated pushes could produce overlapping/duplicate comments
  across multiple review runs.
- **Large diffs aren't chunked.** A very large PR could exceed reasonable prompt size;
  there's no splitting logic yet.
- **Fork PRs are untested.** `pull_request` (as opposed to `pull_request_target`)
  gives a read-only `GITHUB_TOKEN` for PRs from forks, which would prevent posting
  comments on external contributions to this repo. Not an issue for the demo (PRs are
  opened within the same repo), but worth knowing for real-world use.
- **`read_file` has no way to *discover* a relevant file it doesn't already know the
  name of.** It can fetch any path once named (e.g. from an import statement in the
  diff), but in a large codebase where the relevant file isn't obviously named
  anywhere in the diff, there's no search/grep tool to help it find where to look. That
  would be a genuinely different (bigger) feature -- a repo-wide code search tool, not
  just "let it read one more file."
- **Occasional malformed structured output, not fully eliminated.** A diff containing
  a string shaped like a live secret (e.g. `sk_live_...`) used to *reliably* make the
  model emit stray `<parameter name="...">` tool-call syntax instead of valid JSON for
  the `comments` field (3/3 failures). An explicit anti-leakage instruction in
  `SYSTEM_PROMPT` fixed that specific case (5/5 clean after). But the same failure
  shape resurfaced later, unprompted by secrets, during `read_file` loop testing
  against a real PR -- nondeterministically (most runs clean, one wasn't). This looks
  like a broader structured-output fragility in longer/more complex generations that
  prompting alone hasn't eliminated, just made rarer for the specific case tested.
  `review_pr()`'s `_validate_result()` fails soft on it (drops the malformed comments,
  posts a generic notice) rather than crashing or posting garbled text -- a safety
  net, not a fix.

## Evaluating review quality

`local_test.py` only tells you the agent works on one hardcoded diff, and only if you
read the output yourself. `eval_harness.py` runs it against a fixed set of synthetic
diffs and scores how many planted bugs get caught vs. how many false positives show up
on clean code:

```
python eval_harness.py
```

Cases range from obvious (SQL injection, unclosed file handle, division by zero,
hardcoded secret, bare `except`) to deliberately hard -- requiring real reasoning
rather than keyword-spotting: an N+1 query hidden in a loop, a mutable default
argument, a silent output-format contract break, and a non-atomic race condition on a
shared counter. One case (`cross_file_ownership_type_mismatch`) supplies a second file
via `read_file` that genuinely isn't part of the diff, exercising the same
outside-the-diff fetch used in the real `tqdm` demo below -- expects a confirmed `bug`
finding, not just a hedge, since `read_file` can now settle it. There's also a fast,
non-LLM check of the `read_file` deny-list itself (no API calls). Current score:
**10/10 planted bugs caught, 0/2 false positives** on genuinely clean diffs. Re-run
this after any change to `prompts.py` or `review_agent.py` to check whether review
quality moved.

## Demo: read_file against a real repo

To validate `read_file` against something more convincing than a synthetic fixture, a
one-line bug was planted in a fork of the real [tqdm](https://github.com/tqdm/tqdm)
library: renaming `self.total` to `self.total_count` in `tqdm/std.py`'s `__init__`.
The diff itself is a single innocent-looking line -- nothing about it looks wrong, and
the several other places in the same file that still read `self.total`
(`__bool__`, `__len__`, `format_dict`, `reset`) are all far outside the diff's visible
context window.

- **Without `read_file`:** the model hedges based on general (likely memorized, since
  tqdm is a well-known public library) knowledge that `total` is a widely-used
  attribute -- a reasonable but non-specific warning.
- **With `read_file`:** the model fetches the full file, and returns a confirmed `bug`
  naming the exact broken methods and why, with a suggested fix.

Real PR, real GitHub API calls, real posted review: [megbala/tqdm#1](https://github.com/megbala/tqdm/pull/1).

## AI tools used

<!-- Fill in: which parts you had Claude/Copilot/etc. scaffold vs. wrote yourself. -->
