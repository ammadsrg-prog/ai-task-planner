"""pytest runs this file before every test file. The fixture here applies to ALL tests."""

import pytest

import bedrock
import governance
import storage


@pytest.fixture(autouse=True)
def all_files_in_temp_folder(tmp_path, monkeypatch):
    """Every test gets fresh, empty files, so tests never touch your real tasks, costs or audit log."""
    monkeypatch.setattr(storage, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(storage, "GOALS_FILE", tmp_path / "goals.json")
    monkeypatch.setattr(bedrock, "COSTS_FILE", tmp_path / "costs.jsonl")
    monkeypatch.setattr(governance, "AUDIT_FILE", tmp_path / "audit.jsonl")
