import textwrap
from pathlib import Path

import pytest

from uiflow.models import Step, Workflow, resolve_sub_workflows


def test_load_workflow(tmp_path: Path):
    content = textwrap.dedent(
        """
        name: Demo
        backend: web
        steps:
          - action: navigate
            url: "https://example.com"
          - action: click
            selector: "#go"
        """
    )
    path = tmp_path / "demo.yaml"
    path.write_text(content, encoding="utf-8")

    workflow = Workflow.load(path)

    assert workflow.name == "Demo"
    assert workflow.backend == "web"
    assert len(workflow.steps) == 2
    assert workflow.steps[0].action == "navigate"
    assert workflow.steps[0].params == {"url": "https://example.com"}
    assert workflow.steps[1].params == {"selector": "#go"}


def test_load_workflow_rejects_unknown_backend(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("name: Bad\nbackend: mobile\nsteps: []\n", encoding="utf-8")

    with pytest.raises(ValueError):
        Workflow.load(path)


def test_load_workflow_defaults_name_to_filename(tmp_path: Path):
    path = tmp_path / "unnamed.yaml"
    path.write_text("backend: web\nsteps: []\n", encoding="utf-8")

    workflow = Workflow.load(path)

    assert workflow.name == "unnamed"


def test_step_breakpoint_defaults_to_false_and_is_parsed_from_yaml():
    step = Step.from_dict({"action": "click", "selector": "#go"})
    assert step.breakpoint is False

    step = Step.from_dict({"action": "click", "selector": "#go", "breakpoint": True})
    assert step.breakpoint is True
    assert step.params == {"selector": "#go"}  # breakpoint must not leak into params


def test_workflow_to_dict_round_trips_breakpoint_only_when_set():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("navigate", {"url": "a"}),
            Step("click", {"selector": "#go"}, breakpoint=True),
        ],
    )

    data = workflow.to_dict()

    assert "breakpoint" not in data["steps"][0]
    assert data["steps"][1]["breakpoint"] is True

    reloaded = Workflow.from_raw(data)
    assert reloaded.steps[0].breakpoint is False
    assert reloaded.steps[1].breakpoint is True


def test_step_on_error_defaults_to_none_and_is_parsed_from_yaml():
    step = Step.from_dict({"action": "click", "selector": "#go"})
    assert step.on_error is None
    assert step.retry_count == 3
    assert step.retry_delay == 2.0

    step = Step.from_dict(
        {"action": "click", "selector": "#go", "on_error": "retry", "retry_count": 5, "retry_delay": 1.5}
    )
    assert step.on_error == "retry"
    assert step.retry_count == 5
    assert step.retry_delay == 1.5
    assert step.params == {"selector": "#go"}  # on_error fields must not leak into params


def test_step_rejects_unknown_on_error():
    with pytest.raises(ValueError):
        Step.from_dict({"action": "click", "selector": "#go", "on_error": "ignore"})


def test_workflow_to_dict_round_trips_on_error_only_when_set():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("navigate", {"url": "a"}),
            Step("click", {"selector": "#go"}, on_error="continue"),
            Step("click", {"selector": "#retry-me"}, on_error="retry", retry_count=4, retry_delay=3.0),
        ],
    )

    data = workflow.to_dict()

    assert "on_error" not in data["steps"][0]
    assert data["steps"][1]["on_error"] == "continue"
    assert "retry_count" not in data["steps"][1]  # only meaningful (and written) for retry
    assert data["steps"][2] == {
        "action": "click",
        "selector": "#retry-me",
        "on_error": "retry",
        "retry_count": 4,
        "retry_delay": 3.0,
    }

    reloaded = Workflow.from_raw(data)
    assert reloaded.steps[0].on_error is None
    assert reloaded.steps[1].on_error == "continue"
    assert reloaded.steps[2].retry_count == 4


# --- resolve_sub_workflows ---------------------------------------------------


@pytest.fixture
def workflows_dir(tmp_path, monkeypatch):
    """Points name-based workflow lookup at a temp directory."""
    monkeypatch.setattr("uiflow.models.WORKFLOWS_DIR", tmp_path)
    return tmp_path


def write_workflow(directory, name, steps, backend="web"):
    Workflow(name=name, backend=backend, steps=[Step.from_dict(s) for s in steps]).save(
        directory / f"{name}.yaml"
    )


def test_resolve_sub_workflows_finds_a_direct_reference(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "sub"}])
    workflow = Workflow(
        name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})]
    )

    resolved = resolve_sub_workflows(workflow)

    assert resolved == {
        "teilprozess": {"name": "teilprozess", "backend": "web", "steps": [{"action": "navigate", "url": "sub"}]}
    }


def test_resolve_sub_workflows_is_transitive(workflows_dir):
    write_workflow(workflows_dir, "b", [{"action": "navigate", "url": "b"}])
    write_workflow(workflows_dir, "a", [{"action": "run_workflow", "workflow": "b"}])
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "a"})])

    resolved = resolve_sub_workflows(workflow)

    assert set(resolved) == {"a", "b"}


def test_resolve_sub_workflows_finds_references_nested_in_branches(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "sub"}])
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step(
                "if",
                {
                    "condition": "True",
                    "then": [{"action": "run_workflow", "workflow": "teilprozess"}],
                },
            )
        ],
    )

    resolved = resolve_sub_workflows(workflow)

    assert "teilprozess" in resolved


def test_resolve_sub_workflows_skips_a_placeholder_name(workflows_dir):
    workflow = Workflow(
        name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "{var.ziel}"})]
    )

    resolved = resolve_sub_workflows(workflow)

    assert resolved == {}


def test_resolve_sub_workflows_skips_a_missing_file(workflows_dir):
    workflow = Workflow(
        name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "gibtsnicht"})]
    )

    resolved = resolve_sub_workflows(workflow)

    assert resolved == {}


def test_resolve_sub_workflows_does_not_loop_forever_on_a_cycle(workflows_dir):
    # a -> b -> a: a cycle between two *referenced* workflows, neither of which
    # is the top-level one being resolved from - this must terminate and still
    # capture both, instead of skipping "a" the way a self-reference would.
    write_workflow(workflows_dir, "a", [{"action": "run_workflow", "workflow": "b"}])
    write_workflow(workflows_dir, "b", [{"action": "run_workflow", "workflow": "a"}])
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "a"})])

    resolved = resolve_sub_workflows(workflow)  # must return instead of recursing forever

    assert set(resolved) == {"a", "b"}
