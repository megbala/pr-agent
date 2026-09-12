"""
Layer 1 of testing: exercise the review agent's prompt + structured output with a
hardcoded fake diff. No GitHub API calls, no Actions, no waiting on CI.

Run:
    python local_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv

load_dotenv()  # reads ANTHROPIC_API_KEY from .env

from review_agent import review_pr  # noqa: E402

# A deliberately obvious bug (string-concatenated SQL) to sanity-check the agent
# actually catches something real before you point it at a live PR.
FAKE_FILES = [
    {
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
    }
]

if __name__ == "__main__":
    result = review_pr(FAKE_FILES)
    print("\n--- SUMMARY ---")
    print(result.get("summary"))
    print("\n--- COMMENTS ---")
    for c in result.get("comments", []):
        print(f"[{c['severity'].upper()}] {c['file']}:{c['line']} -- {c['comment']}")
        if c.get("suggestion"):
            print(f"  suggestion: {c['suggestion']}")
