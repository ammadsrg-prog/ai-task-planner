"""Tests for the AI parts. They use a FAKE Bedrock, so they are free, fast and work offline."""

from datetime import date

import pytest

import agent
import ai_parser
import bedrock
import storage


@pytest.fixture(autouse=True)
def temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(storage, "GOALS_FILE", tmp_path / "goals.json")
    monkeypatch.setattr(bedrock, "COSTS_FILE", tmp_path / "costs.jsonl")


# ---------- Date helpers ----------

def test_clean_due_accepts_real_dates():
    assert ai_parser.clean_due("2026-11-27") == "2026-11-27"


@pytest.mark.parametrize("bad", [None, "", "Friday", "2026-13-40", "27/11/2026"])
def test_clean_due_rejects_bad_dates(bad):
    assert ai_parser.clean_due(bad) is None


def test_calendar_starts_today_and_marks_tomorrow():
    lines = ai_parser.make_calendar().splitlines()
    assert len(lines) == 14
    assert date.today().isoformat() in lines[0] and "(today)" in lines[0]
    assert "(tomorrow)" in lines[1]


# ---------- parse_tasks with a fake AI answer ----------

def test_parse_tasks_reads_json_and_cleans_it(monkeypatch):
    fake_answer = 'Sure! [{"title": "Gym", "due": "tomorrow", "priority": "URGENT"}] Hope this helps'
    monkeypatch.setattr(ai_parser, "ask_bedrock", lambda prompt: fake_answer)
    assert ai_parser.parse_tasks("gym tomorrow") == [{"title": "Gym", "due": None, "priority": "medium"}]


def test_parse_tasks_without_json_raises(monkeypatch):
    monkeypatch.setattr(ai_parser, "ask_bedrock", lambda prompt: "I don't understand")
    with pytest.raises(ValueError):
        ai_parser.parse_tasks("???")


# ---------- run_tool: the agent's hands ----------

def test_run_tool_add_and_list():
    agent.run_tool("add_task", {"title": "Gym", "due": "not a date", "priority": "high"})
    tasks = agent.run_tool("list_tasks", {})
    assert tasks[0]["title"] == "Gym"
    assert tasks[0]["due"] is None  # The bad date was rejected


def test_run_tool_unknown_tool():
    assert "error" in agent.run_tool("format_hard_drive", {})


def test_run_tool_edit_unknown_id():
    assert "error" in agent.run_tool("edit_task", {"task_id": 42, "title": "x"})


# ---------- The agent loop with a fake Bedrock ----------

class FakeBedrock:
    """Pretends to be Bedrock: first asks to use add_task, then gives a final answer."""

    def __init__(self):
        self.calls = 0

    def converse(self, **request):
        self.calls += 1
        if self.calls == 1:
            return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "t1", "name": "add_task", "input": {"title": "Gym"}}},
            ]}}}
        return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [
            {"text": "Added Gym."},
        ]}}}


def test_agent_loop_runs_tool_then_answers(monkeypatch):
    fake = FakeBedrock()
    monkeypatch.setattr(bedrock.boto3, "client", lambda *args, **kwargs: fake)
    history = []
    used = []

    reply = agent.chat("add gym", history, on_tool=lambda name, tool_input: used.append(name))

    assert reply == "Added Gym."
    assert used == ["add_task"]
    assert storage.list_tasks()[0]["title"] == "Gym"  # The tool really ran
    assert len(history) == 4  # user, AI tool request, tool result, AI answer = the memory


def test_agent_stops_after_max_steps(monkeypatch):
    class LoopingBedrock(FakeBedrock):
        def converse(self, **request):
            self.calls = 0  # Always ask for a tool again: an endless loop
            return super().converse(**request)

    monkeypatch.setattr(bedrock.boto3, "client", lambda *args, **kwargs: LoopingBedrock())
    reply = agent.chat("loop forever", [], max_steps=3, on_tool=lambda *a: None)
    assert "too many steps" in reply
    assert len(storage.list_tasks()) == 3  # Stopped after 3 rounds, not forever


# ---------- Memory trimming ----------

def test_trim_history_keeps_limit_and_starts_with_user_text():
    history = []
    for i in range(15):
        history.append({"role": "user", "content": [{"text": f"message {i}"}]})
        history.append({"role": "assistant", "content": [{"toolUse": {"toolUseId": str(i)}}]})
        history.append({"role": "user", "content": [{"toolResult": {"toolUseId": str(i)}}]})
    agent.trim_history(history)
    assert len(history) <= agent.MAX_HISTORY
    assert "text" in history[0]["content"][0]
