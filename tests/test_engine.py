import logging

import pytest

from uiflow.engine import StepError, WorkflowCancelled, WorkflowEngine, substitute_variables
from uiflow.models import Step, Workflow


class RecordingBackend:
    def __init__(self):
        self.calls = []

    def navigate(self, url):
        self.calls.append(("navigate", url))

    def click(self, selector):
        self.calls.append(("click", selector))


def test_engine_dispatches_steps_in_order():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("navigate", {"url": "https://example.com"}),
            Step("click", {"selector": "#go"}),
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [
        ("navigate", "https://example.com"),
        ("click", "#go"),
    ]


def test_engine_raises_step_error_for_unknown_action():
    workflow = Workflow(name="t", backend="web", steps=[Step("does_not_exist", {})])

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert excinfo.value.index == 1


def test_engine_wraps_backend_exception_with_step_context():
    class FailingBackend:
        def navigate(self, url):
            raise RuntimeError("boom")

    workflow = Workflow(name="t", backend="web", steps=[Step("navigate", {"url": "x"})])

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(FailingBackend()).run(workflow)

    assert isinstance(excinfo.value.original, RuntimeError)
    assert "boom" in str(excinfo.value)


def test_engine_invokes_on_breakpoint_before_the_flagged_step():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("navigate", {"url": "a"}),
            Step("click", {"selector": "#go"}, breakpoint=True),
        ],
    )
    backend = RecordingBackend()
    seen = []

    def on_breakpoint(index, step, variables, path):
        seen.append((index, step.action))
        assert backend.calls == [("navigate", "a")]  # not yet executed

    WorkflowEngine(backend).run(workflow, on_breakpoint=on_breakpoint)

    assert seen == [(2, "click")]
    assert backend.calls == [("navigate", "a"), ("click", "#go")]


def test_on_breakpoint_receives_a_snapshot_of_current_variables():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("assign", {"variable": "x", "value": "42"}), Step("navigate", {"url": "a"}, breakpoint=True)],
    )
    seen = {}

    def on_breakpoint(index, step, variables, path):
        seen.update(variables)

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint)

    assert seen == {"x": "42"}


def test_engine_ignores_breakpoint_without_a_callback():
    workflow = Workflow(name="t", backend="web", steps=[Step("navigate", {"url": "a"}, breakpoint=True)])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)  # must not raise/block

    assert backend.calls == [("navigate", "a")]


def test_engine_stops_before_the_next_step_when_should_stop_is_true():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("navigate", {"url": "a"}),
            Step("click", {"selector": "#go"}),
        ],
    )
    backend = RecordingBackend()
    calls_so_far = []

    def should_stop():
        calls_so_far.append(len(backend.calls))
        return len(backend.calls) >= 1  # stop once the first step has run

    with pytest.raises(WorkflowCancelled) as excinfo:
        WorkflowEngine(backend).run(workflow, should_stop=should_stop)

    assert excinfo.value.index == 2
    assert backend.calls == [("navigate", "a")]  # second step never ran


def test_engine_stops_immediately_after_a_breakpoint_if_requested():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("navigate", {"url": "a"}, breakpoint=True)],
    )
    backend = RecordingBackend()
    stop_after_breakpoint = {"value": False}

    def on_breakpoint(index, step, variables, path):
        stop_after_breakpoint["value"] = True

    with pytest.raises(WorkflowCancelled):
        WorkflowEngine(backend).run(
            workflow,
            on_breakpoint=on_breakpoint,
            should_stop=lambda: stop_after_breakpoint["value"],
        )

    assert backend.calls == []  # the breakpointed step itself never ran


def test_substitute_variables_resolves_item_and_var_namespaces():
    result = substitute_variables(
        "Hello {var.name}, item id {item.id}!", {"name": "World", "item": {"id": "42"}}
    )
    assert result == "Hello World, item id 42!"


def test_substitute_variables_unmatched_placeholder_is_blank():
    assert substitute_variables("{var.missing}", {}) == ""


def test_save_as_stores_backend_return_value_as_a_variable():
    class GetTextBackend:
        def get_text(self, selector):
            return "extracted value"

    workflow = Workflow(
        name="t", backend="web", steps=[Step("get_text", {"selector": "#x"}, save_as="captured")]
    )
    engine = WorkflowEngine(GetTextBackend())

    engine.run(workflow)

    assert engine.variables["captured"] == "extracted value"


def test_backend_step_params_are_substituted_from_variables():
    workflow = Workflow(
        name="t", backend="web", steps=[Step("navigate", {"url": "https://example.com/{var.path}"})]
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"path": "abc"})

    assert backend.calls == [("navigate", "https://example.com/abc")]


def test_if_runs_then_branch_when_condition_true():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "if",
                {
                    "condition": "status == 'ok'",
                    "then": [{"action": "navigate", "url": "then-branch"}],
                    "else": [{"action": "navigate", "url": "else-branch"}],
                },
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"status": "ok"})

    assert backend.calls == [("navigate", "then-branch")]


def test_if_runs_else_branch_when_condition_false():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "if",
                {
                    "condition": "status == 'ok'",
                    "then": [{"action": "navigate", "url": "then-branch"}],
                    "else": [{"action": "navigate", "url": "else-branch"}],
                },
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"status": "broken"})

    assert backend.calls == [("navigate", "else-branch")]


def test_if_without_else_is_a_noop_when_condition_false():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("if", {"condition": "False", "then": [{"action": "navigate", "url": "x"}]}),
            Step("navigate", {"url": "after"}),
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "after")]


def test_switch_runs_matching_case():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "switch",
                {
                    "expression": "country",
                    "cases": {
                        "DE": [{"action": "navigate", "url": "de"}],
                        "US": [{"action": "navigate", "url": "us"}],
                    },
                    "default": [{"action": "navigate", "url": "fallback"}],
                },
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"country": "US"})

    assert backend.calls == [("navigate", "us")]


def test_switch_runs_default_when_no_case_matches():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "switch",
                {
                    "expression": "country",
                    "cases": {"DE": [{"action": "navigate", "url": "de"}]},
                    "default": [{"action": "navigate", "url": "fallback"}],
                },
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"country": "FR"})

    assert backend.calls == [("navigate", "fallback")]


def test_nested_if_inside_if_branch():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "if",
                {
                    "condition": "a",
                    "then": [
                        {
                            "action": "if",
                            "condition": "b",
                            "then": [{"action": "navigate", "url": "a-and-b"}],
                        }
                    ],
                },
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"a": True, "b": True})

    assert backend.calls == [("navigate", "a-and-b")]


def test_assign_sets_a_literal_value_with_substitution():
    workflow = Workflow(
        name="t", backend="web", steps=[Step("assign", {"variable": "greeting", "value": "Hi {var.name}"})]
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"name": "Sam"})

    assert engine.variables["greeting"] == "Hi Sam"


def test_assign_sets_a_computed_expression_value():
    workflow = Workflow(
        name="t", backend="web", steps=[Step("assign", {"variable": "total", "expression": "a + b"})]
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"a": 2, "b": 3})

    assert engine.variables["total"] == 5


def test_increment_defaults_missing_variable_to_zero():
    workflow = Workflow(name="t", backend="web", steps=[Step("increment", {"variable": "counter"})])
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert engine.variables["counter"] == 1


def test_increment_accumulates_across_steps():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("increment", {"variable": "counter", "by": 5}),
            Step("increment", {"variable": "counter", "by": 2}),
        ],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert engine.variables["counter"] == 7


def test_safe_eval_blocks_builtins():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("assign", {"variable": "x", "expression": "__import__('os')"})],
    )
    engine = WorkflowEngine(RecordingBackend())

    with pytest.raises(StepError):
        engine.run(workflow)


def test_safe_eval_allows_curated_builtins():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("assign", {"variable": "n", "expression": "len(account)"})],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"account": "12345"})

    assert engine.variables["n"] == 5


def test_for_each_runs_body_once_per_item_binding_item_and_index_vars():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "for_each",
                {
                    "items": "rows",
                    "item_var": "row",
                    "index_var": "i",
                    "steps": [{"action": "navigate", "url": "{var.i}:{var.row}"}],
                },
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"rows": ["a", "b", "c"]})

    assert backend.calls == [
        ("navigate", "0:a"),
        ("navigate", "1:b"),
        ("navigate", "2:c"),
    ]


def test_for_each_defaults_item_var_to_item():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("for_each", {"items": "rows", "steps": [{"action": "navigate", "url": "{var.item}"}]})],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"rows": [1, 2]})

    assert backend.calls == [("navigate", "1"), ("navigate", "2")]


def test_for_each_over_non_iterable_raises_step_error():
    workflow = Workflow(name="t", backend="web", steps=[Step("for_each", {"items": "42", "steps": []})])
    engine = WorkflowEngine(RecordingBackend())

    with pytest.raises(StepError):
        engine.run(workflow)


def test_try_runs_catch_branch_when_try_body_fails():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "try",
                {
                    "steps": [{"action": "does_not_exist"}],
                    "catch": [{"action": "navigate", "url": "recovered"}],
                    "error_var": "err",
                },
            )
        ],
    )
    backend = RecordingBackend()
    engine = WorkflowEngine(backend)

    engine.run(workflow)

    assert backend.calls == [("navigate", "recovered")]
    assert "does_not_exist" in engine.variables["err"]


def test_try_skips_catch_branch_when_try_body_succeeds():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "try",
                {
                    "steps": [{"action": "navigate", "url": "ok"}],
                    "catch": [{"action": "navigate", "url": "should-not-run"}],
                },
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "ok")]


def test_try_lets_workflow_cancelled_propagate_through_catch():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("try", {"steps": [{"action": "navigate", "url": "a"}], "catch": []})],
    )
    backend = RecordingBackend()

    with pytest.raises(WorkflowCancelled):
        WorkflowEngine(backend).run(workflow, should_stop=lambda: True)


def test_http_request_stores_result_via_save_as(monkeypatch):
    monkeypatch.setattr(
        "uiflow.http_client.send_http_request",
        lambda **kwargs: {"status_code": 200, "headers": {}, "text": "ok", "json": {"a": 1}},
    )
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("http_request", {"url": "https://example.com/{var.path}"}, save_as="resp")],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"path": "x"})

    assert engine.variables["resp"]["status_code"] == 200


def test_http_request_without_url_raises_step_error():
    workflow = Workflow(name="t", backend="web", steps=[Step("http_request", {})])
    engine = WorkflowEngine(RecordingBackend())

    with pytest.raises(StepError):
        engine.run(workflow)


def test_write_excel_calls_write_excel_rows_with_evaluated_data(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        "uiflow.excel.write_excel_rows",
        lambda path, rows, sheet=None: captured.update(path=path, rows=rows, sheet=sheet) or len(rows),
    )
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("write_excel", {"path": str(tmp_path / "out.xlsx"), "data": "rows"})],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"rows": [{"a": 1}]})

    assert captured["rows"] == [{"a": 1}]


def test_get_credential_stores_value_and_redacts_it_from_later_logs(monkeypatch, caplog):
    monkeypatch.setattr("uiflow.credentials.get_credential", lambda name: "s3cr3t")
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("get_credential", {"name": "smtp_password"}, save_as="pw"),
            Step("navigate", {"url": "https://example.com/{var.pw}"}),
        ],
    )
    backend = RecordingBackend()

    with caplog.at_level("INFO", logger="uiflow"):
        WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "https://example.com/s3cr3t")]
    assert "s3cr3t" not in caplog.text
    assert "***" in caplog.text


def test_get_credential_without_save_as_raises_step_error():
    workflow = Workflow(name="t", backend="web", steps=[Step("get_credential", {"name": "x"})])
    engine = WorkflowEngine(RecordingBackend())

    with pytest.raises(StepError):
        engine.run(workflow)


def test_send_email_calls_email_client_with_substituted_params(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "uiflow.email_client.send_email", lambda **kwargs: captured.update(kwargs)
    )
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "send_email",
                {
                    "smtp_host": "smtp.example.com",
                    "username": "u",
                    "password": "p",
                    "to": "{var.recipient}",
                    "subject": "Hi",
                    "body": "Hello",
                },
            )
        ],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"recipient": "a@b.com"})

    assert captured["to"] == "a@b.com"


def test_read_emails_stores_messages_via_save_as(monkeypatch):
    monkeypatch.setattr(
        "uiflow.email_client.read_emails", lambda **kwargs: [{"subject": "hi"}]
    )
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("read_emails", {"imap_host": "imap.example.com", "username": "u", "password": "p"}, save_as="inbox")],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert engine.variables["inbox"] == [{"subject": "hi"}]


def test_read_pdf_stores_text_via_save_as(monkeypatch):
    monkeypatch.setattr("uiflow.documents.read_pdf_text", lambda path, pages=None: "extracted text")
    workflow = Workflow(name="t", backend="web", steps=[Step("read_pdf", {"path": "doc.pdf"}, save_as="text")])
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert engine.variables["text"] == "extracted text"


def test_ocr_image_stores_text_via_save_as(monkeypatch):
    monkeypatch.setattr("uiflow.documents.ocr_image_text", lambda path, lang="eng": "ocr text")
    workflow = Workflow(name="t", backend="web", steps=[Step("ocr_image", {"path": "img.png"}, save_as="text")])
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert engine.variables["text"] == "ocr text"


def _run_capturing_logs(workflow, caplog, backend=None, **kwargs):
    with caplog.at_level(logging.INFO, logger="uiflow"):
        WorkflowEngine(backend or RecordingBackend()).run(workflow, **kwargs)
    return caplog.text


def test_assign_never_logs_a_credential_value(monkeypatch, caplog):
    monkeypatch.setattr("keyring.get_password", lambda service, name: "hunter2")
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("get_credential", {"name": "pw"}, save_as="pw"),
            Step("assign", {"variable": "copy", "expression": "pw"}),
        ],
    )

    logs = _run_capturing_logs(workflow, caplog)

    assert "hunter2" not in logs
    assert "assign copy = '***'" in logs


def test_switch_never_logs_a_credential_value(monkeypatch, caplog):
    monkeypatch.setattr("keyring.get_password", lambda service, name: "hunter2")
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("get_credential", {"name": "pw"}, save_as="pw"),
            Step("switch", {"expression": "pw", "cases": {"other": []}}),
        ],
    )

    logs = _run_capturing_logs(workflow, caplog)

    assert "hunter2" not in logs


def test_breakpoint_variables_snapshot_masks_credentials(monkeypatch):
    monkeypatch.setattr("keyring.get_password", lambda service, name: "hunter2")
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("get_credential", {"name": "pw"}, save_as="pw"),
            Step("navigate", {"url": "a"}, breakpoint=True),
        ],
    )
    seen = {}

    def on_breakpoint(index, step, variables, path):
        seen.update(variables)

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint)

    assert seen == {"pw": "***"}


def test_breakpoint_reports_the_path_of_a_step_inside_a_branch():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("navigate", {"url": "a"}),
            Step(
                "if",
                {
                    "condition": "True",
                    "then": [{"action": "click", "selector": "#x", "breakpoint": True}],
                },
            ),
        ],
    )
    seen = []

    def on_breakpoint(index, step, variables, path):
        seen.append((index, path))

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint)

    # The executed-step number (3) exceeds the two top-level steps, so only the
    # path identifies which card in the Studio canvas is actually paused.
    assert seen == [(3, "1.then.0")]


def test_breakpoint_path_for_a_loop_body_is_stable_across_iterations():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "for_each",
                {
                    "items": "[1, 2]",
                    "steps": [{"action": "navigate", "url": "x", "breakpoint": True}],
                },
            )
        ],
    )
    paths = []

    def on_breakpoint(index, step, variables, path):
        paths.append(path)

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint)

    assert paths == ["0.steps.0", "0.steps.0"]  # same card, two visits


def test_breakpoint_path_for_a_matched_switch_case():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "switch",
                {
                    "expression": "land",
                    "cases": {"DE": [{"action": "navigate", "url": "de", "breakpoint": True}]},
                    "default": [{"action": "navigate", "url": "other"}],
                },
            )
        ],
    )
    paths = []

    def on_breakpoint(index, step, variables, path):
        paths.append(path)

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint, variables={"land": "DE"})

    assert paths == ["0.cases.DE.0"]


def test_breakpoint_path_for_the_default_switch_branch():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "switch",
                {
                    "expression": "land",
                    "cases": {"DE": [{"action": "navigate", "url": "de"}]},
                    "default": [{"action": "navigate", "url": "other", "breakpoint": True}],
                },
            )
        ],
    )
    paths = []

    def on_breakpoint(index, step, variables, path):
        paths.append(path)

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint, variables={"land": "FR"})

    assert paths == ["0.default.0"]


def test_breakpoint_path_for_a_catch_branch():
    class FailingBackend:
        def navigate(self, url):
            raise RuntimeError("boom")

    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "try",
                {
                    "steps": [{"action": "navigate", "url": "x"}],
                    "catch": [{"action": "navigate", "url": "recover", "breakpoint": True}],
                },
            )
        ],
    )
    paths = []

    def on_breakpoint(index, step, variables, path):
        paths.append(path)

    try:
        WorkflowEngine(FailingBackend()).run(workflow, on_breakpoint=on_breakpoint)
    except RuntimeError:
        pass

    assert paths == ["0.catch.0"]


# --- run_workflow (sub-workflows) ------------------------------------------


@pytest.fixture
def workflows_dir(tmp_path, monkeypatch):
    """Points name-based workflow lookup at a temp directory."""
    monkeypatch.setattr("uiflow.models.WORKFLOWS_DIR", tmp_path)
    return tmp_path


def write_workflow(directory, name, steps, backend="web"):
    Workflow(name=name, backend=backend, steps=[Step.from_dict(s) for s in steps]).save(
        directory / f"{name}.yaml"
    )


def test_run_workflow_executes_the_referenced_workflow(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "sub"}])
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[Step("navigate", {"url": "a"}), Step("run_workflow", {"workflow": "teilprozess"})],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "a"), ("navigate", "sub")]


def test_sub_workflow_only_sees_the_arguments_it_is_given(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "{var.kunde}"}])
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step("assign", {"variable": "geheim", "value": "nicht weitergeben"}),
            Step("run_workflow", {"workflow": "teilprozess", "arguments": {"kunde": "{var.name}"}}),
        ],
    )
    backend = RecordingBackend()
    engine = WorkflowEngine(backend)

    engine.run(workflow, variables={"name": "Anna"})

    assert backend.calls == [("navigate", "Anna")]
    # the caller's own variables never entered the sub-workflow
    assert engine.variables["geheim"] == "nicht weitergeben"


def test_sub_workflow_variables_do_not_leak_back_unless_declared(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "assign", "variable": "intern", "value": "x"}])
    workflow = Workflow(
        name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})]
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert "intern" not in engine.variables


def test_outputs_map_sub_workflow_variables_into_the_caller(workflows_dir):
    write_workflow(
        workflows_dir, "teilprozess", [{"action": "assign", "variable": "ergebnis", "value": "gebucht"}]
    )
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step("run_workflow", {"workflow": "teilprozess", "outputs": {"ergebnis": "buchungsstatus"}}),
        ],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert engine.variables["buchungsstatus"] == "gebucht"


def test_outputs_are_not_copied_when_the_sub_workflow_fails(workflows_dir):
    write_workflow(
        workflows_dir,
        "teilprozess",
        [
            {"action": "assign", "variable": "ergebnis", "value": "halb fertig"},
            {"action": "does_not_exist", "x": 1},
        ],
    )
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[Step("run_workflow", {"workflow": "teilprozess", "outputs": {"ergebnis": "status"}})],
    )
    engine = WorkflowEngine(RecordingBackend())

    with pytest.raises(StepError):
        engine.run(workflow)

    assert "status" not in engine.variables


def test_caller_variables_are_restored_after_a_failing_sub_workflow(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "does_not_exist", "x": 1}])
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step("assign", {"variable": "vorher", "value": "da"}),
            Step("try", {"steps": [{"action": "run_workflow", "workflow": "teilprozess"}]}),
            Step("navigate", {"url": "{var.vorher}"}),
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "da")]  # the caller's scope came back intact


def test_run_workflow_rejects_a_cycle(workflows_dir):
    write_workflow(workflows_dir, "b", [{"action": "run_workflow", "workflow": "haupt"}])
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "b"})])

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert "cycle" in str(excinfo.value).lower()
    assert "haupt -> b -> haupt" in str(excinfo.value)


def test_run_workflow_rejects_a_backend_mismatch(workflows_dir):
    write_workflow(workflows_dir, "desktop_teil", [{"action": "click"}], backend="desktop")
    workflow = Workflow(
        name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "desktop_teil"})]
    )

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert "backend 'desktop'" in str(excinfo.value)


def test_run_workflow_reports_a_missing_file_with_step_context(workflows_dir):
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "gibtsnicht"})])

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert isinstance(excinfo.value.original, FileNotFoundError)


def test_run_workflow_requires_a_name(workflows_dir):
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {})])

    with pytest.raises(StepError):
        WorkflowEngine(RecordingBackend()).run(workflow)


def test_breakpoint_in_a_sub_workflow_reports_a_path_of_its_own(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "x", "breakpoint": True}])
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[Step("navigate", {"url": "a"}), Step("run_workflow", {"workflow": "teilprozess"})],
    )
    seen = []

    def on_breakpoint(index, step, variables, path):
        seen.append(path)

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint)

    # Carries the sub-workflow's name, so it cannot collide with a path in the
    # calling workflow - whose canvas has no card for this step.
    assert seen == ["1@teilprozess.0"]


def test_credentials_stay_masked_across_the_sub_workflow_boundary(workflows_dir, monkeypatch, caplog):
    monkeypatch.setattr("keyring.get_password", lambda service, name: "hunter2")
    write_workflow(
        workflows_dir,
        "teilprozess",
        [{"action": "get_credential", "name": "pw", "save_as": "pw"}, {"action": "navigate", "url": "sub"}],
    )
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step("run_workflow", {"workflow": "teilprozess", "outputs": {"pw": "uebernommen"}}),
            Step("assign", {"variable": "kopie", "expression": "uebernommen"}),
        ],
    )

    logs = _run_capturing_logs(workflow, caplog)

    assert "hunter2" not in logs
    assert "assign kopie = '***'" in logs


def test_a_sub_workflow_can_itself_call_another(workflows_dir):
    write_workflow(workflows_dir, "innen", [{"action": "navigate", "url": "innen"}])
    write_workflow(
        workflows_dir,
        "mitte",
        [{"action": "navigate", "url": "mitte"}, {"action": "run_workflow", "workflow": "innen"}],
    )
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "mitte"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "mitte"), ("navigate", "innen")]


def test_a_whole_placeholder_argument_keeps_its_type(workflows_dir):
    write_workflow(
        workflows_dir,
        "teilprozess",
        [{"action": "assign", "variable": "anzahl", "expression": "len(kunden)"}],
    )
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step(
                "run_workflow",
                {
                    "workflow": "teilprozess",
                    "arguments": {"kunden": "{var.alle_kunden}"},
                    "outputs": {"anzahl": "wie_viele"},
                },
            )
        ],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"alle_kunden": [{"nr": 1}, {"nr": 2}, {"nr": 3}]})

    # len() on the list, not on its str() - which would have been 24 characters
    assert engine.variables["wie_viele"] == 3


def test_a_placeholder_inside_a_longer_argument_still_substitutes(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "{var.ziel}"}])
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step(
                "run_workflow",
                {"workflow": "teilprozess", "arguments": {"ziel": "https://x/{var.pfad}?q=1"}},
            )
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"pfad": "suche"})

    assert backend.calls == [("navigate", "https://x/suche?q=1")]


def test_queue_item_fields_can_be_passed_whole(workflows_dir):
    write_workflow(
        workflows_dir, "teilprozess", [{"action": "assign", "variable": "erste", "expression": "zeilen[0]"}]
    )
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step(
                "run_workflow",
                {
                    "workflow": "teilprozess",
                    "arguments": {"zeilen": "{item.positionen}"},
                    "outputs": {"erste": "erste_position"},
                },
            )
        ],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, variables={"item": {"positionen": ["a", "b"]}})

    assert engine.variables["erste_position"] == "a"


# --- global variables -------------------------------------------------------


def test_globals_are_readable_as_a_placeholder_namespace():
    workflow = Workflow(name="t", backend="web", steps=[Step("navigate", {"url": "{global.basis_url}/start"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, global_variables={"basis_url": "https://erp.example.com"})

    assert backend.calls == [("navigate", "https://erp.example.com/start")]


def test_globals_are_readable_in_expressions_under_their_plain_name():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("if", {"condition": "betrag > max_betrag", "then": [{"action": "navigate", "url": "freigabe"}]})
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"betrag": 9000}, global_variables={"max_betrag": 5000})

    assert backend.calls == [("navigate", "freigabe")]


def test_a_workflow_variable_shadows_a_global_of_the_same_name_in_expressions():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("assign", {"variable": "max_betrag", "expression": "100"}),
            Step("assign", {"variable": "grenze", "expression": "max_betrag"}),
        ],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, global_variables={"max_betrag": 5000})

    assert engine.variables["grenze"] == 100  # the run's own value, not the global


def test_assigning_a_shadowing_variable_leaves_the_global_untouched():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("assign", {"variable": "max_betrag", "expression": "100"}),
            Step("navigate", {"url": "{global.max_betrag}"}),
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, global_variables={"max_betrag": 5000})

    assert backend.calls == [("navigate", "5000")]


def test_globals_keep_their_type_in_expressions():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("assign", {"variable": "wie_viele", "expression": "len(empfaenger)"})],
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow, global_variables={"empfaenger": ["a@x.de", "b@x.de", "c@x.de"]})

    assert engine.variables["wie_viele"] == 3


def test_an_unknown_global_placeholder_is_blank_like_any_other():
    workflow = Workflow(name="t", backend="web", steps=[Step("navigate", {"url": "x/{global.fehlt}"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, global_variables={})

    assert backend.calls == [("navigate", "x/")]


def test_globals_reach_a_sub_workflow_without_being_passed(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "{global.basis_url}/sub"}])
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, global_variables={"basis_url": "https://erp.example.com"})

    # No `arguments` at all - a global is installation-wide, not something that
    # has to be threaded through every call.
    assert backend.calls == [("navigate", "https://erp.example.com/sub")]


def test_a_sub_workflow_cannot_change_a_global_for_its_caller(workflows_dir):
    write_workflow(
        workflows_dir, "teilprozess", [{"action": "assign", "variable": "basis_url", "value": "gekapert"}]
    )
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[
            Step("run_workflow", {"workflow": "teilprozess"}),
            Step("navigate", {"url": "{global.basis_url}"}),
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, global_variables={"basis_url": "https://erp.example.com"})

    assert backend.calls == [("navigate", "https://erp.example.com")]


def test_breakpoint_shows_globals_only_when_there_are_some():
    workflow = Workflow(name="t", backend="web", steps=[Step("navigate", {"url": "a"}, breakpoint=True)])
    seen = []

    def on_breakpoint(index, step, variables, path):
        seen.append(variables)

    WorkflowEngine(RecordingBackend()).run(workflow, on_breakpoint=on_breakpoint)
    assert seen == [{}]  # no permanent empty "global" row in the variables watch

    seen.clear()
    WorkflowEngine(RecordingBackend()).run(
        workflow, on_breakpoint=on_breakpoint, global_variables={"basis_url": "https://x"}
    )
    assert seen == [{"global": {"basis_url": "https://x"}}]
