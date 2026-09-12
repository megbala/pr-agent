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

from review_agent import review_pr  # noqa: E402

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


def run() -> None:
    total_expected = 0
    total_caught = 0
    clean_cases = [c for c in CASES if c.should_be_clean]
    false_positive_cases = 0

    for case in CASES:
        result = review_pr(case.files)
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
