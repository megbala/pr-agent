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
  3. Sends the diff to Claude with a review prompt, forcing structured
     JSON-shaped output via a tool call                     (src/review_agent.py, src/prompts.py)
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
- **No evaluation harness yet.** There's no labeled test set of "PRs with known
  planted bugs" to measure recall/false-positive rate against — output quality is
  currently judged by eye. This would be the first thing added with more time.
- **The `read_file` tool exists in `github_client.py` but isn't wired into the agent
  loop yet.** Right now the agent only ever sees diff hunks, not full file content —
  a genuine limitation for changes where surrounding context matters.

## AI tools used

<!-- Fill in: which parts you had Claude/Copilot/etc. scaffold vs. wrote yourself. -->
