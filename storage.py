import json
import os
from datetime import date
from pathlib import Path

# Where your data files live. Normally the project folder.
# Set PLANNER_DATA_DIR to use another folder, for example a demo with sample data.
DATA_DIR = Path(os.getenv("PLANNER_DATA_DIR", Path(__file__).parent))
TASKS_FILE = DATA_DIR / "tasks.json"
GOALS_FILE = DATA_DIR / "goals.json"


def load_tasks():
    if not TASKS_FILE.exists():
        return []
    text = TASKS_FILE.read_text(encoding="utf-8")
    return json.loads(text)


def save_tasks(tasks):
    text = json.dumps(tasks, indent=2)
    TASKS_FILE.write_text(text, encoding="utf-8")


def add_task(title, due=None, priority="medium", minutes=None, goal_id=None):
    """minutes = how long the task takes. goal_id = which goal it belongs to (None = a normal task)."""
    tasks = load_tasks()
    new_id = max([t["id"] for t in tasks], default=0) + 1
    task = {
        "id": new_id,
        "title": title,
        "due": due,
        "priority": priority,
        "done": False,
        "minutes": minutes,
        "goal_id": goal_id,
        "completed_on": None,
    }
    tasks.append(task)
    save_tasks(tasks)
    return task


def list_tasks(show_done=False):
    tasks = load_tasks()
    if show_done:
        return tasks
    return [t for t in tasks if not t["done"]]


def complete_task(task_id):
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["done"] = True
            t["completed_on"] = date.today().isoformat()  # Needed for the weekly review
            save_tasks(tasks)
            return True
    return False


def delete_task(task_id):
    tasks = load_tasks()
    remaining = [t for t in tasks if t["id"] != task_id]
    if len(remaining) == len(tasks):
        return False
    save_tasks(remaining)
    return True


def edit_task(task_id, title=None, due=None, priority=None):
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            if title:
                t["title"] = title
            if due:
                t["due"] = due
            if priority in ("low", "medium", "high"):
                t["priority"] = priority
            save_tasks(tasks)
            return t
    return None


# ---------- Goals: a big goal owns many small tasks ----------

def list_goals():
    if not GOALS_FILE.exists():
        return []
    return json.loads(GOALS_FILE.read_text(encoding="utf-8"))


def save_goals(goals):
    GOALS_FILE.write_text(json.dumps(goals, indent=2), encoding="utf-8")


def add_goal(title, deadline, minutes_per_day, study_days):
    """study_days = which weekdays you can work on it, 0 = Monday ... 6 = Sunday."""
    goals = list_goals()
    goal = {
        "id": max([g["id"] for g in goals], default=0) + 1,
        "title": title,
        "deadline": deadline,
        "minutes_per_day": minutes_per_day,
        "study_days": study_days,
        "created_on": date.today().isoformat(),
    }
    goals.append(goal)
    save_goals(goals)
    return goal


def delete_goal(goal_id):
    """Delete a goal and all of its tasks."""
    save_goals([g for g in list_goals() if g["id"] != goal_id])
    save_tasks([t for t in load_tasks() if t.get("goal_id") != goal_id])
