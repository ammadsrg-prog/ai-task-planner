"""AI governance: the rules and records that keep the AI agent safe, under control and checkable.

1. Audit log      - a record of every action the AI takes, so we can check later what happened.
2. Risky tools    - actions the AI may only REQUEST; a human must approve them.
3. Injection scan - finds text that tries to give the AI orders (a "prompt injection" attack).
The daily budget limit lives in bedrock.py, because every AI call passes through there.
"""

import json
import os
import re
from datetime import datetime

from dotenv import load_dotenv

from storage import DATA_DIR

load_dotenv()

AUDIT_FILE = DATA_DIR / "audit.jsonl"
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", "0.50"))

# Tools that can't be undone. The AI can ask for them, but only a human can say yes.
RISKY_TOOLS = {"delete_task"}


# ---------- 1. Audit log ----------

def log_event(event, **details):
    """Add one line to audit.jsonl. Example: log_event("approved", tool="delete_task", task_id=3)"""
    record = {"time": datetime.now().isoformat(timespec="seconds"), "event": event, **details}
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_audit(limit=200):
    """The newest events first."""
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in reversed(lines) if line.strip()][:limit]


# ---------- 3. Prompt-injection scan ----------
# Same idea as in the research assistant: each rule is a name + a regex pattern. (?i) = ignore upper/lower case.

INJECTION_PATTERNS = {
    "tries to cancel the AI's instructions": (
        r"(?i)\b(ignore|disregard|forget|override)\b(\s+\w+){0,3}\s+(instructions?|prompts?|rules|guidelines)"
    ),
    "tries to give the AI new orders": r"(?i)\b(new|updated|real)\s+instructions?\s*:",
    "tries to change the AI's role": r"(?i)\byou\s+are\s+(now|no\s+longer)\b",
    "tries to read the AI's hidden instructions": r"(?i)\b(system\s+prompt|reveal\s+(your|the)\s+(instructions|prompt))\b",
    "orders a mass action": r"(?i)\b(delete|remove|erase|complete)\s+(all|every|everything)\b",
    # Found by our live attack test: a polite "note" written to the AI got past the other rules
    "talks to the AI directly": r"(?i)\b(note|message|reminder|instruction)s?\s+(for|to)\s+(the\s+)?(assistant|ai|agent|model|bot)\b",
    "uses AI chat markers": r"(?i)(\[/?INST\]|<\|im_start\|>|^\s*(system|assistant)\s*:)",
    "contains hidden invisible characters": r"[​-‏⁠﻿\U000e0000-\U000e007f]",
}


def find_injection(text):
    """Return the names of the rules the text breaks. An empty list means it looks safe."""
    return [name for name, pattern in INJECTION_PATTERNS.items() if re.search(pattern, text or "", flags=re.MULTILINE)]


def safe_task_for_ai(task):
    """A copy of a task that is safe to show the AI. A title that looks like an attack is hidden,
    so the AI never even reads the attacker's orders. The real task is not changed."""
    reasons = find_injection(task["title"])
    if not reasons:
        return task
    log_event("injection_hidden", task_id=task["id"], reasons=reasons)
    return {**task, "title": "[title hidden by safety filter: it looked like instructions to the AI]"}
