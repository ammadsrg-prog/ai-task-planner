"""The goal planner: turns one big goal into a day-by-day plan, and fixes the plan when you miss days.

The key idea: the AI does what it is good at (breaking a goal into study steps, writing advice),
and Python does what the AI is bad at (dates, counting, fitting minutes into days).
"""

import json
from datetime import date, timedelta

from ai_parser import ask_bedrock, extract_json_list
from storage import add_goal, add_task, list_goals, load_tasks, save_tasks

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

PLAN_PROMPT = """You are a study and project coach. Break this goal into small, concrete steps, in the order they should be done.

Goal: {goal}
Time available: about {sessions} sessions of {minutes_per_day} minutes ({total_minutes} minutes in total).

Rules:
- Each step takes between 15 and {minutes_per_day} minutes.
- All steps together must fit in {total_minutes} minutes. Leave some time for review and practice at the end.
- Titles are short and specific, like "Read Bedrock Converse API docs" (not "Study").

Reply with ONLY a JSON list, no other text:
[{{"title": "step name", "minutes": 30}}, ...]"""

REVIEW_PROMPT = """You are a friendly productivity coach. Here are the user's real numbers from the last 7 days (JSON):
{stats}

Write a short weekly review (max 120 words):
1. One sentence on how the week went, using the numbers.
2. Two or three specific suggestions based on the patterns (for example, a weekday they often miss).
Do not invent numbers that are not in the data."""


# ---------- 1. Scheduling: pure Python, no AI ----------

def study_dates(start, deadline, study_days):
    """Every date from start to deadline that is one of the study days."""
    day = start
    while day <= deadline:
        if day.weekday() in study_days:
            yield day
        day += timedelta(days=1)


def schedule_steps(steps, start, deadline, minutes_per_day, study_days):
    """Put each step on a day, in order, without going over minutes_per_day.
    Returns (list of (step, date), number of steps that did not fit before the deadline)."""
    planned = []
    days = list(study_dates(start, deadline, study_days))
    i = 0
    for day in days:
        used = 0
        # "used == 0": a day always takes at least one step, even a long one, so nothing gets stuck
        while i < len(steps) and (used == 0 or used + steps[i]["minutes"] <= minutes_per_day):
            planned.append((steps[i], day))
            used += steps[i]["minutes"]
            i += 1
    overflow = len(steps) - i
    last_day = days[-1] if days else deadline
    for step in steps[i:]:  # Steps that didn't fit: put them on the last day and warn the user
        planned.append((step, last_day))
    return planned, overflow


# ---------- 2. Creating a goal plan: AI writes the steps, Python schedules them ----------

def create_goal(title, deadline, minutes_per_day, study_days, today=None):
    today = today or date.today()
    deadline_date = date.fromisoformat(deadline)
    if deadline_date <= today:
        raise ValueError("The deadline must be in the future.")
    if not study_days:
        raise ValueError("Choose at least one study day.")

    sessions = len(list(study_dates(today, deadline_date, study_days)))
    if sessions == 0:
        raise ValueError("There are no study days before the deadline.")
    # Ask for only 75% of the time: steps can't always fill a day perfectly (30 + 45 min don't fit in 60),
    # and real life needs some spare time for re-planning missed days.
    budget = int(sessions * minutes_per_day * 0.75)

    prompt = PLAN_PROMPT.format(goal=title, sessions=sessions, minutes_per_day=minutes_per_day, total_minutes=budget)
    raw_steps = extract_json_list(ask_bedrock(prompt, feature="goal_plan", max_tokens=3000))

    # Never trust the AI's numbers: keep every step between 15 minutes and the daily limit
    steps = []
    for s in raw_steps:
        try:
            minutes = int(s.get("minutes", 30))
        except (TypeError, ValueError):
            minutes = 30
        steps.append({"title": str(s["title"]), "minutes": max(15, min(minutes, minutes_per_day))})

    goal = add_goal(title, deadline, minutes_per_day, study_days)
    planned, overflow = schedule_steps(steps, today, deadline_date, minutes_per_day, study_days)
    tasks = [add_task(step["title"], day.isoformat(), "medium", step["minutes"], goal["id"]) for step, day in planned]
    return goal, tasks, overflow


# ---------- 3. Re-planning: move unfinished tasks forward when you miss days ----------

def goal_tasks(goal_id):
    return [t for t in load_tasks() if t.get("goal_id") == goal_id]


def missed_tasks(goal_id, today=None):
    today = (today or date.today()).isoformat()
    return [t for t in goal_tasks(goal_id) if not t["done"] and t["due"] and t["due"] < today]


def replan_goal(goal_id, today=None):
    """Spread all unfinished tasks of a goal again, starting today. Pure Python: free and instant.
    Returns (how many tasks got a new date, how many no longer fit before the deadline)."""
    today = today or date.today()
    goal = next(g for g in list_goals() if g["id"] == goal_id)

    all_tasks = load_tasks()
    todo = [t for t in all_tasks if t.get("goal_id") == goal_id and not t["done"]]
    todo.sort(key=lambda t: (t["due"] or "9999", t["id"]))  # Keep the original order
    steps = [{"id": t["id"], "minutes": t.get("minutes") or 30} for t in todo]

    planned, overflow = schedule_steps(
        steps, today, date.fromisoformat(goal["deadline"]), goal["minutes_per_day"], goal["study_days"]
    )
    new_dates = {step["id"]: day.isoformat() for step, day in planned}

    moved = 0
    for t in all_tasks:
        if t["id"] in new_dates and t["due"] != new_dates[t["id"]]:
            t["due"] = new_dates[t["id"]]
            moved += 1
    save_tasks(all_tasks)
    return moved, overflow


def goal_progress(goal_id, today=None):
    tasks = goal_tasks(goal_id)
    done = [t for t in tasks if t["done"]]
    total_minutes = sum(t.get("minutes") or 0 for t in tasks)
    done_minutes = sum(t.get("minutes") or 0 for t in done)
    return {
        "tasks_done": len(done),
        "tasks_total": len(tasks),
        "percent": round(100 * done_minutes / total_minutes) if total_minutes else 0,
        "missed": len(missed_tasks(goal_id, today)),
    }


# ---------- 4. Weekly review: Python counts, AI writes the advice ----------

def week_stats(today=None):
    """Numbers for the last 7 days (not counting today). Everything here is counted by Python."""
    today = today or date.today()
    week_start = (today - timedelta(days=7)).isoformat()
    week_end = today.isoformat()

    due_this_week = [t for t in load_tasks() if t["due"] and week_start <= t["due"] < week_end]
    per_day = {name: {"done": 0, "missed": 0} for name in WEEKDAYS}
    for t in due_this_week:
        name = WEEKDAYS[date.fromisoformat(t["due"]).weekday()]
        per_day[name]["done" if t["done"] else "missed"] += 1

    done = sum(1 for t in due_this_week if t["done"])
    return {
        "tasks_due": len(due_this_week),
        "tasks_done": done,
        "tasks_missed": len(due_this_week) - done,
        "done_percent": round(100 * done / len(due_this_week)) if due_this_week else 0,
        "per_weekday": {name: d for name, d in per_day.items() if d["done"] or d["missed"]},
        "goals": [
            {"goal": g["title"], "deadline": g["deadline"], **goal_progress(g["id"], today)}
            for g in list_goals()
        ],
    }


def weekly_review(today=None):
    stats = week_stats(today)
    if stats["tasks_due"] == 0:
        # No data = no AI call. This saves money and stops the AI from making things up.
        return stats, "No tasks were due in the last 7 days, so there is nothing to review yet."
    text = ask_bedrock(REVIEW_PROMPT.format(stats=json.dumps(stats, indent=2)), feature="weekly_review", max_tokens=400)
    return stats, text
