import json
from datetime import date, timedelta

from bedrock import call_bedrock

PROMPT = """You turn a sentence into a list of tasks.
{date_rules}

Reply with ONLY a JSON list, no other text. Each task looks like this:
{{"title": "short task name", "due": "YYYY-MM-DD or null", "priority": "low, medium or high"}}

If no date is mentioned, use null. If no priority is clear, use "medium".

Sentence: {sentence}"""


def make_calendar(days=14):
    today = date.today()
    lines = []
    for i in range(days):
        day = today + timedelta(days=i)
        label = "today" if i == 0 else "tomorrow" if i == 1 else f"in {i} days"
        lines.append(f"{day.strftime('%A')} = {day.isoformat()} ({label})")
    return "\n".join(lines)


def date_rules():
    """The date instructions + calendar. Used by both the parser and the agent, so they behave the same."""
    return f"""Dates: NEVER calculate a date yourself. Always copy a date from this calendar:
{make_calendar()}

How to find the right line:
- "today", "tomorrow", "in 3 days": use the line with that label.
- A weekday name ("Friday", "by Sunday", "on Monday"): use the FIRST line with that weekday,
  even if it is today or tomorrow. Only use the second one if the user says "next week"."""


def clean_due(due):
    """Return the date if it is a real YYYY-MM-DD date, otherwise None. Never trust the AI's format."""
    try:
        return date.fromisoformat(due).isoformat()
    except (TypeError, ValueError):
        return None


def ask_bedrock(prompt, feature="parse", max_tokens=500):
    response = call_bedrock(
        feature,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
    )
    return response["output"]["message"]["content"][0]["text"]


def extract_json_list(answer):
    """AI answers sometimes add words around the JSON. Keep only the part from the first [ to the last ]."""
    start = answer.find("[")
    end = answer.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError("The AI did not return a JSON list.")
    return json.loads(answer[start:end])


def parse_tasks(sentence):
    prompt = PROMPT.format(date_rules=date_rules(), sentence=sentence)
    tasks = extract_json_list(ask_bedrock(prompt))

    clean = []
    for t in tasks:
        priority = t.get("priority", "medium")
        if priority not in ("low", "medium", "high"):
            priority = "medium"
        clean.append({"title": str(t["title"]), "due": clean_due(t.get("due")), "priority": priority})
    return clean
