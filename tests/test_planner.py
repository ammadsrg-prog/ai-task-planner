"""Tests for the goal planner and the cost tracker. A FAKE AI is used, so they are free and offline."""

from datetime import date

import pytest

import bedrock
import governance
import planner
import storage

MONDAY = date(2026, 11, 23)  # A fixed "today", so the tests give the same result every day


@pytest.fixture(autouse=True)
def temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(storage, "GOALS_FILE", tmp_path / "goals.json")
    monkeypatch.setattr(bedrock, "COSTS_FILE", tmp_path / "costs.jsonl")


def fake_ai(answer):
    """Replace the real AI with one that always gives `answer`."""
    return lambda prompt, feature="parse", max_tokens=500: answer


# ---------- Scheduling (pure Python) ----------

def test_study_dates_only_uses_chosen_weekdays():
    days = list(planner.study_dates(MONDAY, date(2026, 11, 29), [0, 2, 4]))  # Mon, Wed, Fri
    assert [d.isoformat() for d in days] == ["2026-11-23", "2026-11-25", "2026-11-27"]


def test_schedule_respects_minutes_per_day():
    steps = [{"title": f"step {i}", "minutes": 30} for i in range(4)]
    planned, overflow = planner.schedule_steps(steps, MONDAY, date(2026, 11, 30), 60, [0, 1, 2, 3, 4])
    dates = [d.isoformat() for _, d in planned]
    assert dates == ["2026-11-23", "2026-11-23", "2026-11-24", "2026-11-24"]  # Two 30-min steps per day
    assert overflow == 0


def test_schedule_reports_steps_that_do_not_fit():
    steps = [{"title": f"step {i}", "minutes": 60} for i in range(5)]
    planned, overflow = planner.schedule_steps(steps, MONDAY, date(2026, 11, 24), 60, [0, 1, 2, 3, 4, 5, 6])
    assert overflow == 3  # Only 2 days x 60 minutes before the deadline
    assert len(planned) == 5  # Nothing is lost: the extra steps go on the last day


def test_schedule_never_gets_stuck_on_a_long_step():
    planned, overflow = planner.schedule_steps([{"title": "long", "minutes": 200}], MONDAY, MONDAY, 60, [0])
    assert overflow == 0 and len(planned) == 1


# ---------- Creating a goal ----------

def test_create_goal_makes_scheduled_tasks(monkeypatch):
    answer = 'Here: [{"title": "Read docs", "minutes": 45}, {"title": "Practice", "minutes": 999}, {"title": "Quiz", "minutes": "abc"}]'
    monkeypatch.setattr(planner, "ask_bedrock", fake_ai(answer))

    goal, tasks, overflow = planner.create_goal("Learn Bedrock", "2026-11-30", 60, [0, 1, 2, 3, 4], today=MONDAY)

    assert goal["title"] == "Learn Bedrock"
    assert [t["title"] for t in tasks] == ["Read docs", "Practice", "Quiz"]
    assert [t["minutes"] for t in tasks] == [45, 60, 30]  # 999 was cut to the daily limit, "abc" became 30
    assert all(t["goal_id"] == goal["id"] for t in tasks)
    assert tasks[0]["due"] == "2026-11-23"


@pytest.mark.parametrize("deadline, days", [("2026-11-23", [0]), ("2026-11-20", [0]), ("2026-11-30", [])])
def test_create_goal_rejects_bad_input(monkeypatch, deadline, days):
    monkeypatch.setattr(planner, "ask_bedrock", fake_ai("[]"))
    with pytest.raises(ValueError):
        planner.create_goal("x", deadline, 60, days, today=MONDAY)


# ---------- Re-planning ----------

def make_goal_with_tasks():
    goal = storage.add_goal("Learn", "2026-12-04", 60, [0, 1, 2, 3, 4])
    for i, due in enumerate(["2026-11-23", "2026-11-24", "2026-11-25"]):
        storage.add_task(f"step {i}", due, "medium", 60, goal["id"])
    return goal


def test_missed_tasks_and_progress():
    goal = make_goal_with_tasks()
    storage.complete_task(1)
    wednesday = date(2026, 11, 25)
    assert [t["title"] for t in planner.missed_tasks(goal["id"], wednesday)] == ["step 1"]
    progress = planner.goal_progress(goal["id"], wednesday)
    assert progress["tasks_done"] == 1 and progress["missed"] == 1 and progress["percent"] == 33


def test_replan_moves_missed_tasks_forward_in_order():
    goal = make_goal_with_tasks()
    storage.complete_task(1)
    thursday = date(2026, 11, 26)  # step 1 and step 2 were missed

    moved, overflow = planner.replan_goal(goal["id"], thursday)

    todo = [t for t in planner.goal_tasks(goal["id"]) if not t["done"]]
    assert [(t["title"], t["due"]) for t in todo] == [("step 1", "2026-11-26"), ("step 2", "2026-11-27")]
    assert moved == 2 and overflow == 0
    assert planner.goal_tasks(goal["id"])[0]["due"] == "2026-11-23"  # Finished tasks stay where they were


def test_delete_goal_removes_its_tasks_only():
    goal = make_goal_with_tasks()
    storage.add_task("normal task")
    storage.delete_goal(goal["id"])
    assert storage.list_goals() == []
    assert [t["title"] for t in storage.list_tasks()] == ["normal task"]


# ---------- Weekly review ----------

def test_week_stats_counts_done_and_missed_per_weekday():
    make_goal_with_tasks()
    storage.complete_task(1)
    stats = planner.week_stats(date(2026, 11, 26))
    assert stats["tasks_due"] == 3 and stats["tasks_done"] == 1 and stats["done_percent"] == 33
    assert stats["per_weekday"]["Monday"] == {"done": 1, "missed": 0}
    assert stats["per_weekday"]["Tuesday"] == {"done": 0, "missed": 1}


def test_weekly_review_skips_the_ai_when_there_is_no_data(monkeypatch):
    def must_not_be_called(*args, **kwargs):
        raise AssertionError("The AI should not be called without data")
    monkeypatch.setattr(planner, "ask_bedrock", must_not_be_called)
    stats, text = planner.weekly_review(MONDAY)
    assert "nothing to review" in text


# ---------- Cost tracker ----------

class FakeClient:
    def converse(self, **request):
        return {"usage": {"inputTokens": 1_000_000, "outputTokens": 1_000_000},
                "output": {"message": {"content": [{"text": "hi"}]}}}


def test_every_ai_call_is_logged_with_its_cost(monkeypatch):
    monkeypatch.setattr(governance, "DAILY_BUDGET_USD", 100)  # Each fake call costs $2, over the normal limit
    monkeypatch.setattr(bedrock.boto3, "client", lambda *a, **k: FakeClient())
    bedrock.call_bedrock("chat", messages=[])
    bedrock.call_bedrock("goal_plan", messages=[])

    summary = bedrock.cost_summary()
    assert summary["calls"] == 2
    expected_one_call = bedrock.PRICE_INPUT + bedrock.PRICE_OUTPUT  # 1M tokens of each
    assert summary["total_usd"] == pytest.approx(2 * expected_one_call)
    assert summary["by_feature"]["chat"]["calls"] == 1
