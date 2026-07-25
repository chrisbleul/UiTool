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
        {
            "scope": "A-App",
            "name": "Element",
            "fields": {"selector": "#a"},
            "candidates": [{"selector": "#a"}],
        },
        {
            "scope": "B-App",
            "name": "Z-Element",
            "fields": {"selector": "#z"},
            "candidates": [{"selector": "#z"}],
        },
    ]


# --- fallback candidates -----------------------------------------------------


def test_add_fallback_appends_to_an_existing_single_candidate_element():
    repo.set_element("MeineApp", "Suchfeld", {"selector": "#search"})

    repo.add_fallback("MeineApp", "Suchfeld", {"selector": "input[name=q]"})

    assert repo.get_element_candidates("MeineApp", "Suchfeld") == [
        {"selector": "#search"},
        {"selector": "input[name=q]"},
    ]
    assert repo.get_element("MeineApp", "Suchfeld") == {"selector": "#search"}  # still the primary one


def test_add_fallback_creates_the_element_if_it_does_not_exist_yet():
    repo.add_fallback("MeineApp", "Neu", {"selector": "#neu"})

    assert repo.get_element_candidates("MeineApp", "Neu") == [{"selector": "#neu"}]


def test_get_element_candidates_wraps_a_legacy_single_dict_entry():
    # A file written before fallbacks existed stores the fields dict directly,
    # not a list - loading it must still work.
    repo.save_repository({"MeineApp": {"Suchfeld": {"selector": "#search"}}})

    assert repo.get_element_candidates("MeineApp", "Suchfeld") == [{"selector": "#search"}]
    assert repo.get_element("MeineApp", "Suchfeld") == {"selector": "#search"}


def test_remove_candidate_by_index():
    repo.set_element("MeineApp", "Suchfeld", {"selector": "#a"})
    repo.add_fallback("MeineApp", "Suchfeld", {"selector": "#b"})
    repo.add_fallback("MeineApp", "Suchfeld", {"selector": "#c"})

    repo.remove_candidate("MeineApp", "Suchfeld", 1)

    assert repo.get_element_candidates("MeineApp", "Suchfeld") == [{"selector": "#a"}, {"selector": "#c"}]


def test_remove_candidate_deletes_the_element_once_its_last_candidate_is_gone():
    repo.set_element("MeineApp", "Suchfeld", {"selector": "#a"})

    repo.remove_candidate("MeineApp", "Suchfeld", 0)

    assert repo.get_element_candidates("MeineApp", "Suchfeld") == []
    assert repo.load_repository() == {}


def test_remove_candidate_ignores_an_out_of_range_index():
    repo.set_element("MeineApp", "Suchfeld", {"selector": "#a"})

    repo.remove_candidate("MeineApp", "Suchfeld", 5)

    assert repo.get_element_candidates("MeineApp", "Suchfeld") == [{"selector": "#a"}]
