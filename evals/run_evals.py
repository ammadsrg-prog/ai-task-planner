"""Accuracy test for the AI task parser.

It sends every sentence in cases.json to the real AI and checks the answer against the correct one.
Run it from the project folder:   .venv\\Scripts\\python.exe evals\\run_evals.py
Each run makes about 20 small AI calls (a fraction of a cent).
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # So we can import the project's files
from ai_parser import parse_tasks  # noqa: E402

HERE = Path(__file__).parent
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def expected_date(word, today):
    """Turn "tomorrow", "friday" or "+3" into a real date. Python works out the correct answer."""
    if word is None:
        return None
    if word == "today":
        return today.isoformat()
    if word == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if word.startswith("+"):
        return (today + timedelta(days=int(word[1:]))).isoformat()
    days_ahead = (WEEKDAYS.index(word) - today.weekday()) % 7  # The first such weekday, from today
    return (today + timedelta(days=days_ahead)).isoformat()


def check_case(case, today):
    """Returns a list of problems. An empty list means the AI got everything right."""
    try:
        got = parse_tasks(case["sentence"])
    except Exception as error:
        return [f"crashed: {error}"], []

    problems = []
    if len(got) != len(case["expected"]):
        problems.append(f"expected {len(case['expected'])} task(s), got {len(got)}")
    for want, task in zip(case["expected"], got):
        due = expected_date(want["due"], today)
        if task["due"] != due:
            problems.append(f"'{task['title']}': due {task['due']}, expected {due} ({want['due']})")
        if "priority" in want and task["priority"] != want["priority"]:
            problems.append(f"'{task['title']}': priority {task['priority']}, expected {want['priority']}")
    return problems, got


def main():
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    today = date.today()
    results = []

    for i, case in enumerate(cases, 1):
        problems, got = check_case(case, today)
        status = "PASS" if not problems else "FAIL"
        print(f"[{status}] {i:2}. {case['sentence']}")
        for p in problems:
            print(f"         - {p}")
        results.append({"sentence": case["sentence"], "passed": not problems, "problems": problems, "got": got})

    passed = sum(r["passed"] for r in results)
    score = round(100 * passed / len(results))
    print(f"\nScore: {passed}/{len(results)} = {score}%")

    report = {"run_at": datetime.now().isoformat(timespec="seconds"), "today": today.isoformat(),
              "passed": passed, "total": len(results), "score_percent": score, "results": results}
    (HERE / "last_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Saved: evals/last_report.json")


if __name__ == "__main__":
    main()
