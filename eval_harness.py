"""
Eval harness: run the review agent against a small, fixed set of synthetic diffs with
known planted bugs (plus a couple of clean diffs), and score how many it actually
catches -- without flagging false positives on code that's fine.

This exists because local_test.py only tells you the agent works on ONE diff, and only
if you read the output yourself. This gives a repeatable, quantifiable signal (recall
on planted bugs, false-positive rate on clean diffs) so a prompt or model change can be
judged by a number instead of a vibe. Re-run after any change to prompts.py or
review_agent.py.

Run:
    python eval_harness.py
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv

load_dotenv()  # reads ANTHROPIC_API_KEY from .env

from review_agent import review_pr, _is_denied_path  # noqa: E402

SEVERITY_RANK = {"nit": 0, "suggestion": 1, "warning": 2, "bug": 3}


@dataclass
class Expected:
    file: str
    line: int
    keyword: str  # substring (case-insensitive) that should appear in a matching comment
    min_severity: str = "warning"
    line_tolerance: int = 2  # LLM line counting isn't pixel-perfect -- allow some slack


@dataclass
class Case:
    name: str
    files: list[dict]
    expected: list[Expected] = field(default_factory=list)
    should_be_clean: bool = False  # True = agent should raise no bug/warning findings at all
    repo_files: dict[str, str] | None = None  # other files read_file can fetch, keyed by path


def _make_read_file(repo_files: dict[str, str]):
    def read_file(path: str) -> str:
        if path not in repo_files:
            raise FileNotFoundError(f"no such file in this fixture: {path}")
        return repo_files[path]
    return read_file


CASES = [
    Case(
        name="sql_injection",
        files=[{
            "filename": "app/db.py",
            "status": "modified",
            "patch": (
                "@@ -10,6 +10,9 @@ def get_user(user_id):\n"
                "     conn = get_connection()\n"
                "     cursor = conn.cursor()\n"
                "-    cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))\n"
                "+    query = \"SELECT * FROM users WHERE id = \" + user_id\n"
                "+    cursor.execute(query)\n"
                "     return cursor.fetchone()\n"
            ),
        }],
        expected=[Expected(file="app/db.py", line=13, keyword="inject", min_severity="bug")],
    ),
    Case(
        name="unclosed_file_handle",
        files=[{
            "filename": "app/config.py",
            "status": "modified",
            "patch": (
                "@@ -1,3 +1,4 @@\n"
                " def load_config(path):\n"
                "-    with open(path) as f:\n"
                "-        return f.read()\n"
                "+    f = open(path)\n"
                "+    data = f.read()\n"
                "+    return data\n"
            ),
        }],
        expected=[Expected(file="app/config.py", line=2, keyword="clos")],
    ),
    Case(
        name="division_by_zero",
        files=[{
            "filename": "app/stats.py",
            "status": "modified",
            "patch": (
                "@@ -1,1 +1,5 @@\n"
                " import statistics\n"
                "+\n"
                "+def average(values):\n"
                "+    total = sum(values)\n"
                "+    return total / len(values)\n"
            ),
        }],
        expected=[Expected(file="app/stats.py", line=5, keyword="empty")],
    ),
    Case(
        name="hardcoded_secret",
        files=[{
            "filename": "app/settings.py",
            "status": "modified",
            "patch": (
                "@@ -1,2 +1,3 @@\n"
                " import os\n"
                "+STRIPE_API_KEY = \"sk_live_51H8xyzABCDEFGHIJKLMNOP\"\n"
                " DEBUG = os.environ.get(\"DEBUG\", False)\n"
            ),
        }],
        expected=[Expected(file="app/settings.py", line=2, keyword="secret")],
    ),
    Case(
        name="bare_except_swallows_error",
        files=[{
            "filename": "app/worker.py",
            "status": "modified",
            "patch": (
                "@@ -1,7 +1,6 @@\n"
                " def process(item):\n"
                "     try:\n"
                "         result = do_work(item)\n"
                "-    except ValueError as e:\n"
                "-        log.error(f\"failed: {e}\")\n"
                "-        raise\n"
                "+    except:\n"
                "+        pass\n"
                "     return result\n"
            ),
        }],
        expected=[Expected(file="app/worker.py", line=4, keyword="except", line_tolerance=3)],
    ),
    Case(
        # A real production-shaped bug: batched fetch replaced by a per-item DB call
        # inside a loop. Nothing is wrong line-by-line -- it only matters at scale, so
        # this tests whether the model reasons about the shape of the code rather than
        # spotting a suspicious keyword. Lower severity threshold since a performance
        # regression is more likely to land as a "suggestion" than a "bug".
        name="n_plus_one_query",
        files=[{
            "filename": "app/orders.py",
            "status": "modified",
            "patch": (
                "@@ -1,5 +1,6 @@\n"
                " def get_order_totals(orders):\n"
                "-    customer_ids = [o.customer_id for o in orders]\n"
                "-    customers = db.get_customers_by_ids(customer_ids)\n"
                "-    customer_map = {c.id: c for c in customers}\n"
                "-    return [(o, customer_map[o.customer_id]) for o in orders]\n"
                "+    results = []\n"
                "+    for order in orders:\n"
                "+        customer = db.get_customer(order.customer_id)\n"
                "+        results.append((order, customer))\n"
                "+    return results\n"
            ),
        }],
        expected=[Expected(file="app/orders.py", line=4, keyword="n+1", min_severity="suggestion", line_tolerance=2)],
    ),
    Case(
        # Classic Python gotcha: a mutable default argument is created once at function
        # definition time and silently shared/leaked across every call. Deterministic
        # and well-defined, but requires actually understanding Python semantics rather
        # than pattern-matching a named vulnerability.
        name="mutable_default_argument",
        files=[{
            "filename": "app/cache.py",
            "status": "modified",
            "patch": (
                "@@ -1,1 +1,5 @@\n"
                " import time\n"
                "+\n"
                "+def add_to_cache(key, value, _cache={}):\n"
                "+    _cache[key] = (value, time.time())\n"
                "+    return _cache\n"
            ),
        }],
        expected=[Expected(file="app/cache.py", line=3, keyword="default")],
    ),
    Case(
        # The bug is only fully verifiable by reading a SECOND file this diff doesn't
        # touch: user_can_access() (defined in permissions.py, provided below via
        # repo_files -- NOT part of this PR's diff) expects a resource with an
        # .owner_id attribute, but the caller now passes doc.owner (a user object)
        # instead of doc itself. Without read_file the model still gets suspicious and
        # hedges from the argument-type change alone ("unless user_can_access was
        # specifically refactored..."). With read_file now able to fetch files outside
        # the diff (see review_agent.py's deny-list, not allow-list, policy), it should
        # turn that hedge into a confirmed, specific answer -- expecting `bug` severity
        # here, not just `warning`.
        name="cross_file_ownership_type_mismatch",
        files=[{
            "filename": "app/views.py",
            "status": "modified",
            "patch": (
                "@@ -1,7 +1,7 @@\n"
                " from permissions import user_can_access\n"
                " \n"
                " def get_document(request, doc_id):\n"
                "     doc = Document.objects.get(id=doc_id)\n"
                "-    if not user_can_access(request.user, doc):\n"
                "+    if not user_can_access(request.user, doc.owner):\n"
                "         raise PermissionDenied()\n"
                "     return doc\n"
            ),
        }],
        repo_files={
            "app/permissions.py": (
                "def user_can_access(user, resource):\n"
                "    return resource.owner_id == user.id\n"
            ),
        },
        expected=[Expected(file="app/views.py", line=5, keyword="owner", min_severity="bug", line_tolerance=2)],
    ),
    Case(
        # Unlike cross_file_ownership_type_mismatch, there is NO visible red flag in
        # this diff -- swapping a manual date format for the standard isoformat() looks
        # like a strict improvement, arguably even better practice. The break only
        # exists because some other file (not shown, not even referenced by name here)
        # parses the old fixed-width output by slicing/regex. Nothing about this diff
        # should make a reviewer suspicious on its own -- a genuinely hard case for a
        # diff-only reviewer, expected to be MISSED today.
        name="silent_format_contract_break",
        files=[{
            "filename": "app/formatting.py",
            "status": "modified",
            "patch": (
                "@@ -1,3 +1,3 @@\n"
                " def format_timestamp(dt):\n"
                "-    return dt.strftime('%Y-%m-%d')\n"
                "+    return dt.isoformat()\n"
            ),
        }],
        expected=[Expected(file="app/formatting.py", line=2, keyword="format", line_tolerance=1)],
    ),
    Case(
        # A non-atomic check-then-act on a shared counter: read, compute, write as three
        # separate steps instead of one atomic INCR. Under concurrent requests, two
        # increments can read the same starting value and one gets lost. Nothing here is
        # syntactically wrong -- it requires reasoning about interleaved execution across
        # multiple callers, not reading code as a single linear sequence.
        name="race_condition_non_atomic_increment",
        files=[{
            "filename": "app/rate_limiter.py",
            "status": "modified",
            "patch": (
                "@@ -1,4 +1,5 @@\n"
                " def increment_request_count(redis_client, key):\n"
                "-    return redis_client.incr(key)\n"
                "+    current = redis_client.get(key) or 0\n"
                "+    redis_client.set(key, int(current) + 1)\n"
                "+    return int(current) + 1\n"
            ),
        }],
        expected=[Expected(file="app/rate_limiter.py", line=3, keyword="race", line_tolerance=2)],
    ),
    Case(
        # A pure local-variable rename inside a function body -- no public API change,
        # no behavior change. (An earlier version of this case renamed the function
        # itself, which the agent correctly flagged as a breaking-API-change concern --
        # that wasn't a false positive, it was a bad fixture.)
        name="clean_rename_refactor",
        files=[{
            "filename": "app/utils.py",
            "status": "modified",
            "patch": (
                "@@ -1,5 +1,5 @@\n"
                " def compute_total(items):\n"
                "-    s = 0\n"
                "+    total = 0\n"
                "     for item in items:\n"
                "-        s += item.price\n"
                "+        total += item.price\n"
                "-    return s\n"
                "+    return total\n"
            ),
        }],
        should_be_clean=True,
    ),
    Case(
        # A fully input-validated addition: handles both the non-numeric case (try/except)
        # and the non-positive case explicitly. (An earlier version skipped the
        # try/except -- the agent correctly caught the resulting unhandled ValueError,
        # which meant that fixture wasn't actually clean.)
        name="clean_validated_addition",
        files=[{
            "filename": "app/parsing.py",
            "status": "modified",
            "patch": (
                "@@ -1,1 +1,10 @@\n"
                " import json\n"
                "+\n"
                "+def parse_positive_int(raw: str) -> int:\n"
                "+    try:\n"
                "+        value = int(raw)\n"
                "+    except ValueError:\n"
                "+        raise ValueError(f\"expected a valid integer, got {raw!r}\") from None\n"
                "+    if value <= 0:\n"
                "+        raise ValueError(f\"expected a positive integer, got {value}\")\n"
                "+    return value\n"
            ),
        }],
        should_be_clean=True,
    ),
]


def _matches(comment: dict, exp: Expected) -> bool:
    return (
        comment["file"] == exp.file
        and abs(comment["line"] - exp.line) <= exp.line_tolerance
        and SEVERITY_RANK.get(comment["severity"], 0) >= SEVERITY_RANK[exp.min_severity]
        and exp.keyword.lower() in comment["comment"].lower()
    )


def _check_denylist() -> bool:
    """Direct, non-LLM check of the read_file security boundary -- no API calls."""
    denied = [".env", "app/.env.production", "id_rsa", "config/secrets.yml", ".ssh/id_ed25519", "aws/credentials.pem"]
    allowed = ["app/views.py", "app/permissions.py", "src/main.py", "README.md"]
    failures = [p for p in denied if not _is_denied_path(p)]
    failures += [p for p in allowed if _is_denied_path(p)]
    if failures:
        print(f"[FAIL] read_file denylist -- misclassified: {failures}")
        return False
    print("[PASS] read_file denylist -- sensitive paths blocked, normal paths allowed")
    return True


def run() -> None:
    _check_denylist()
    print()

    total_expected = 0
    total_caught = 0
    clean_cases = [c for c in CASES if c.should_be_clean]
    false_positive_cases = 0

    for case in CASES:
        read_file = _make_read_file(case.repo_files) if case.repo_files else None
        result = review_pr(case.files, read_file=read_file)
        comments = result.get("comments", [])

        if case.should_be_clean:
            flagged = [c for c in comments if SEVERITY_RANK.get(c["severity"], 0) >= SEVERITY_RANK["warning"]]
            if flagged:
                false_positive_cases += 1
                print(f"[FAIL] {case.name} -- expected no bug/warning findings, got {len(flagged)}")
                for c in flagged:
                    print(f"    unexpected: [{c['severity']}] {c['file']}:{c['line']} -- {c['comment']}")
            else:
                print(f"[PASS] {case.name}")
            continue

        for exp in case.expected:
            total_expected += 1
            caught = any(_matches(c, exp) for c in comments)
            total_caught += caught
            status = "PASS" if caught else "FAIL (missed)"
            print(f"[{status}] {case.name} -- expected '{exp.keyword}' near {exp.file}:{exp.line}")

    print("\n--- SUMMARY ---")
    print(f"Bugs caught:                      {total_caught}/{total_expected}")
    print(f"Clean diffs with false positives:  {false_positive_cases}/{len(clean_cases)}")


if __name__ == "__main__":
    run()
