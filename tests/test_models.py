import textwrap
from pathlib import Path

import pytest

from uiflow.models import Step, Workflow


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
