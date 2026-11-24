import pytest

import storage


@pytest.fixture(autouse=True)
def temp_tasks_file(tmp_path, monkeypatch):
    """Use a fresh, empty file for every test, so tests never touch your real tasks.json."""
    monkeypatch.setattr(storage, "TASKS_FILE", tmp_path / "tasks.json")


def test_starts_empty():
    assert storage.list_tasks() == []


def test_add_task_gives_increasing_ids():
    first = storage.add_task("Gym")
    second = storage.add_task("Study", "2026-11-27", "high")
    assert first["id"] == 1
    assert second["id"] == 2
    assert second["priority"] == "high"
    assert second["done"] is False


def test_tasks_are_saved_to_the_file():
    storage.add_task("Gym")
    assert storage.TASKS_FILE.exists()
    assert storage.load_tasks()[0]["title"] == "Gym"


def test_complete_task_hides_it_from_the_list():
    storage.add_task("Gym")
    assert storage.complete_task(1) is True
    assert storage.list_tasks() == []
    assert len(storage.list_tasks(show_done=True)) == 1


def test_complete_unknown_id_returns_false():
    assert storage.complete_task(99) is False


def test_delete_task():
    storage.add_task("Gym")
    storage.add_task("Study")
    assert storage.delete_task(1) is True
    assert [t["title"] for t in storage.list_tasks()] == ["Study"]
    assert storage.delete_task(1) is False


def test_new_id_comes_after_the_highest_id():
    storage.add_task("Gym")
    storage.add_task("Study")
    storage.delete_task(1)
    assert storage.add_task("Read")["id"] == 3


def test_edit_task_changes_only_given_fields():
    storage.add_task("Gym", "2026-11-22", "low")
    edited = storage.edit_task(1, priority="high")
    assert edited["priority"] == "high"
    assert edited["title"] == "Gym"
    assert edited["due"] == "2026-11-22"


def test_edit_task_ignores_bad_priority():
    storage.add_task("Gym")
    assert storage.edit_task(1, priority="super urgent")["priority"] == "medium"


def test_edit_unknown_id_returns_none():
    assert storage.edit_task(99, title="x") is None
