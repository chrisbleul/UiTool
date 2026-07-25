import pytest

from uiflow import object_repository as repo


@pytest.fixture(autouse=True)
def isolated_repository(tmp_path, monkeypatch):
    monkeypatch.setattr("uiflow.object_repository.WORKFLOWS_DIR", tmp_path)
    return tmp_path


def test_load_repository_returns_empty_dict_when_no_file_exists():
    assert repo.load_repository() == {}


def test_set_and_get_element_round_trip():
    repo.set_element("Editor", "Anmelden-Knopf", {"control_type": "Button", "auto_id": "btnOK"})

    assert repo.get_element("Editor", "Anmelden-Knopf") == {"control_type": "Button", "auto_id": "btnOK"}
    assert repo.get_element("Editor", "gibtsnicht") is None
    assert repo.get_element("AndereApp", "Anmelden-Knopf") is None


def test_set_element_persists_across_reload(tmp_path):
    repo.set_element("Editor", "Feld", {"selector": "#x"})

    assert repo.repository_path().exists()
    assert repo.get_element("Editor", "Feld") == {"selector": "#x"}


def test_set_element_requires_scope_and_name():
    with pytest.raises(ValueError):
        repo.set_element("", "Feld", {"selector": "#x"})
    with pytest.raises(ValueError):
        repo.set_element("Editor", "", {"selector": "#x"})


def test_delete_element_removes_it_and_prunes_empty_scope():
    repo.set_element("Editor", "Feld", {"selector": "#x"})

    repo.delete_element("Editor", "Feld")

    assert repo.get_element("Editor", "Feld") is None
    assert repo.load_repository() == {}  # the now-empty scope is pruned, not left as {}


def test_delete_element_is_a_no_op_for_an_unknown_scope_or_name():
    repo.set_element("Editor", "Feld", {"selector": "#x"})

    repo.delete_element("AndereApp", "Feld")
    repo.delete_element("Editor", "gibtsnicht")

    assert repo.get_element("Editor", "Feld") == {"selector": "#x"}


def test_list_elements_returns_a_sorted_flat_list():
    repo.set_element("B-App", "Z-Element", {"selector": "#z"})
    repo.set_element("A-App", "Element", {"selector": "#a"})

    entries = repo.list_elements()

    assert entries == [
        {"scope": "A-App", "name": "Element", "fields": {"selector": "#a"}},
        {"scope": "B-App", "name": "Z-Element", "fields": {"selector": "#z"}},
    ]
