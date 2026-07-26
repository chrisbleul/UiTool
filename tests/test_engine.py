import logging

import pytest

from uiflow.engine import BusinessError, StepError, WorkflowCancelled, WorkflowEngine, substitute_variables
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


def test_on_error_retry_succeeds_after_transient_failures():
    class FlakyBackend:
        def __init__(self):
            self.attempts = 0
            self.calls = []

        def click(self, selector):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("not ready yet")
            self.calls.append(("click", selector))

    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("click", {"selector": "#go"}, on_error="retry", retry_count=3, retry_delay=0.01)],
    )
    backend = FlakyBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.attempts == 3
    assert backend.calls == [("click", "#go")]


def test_on_error_retry_gives_up_after_retry_count_and_raises():
    class AlwaysFailingBackend:
        def __init__(self):
            self.attempts = 0

        def click(self, selector):
            self.attempts += 1
            raise RuntimeError("boom")

    backend = AlwaysFailingBackend()
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("click", {"selector": "#go"}, on_error="retry", retry_count=2, retry_delay=0)],
    )

    with pytest.raises(StepError):
        WorkflowEngine(backend).run(workflow)

    assert backend.attempts == 3  # the original attempt plus 2 retries


def test_on_error_continue_swallows_failure_and_runs_next_step():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step("does_not_exist", {}, on_error="continue"),
            Step("navigate", {"url": "after"}),
        ],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "after")]


def test_on_error_retry_stops_immediately_when_stop_is_requested_during_wait():
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1  # let the first attempt run, then cancel during its retry wait

    class AlwaysFailingBackend:
        def click(self, selector):
            raise RuntimeError("boom")

    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("click", {"selector": "#go"}, on_error="retry", retry_count=5, retry_delay=5.0)],
    )

    with pytest.raises(WorkflowCancelled):
        WorkflowEngine(AlwaysFailingBackend()).run(workflow, should_stop=should_stop)


def test_on_error_continue_does_not_swallow_workflow_cancelled_from_nested_steps():
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1  # cancel once the for_each's own body starts iterating

    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "for_each",
                {"items": "[1, 2]", "steps": [{"action": "navigate", "url": "a"}]},
                on_error="continue",
            )
        ],
    )

    with pytest.raises(WorkflowCancelled):
        WorkflowEngine(RecordingBackend()).run(workflow, should_stop=should_stop)


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
    """Points name-based workflow lookup - and the Object Repository, which
    lives in the same directory - at a temp directory."""
    monkeypatch.setattr("uiflow.models.WORKFLOWS_DIR", tmp_path)
    monkeypatch.setattr("uiflow.object_repository.WORKFLOWS_DIR", tmp_path)
    return tmp_path


def write_workflow(directory, name, steps, backend="web", variables=None):
    Workflow(
        name=name, backend=backend, steps=[Step.from_dict(s) for s in steps], variables=variables or {}
    ).save(directory / f"{name}.yaml")


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


def test_sub_workflows_snapshot_is_used_instead_of_the_live_file(workflows_dir):
    # No file for "teilprozess" is written at all - a run relying on the live
    # file would fail with FileNotFoundError; the snapshot must be enough.
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})])
    snapshot = Workflow(name="teilprozess", backend="web", steps=[Step("navigate", {"url": "aus dem snapshot"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, sub_workflows={"teilprozess": snapshot})

    assert backend.calls == [("navigate", "aus dem snapshot")]


def test_sub_workflows_snapshot_wins_over_a_changed_file(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "aktuelle datei"}])
    snapshot = Workflow(
        name="teilprozess", backend="web", steps=[Step("navigate", {"url": "stand beim einreihen"})]
    )
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, sub_workflows={"teilprozess": snapshot})

    assert backend.calls == [("navigate", "stand beim einreihen")]


def test_a_name_missing_from_the_snapshot_still_falls_back_to_the_live_file(workflows_dir):
    write_workflow(workflows_dir, "teilprozess", [{"action": "navigate", "url": "von der platte"}])
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})])
    backend = RecordingBackend()

    # An irrelevant snapshot entry (e.g. an older job queued before this
    # sub-workflow existed) must not break resolution - it just falls through.
    unrelated = Workflow(name="andere", backend="web", steps=[])
    WorkflowEngine(backend).run(workflow, sub_workflows={"andere": unrelated})

    assert backend.calls == [("navigate", "von der platte")]


# --- declared workflow variables ---------------------------------------------


def test_declared_variable_default_is_seeded_before_the_first_step():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("navigate", {"url": "{var.basis}"})],
        variables={"basis": "https://x"},
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "https://x")]


def test_declared_variable_without_a_default_stays_unset_until_assigned():
    workflow = Workflow(
        name="t", backend="web", steps=[Step("navigate", {"url": "a"})], variables={"zaehler": None}
    )
    engine = WorkflowEngine(RecordingBackend())

    engine.run(workflow)

    assert "zaehler" not in engine.variables


def test_an_explicit_run_variable_overrides_the_declared_default():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("navigate", {"url": "{var.basis}"})],
        variables={"basis": "https://default"},
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"basis": "https://override"})

    assert backend.calls == [("navigate", "https://override")]


def test_sub_workflow_own_declared_default_is_seeded_when_entering_it(workflows_dir):
    write_workflow(
        workflows_dir,
        "teilprozess",
        [{"action": "navigate", "url": "{var.zaehler}"}],
        variables={"zaehler": 0},
    )
    workflow = Workflow(name="haupt", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "0")]


def test_sub_workflow_argument_overrides_its_own_declared_default(workflows_dir):
    write_workflow(
        workflows_dir,
        "teilprozess",
        [{"action": "navigate", "url": "{var.zaehler}"}],
        variables={"zaehler": 0},
    )
    workflow = Workflow(
        name="haupt",
        backend="web",
        steps=[Step("run_workflow", {"workflow": "teilprozess", "arguments": {"zaehler": 5}})],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("navigate", "5")]


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


# --- object repository (element references) ---------------------------------


def test_element_reference_resolves_to_repository_fields(workflows_dir):
    from uiflow import object_repository

    object_repository.set_element("MeineApp", "Suchfeld", {"selector": "#search"})
    workflow = Workflow(name="t", backend="web", steps=[Step("click", {"element": "MeineApp/Suchfeld"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("click", "#search")]


def test_element_reference_fields_override_any_inline_selector_on_the_same_step(workflows_dir):
    from uiflow import object_repository

    object_repository.set_element("MeineApp", "Suchfeld", {"selector": "#search"})
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("click", {"selector": "#veraltet", "element": "MeineApp/Suchfeld"})],
    )
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("click", "#search")]


def test_element_reference_to_an_unknown_element_raises_a_clear_step_error(workflows_dir):
    workflow = Workflow(name="t", backend="web", steps=[Step("click", {"element": "MeineApp/GibtsNicht"})])

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert "MeineApp" in str(excinfo.value)
    assert "GibtsNicht" in str(excinfo.value)


def test_element_reference_without_a_slash_raises_a_clear_step_error(workflows_dir):
    workflow = Workflow(name="t", backend="web", steps=[Step("click", {"element": "keinslash"})])

    with pytest.raises(StepError):
        WorkflowEngine(RecordingBackend()).run(workflow)


def test_element_reference_supports_a_variable_placeholder_in_the_reference(workflows_dir):
    from uiflow import object_repository

    object_repository.set_element("MeineApp", "Suchfeld", {"selector": "#search"})
    workflow = Workflow(name="t", backend="web", steps=[Step("click", {"element": "{var.ref}"})])
    backend = RecordingBackend()

    WorkflowEngine(backend).run(workflow, variables={"ref": "MeineApp/Suchfeld"})

    assert backend.calls == [("click", "#search")]


class _ExistsAwareBackend:
    """A RecordingBackend that also answers element_exists(), for testing
    fallback-candidate selection - only the selectors in `existing` are
    reported as present."""

    def __init__(self, existing):
        self.calls = []
        self._existing = existing

    def click(self, selector):
        self.calls.append(("click", selector))

    def element_exists(self, selector):
        return selector in self._existing


def test_element_reference_tries_fallback_candidates_in_order(workflows_dir):
    from uiflow import object_repository

    object_repository.set_element("MeineApp", "Suchfeld", {"selector": "#alt-1"})
    object_repository.add_fallback("MeineApp", "Suchfeld", {"selector": "#alt-2"})
    object_repository.add_fallback("MeineApp", "Suchfeld", {"selector": "#alt-3"})
    workflow = Workflow(name="t", backend="web", steps=[Step("click", {"element": "MeineApp/Suchfeld"})])
    # Only the second candidate currently matches - the engine must skip the
    # first (absent) and use it instead of just taking the first one blindly.
    backend = _ExistsAwareBackend(existing={"#alt-2"})

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("click", "#alt-2")]


def test_element_reference_falls_back_to_the_first_candidate_when_none_match(workflows_dir):
    from uiflow import object_repository

    object_repository.set_element("MeineApp", "Suchfeld", {"selector": "#alt-1"})
    object_repository.add_fallback("MeineApp", "Suchfeld", {"selector": "#alt-2"})
    workflow = Workflow(name="t", backend="web", steps=[Step("click", {"element": "MeineApp/Suchfeld"})])
    backend = _ExistsAwareBackend(existing=set())  # nothing currently matches

    WorkflowEngine(backend).run(workflow)

    # Falls through to the first candidate so the real click still runs (and
    # would raise its own normal "not found" error), instead of silently
    # skipping the step because no fallback matched.
    assert backend.calls == [("click", "#alt-1")]


def test_element_reference_uses_the_first_candidate_when_backend_cannot_check_existence(workflows_dir):
    from uiflow import object_repository

    object_repository.set_element("MeineApp", "Suchfeld", {"selector": "#alt-1"})
    object_repository.add_fallback("MeineApp", "Suchfeld", {"selector": "#alt-2"})
    workflow = Workflow(name="t", backend="web", steps=[Step("click", {"element": "MeineApp/Suchfeld"})])
    backend = RecordingBackend()  # no element_exists method at all

    WorkflowEngine(backend).run(workflow)

    assert backend.calls == [("click", "#alt-1")]


# --- fail (business vs. technical errors) ------------------------------------


def test_fail_with_type_business_raises_business_error_not_step_error():
    workflow = Workflow(
        name="t", backend="web", steps=[Step("fail", {"message": "Ungültige Rechnung", "type": "business"})]
    )

    with pytest.raises(BusinessError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert "Ungültige Rechnung" in str(excinfo.value)


def test_fail_with_type_technical_raises_a_normal_step_error():
    workflow = Workflow(
        name="t", backend="web", steps=[Step("fail", {"message": "Timeout", "type": "technical"})]
    )

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert "Timeout" in str(excinfo.value)


def test_fail_defaults_to_technical_when_type_is_omitted():
    workflow = Workflow(name="t", backend="web", steps=[Step("fail", {"message": "x"})])

    with pytest.raises(StepError):
        WorkflowEngine(RecordingBackend()).run(workflow)


def test_fail_rejects_an_unknown_type():
    workflow = Workflow(name="t", backend="web", steps=[Step("fail", {"message": "x", "type": "oops"})])

    with pytest.raises(StepError):
        WorkflowEngine(RecordingBackend()).run(workflow)


def test_fail_message_substitutes_variables():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("fail", {"message": "Betrag {var.betrag} ungültig", "type": "business"})],
    )

    with pytest.raises(BusinessError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow, variables={"betrag": -5})

    assert "Betrag -5 ungültig" in str(excinfo.value)


def test_try_catches_a_business_fail_like_any_other_error():
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "try",
                {
                    "steps": [{"action": "fail", "message": "ungültig", "type": "business"}],
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
    assert "ungültig" in engine.variables["err"]


def _load_shipped_workflow(name: str) -> Workflow:
    """Loads a workflow shipped in the repo's own workflows/ directory - by
    path relative to this test file, not the current working directory, so
    the test doesn't depend on where pytest happens to be invoked from."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    return Workflow.load(repo_root / "workflows" / name)


def test_shipped_reframework_example_validates_business_rules_before_the_try_block():
    workflow = _load_shipped_workflow("beispiel_reframework.yaml")
    backend = RecordingBackend()

    with pytest.raises(BusinessError):
        WorkflowEngine(backend).run(
            workflow,
            variables={"item": {"betrag": -5, "rechnungsnummer": "R1"}},
            global_variables={"basis_url": "https://erp.example.com"},
        )

    # the business check ran before the automation - the try block never started
    assert backend.calls == [("navigate", "https://erp.example.com/anmelden")]


def test_shipped_reframework_example_re_raises_a_technical_failure_after_the_catch_block():
    class _FailingBackend:
        def __init__(self):
            self.calls = []

        def navigate(self, url):
            self.calls.append(("navigate", url))
            if "rechnung" in url:
                raise RuntimeError("Seite nicht erreichbar")

        def screenshot(self, path):
            self.calls.append(("screenshot", path))

    workflow = _load_shipped_workflow("beispiel_reframework.yaml")
    backend = _FailingBackend()

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(backend).run(
            workflow,
            variables={"item": {"betrag": 500, "rechnungsnummer": "R1"}},
            global_variables={"basis_url": "https://erp.example.com"},
        )

    # the catch block ran (screenshot taken) before the error was re-raised as
    # technical - a queue-driven job must still see this as a normal, retryable
    # failure, not a silently swallowed one.
    assert ("screenshot", "fehler_R1.png") in backend.calls


# --- dry-run mode (see backends/dry_run.py) -----------------------------------


def test_dry_run_never_touches_a_real_backend_method():
    from uiflow.backends.dry_run import DryRunBackend

    class _RealWebBackend:
        def navigate(self, url):
            raise AssertionError("a dry run must never call the real backend")

    workflow = Workflow(name="t", backend="web", steps=[Step("navigate", {"url": "https://x"})])

    WorkflowEngine(DryRunBackend(_RealWebBackend)).run(workflow, dry_run=True)  # must not raise


def test_dry_run_still_catches_an_undefined_variable_in_an_expression():
    from uiflow.backends.dry_run import DryRunBackend

    class _RealWebBackend:
        pass

    workflow = Workflow(
        name="t", backend="web", steps=[Step("if", {"condition": "nicht_deklariert == 1", "then": []})]
    )

    with pytest.raises(StepError):
        WorkflowEngine(DryRunBackend(_RealWebBackend)).run(workflow, dry_run=True)


def test_dry_run_rejects_an_action_name_neither_backend_implements():
    from uiflow.backends.dry_run import DryRunBackend

    class _RealWebBackend:
        pass

    workflow = Workflow(name="t", backend="web", steps=[Step("does_not_exist_anywhere", {})])

    with pytest.raises(StepError, match="has no action"):
        WorkflowEngine(DryRunBackend(_RealWebBackend)).run(workflow, dry_run=True)


def test_dry_run_element_exists_is_optimistic():
    from uiflow.backends.dry_run import DryRunBackend

    class _RealWebBackend:
        def element_exists(self, **kwargs):
            raise AssertionError("must not call the real backend")

    assert DryRunBackend(_RealWebBackend).element_exists(selector="#x") is True


def test_dry_run_skips_http_request_and_provides_a_placeholder(monkeypatch):
    from uiflow.backends.dry_run import DryRunBackend

    def _boom(**kwargs):
        raise AssertionError("must not make a real HTTP request during a dry run")

    monkeypatch.setattr("uiflow.http_client.send_http_request", _boom)

    class _RealWebBackend:
        pass

    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("http_request", {"url": "https://api.example.com"}, save_as="result")],
    )

    engine = WorkflowEngine(DryRunBackend(_RealWebBackend))
    engine.run(workflow, dry_run=True)

    assert engine.variables["result"]["dry_run"] is True
    assert engine.variables["result"]["status_code"] == 0


def test_dry_run_skips_send_email(monkeypatch):
    from uiflow.backends.dry_run import DryRunBackend

    def _boom(**kwargs):
        raise AssertionError("must not send a real e-mail during a dry run")

    monkeypatch.setattr("uiflow.email_client.send_email", _boom)

    class _RealWebBackend:
        pass

    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("send_email", {"to": "a@x.de", "subject": "s", "body": "b"}, save_as="result")],
    )

    engine = WorkflowEngine(DryRunBackend(_RealWebBackend))
    engine.run(workflow, dry_run=True)

    assert engine.variables["result"] == {"sent": False, "dry_run": True}


def test_dry_run_skips_read_emails(monkeypatch):
    from uiflow.backends.dry_run import DryRunBackend

    def _boom(**kwargs):
        raise AssertionError("must not connect to a real mailbox during a dry run")

    monkeypatch.setattr("uiflow.email_client.read_emails", _boom)

    class _RealWebBackend:
        pass

    workflow = Workflow(name="t", backend="web", steps=[Step("read_emails", {}, save_as="messages")])

    engine = WorkflowEngine(DryRunBackend(_RealWebBackend))
    engine.run(workflow, dry_run=True)

    assert engine.variables["messages"] == []


def test_dry_run_skips_write_excel(monkeypatch, tmp_path):
    from uiflow.backends.dry_run import DryRunBackend

    def _boom(*args, **kwargs):
        raise AssertionError("must not write a real file during a dry run")

    monkeypatch.setattr("uiflow.excel.write_excel_rows", _boom)

    class _RealWebBackend:
        pass

    target = tmp_path / "out.xlsx"
    workflow = Workflow(
        name="t",
        backend="web",
        steps=[Step("write_excel", {"path": str(target), "data": "[{'a': 1}]"}, save_as="count")],
    )

    engine = WorkflowEngine(DryRunBackend(_RealWebBackend))
    engine.run(workflow, dry_run=True)

    assert engine.variables["count"] == 1
    assert not target.exists()


def test_dry_run_propagates_into_a_sub_workflow(monkeypatch, tmp_path):
    from uiflow.backends.dry_run import DryRunBackend

    def _boom(**kwargs):
        raise AssertionError("a sub-workflow's http_request must also be skipped in a dry run")

    monkeypatch.setattr("uiflow.http_client.send_http_request", _boom)

    class _RealWebBackend:
        pass

    sub = Workflow(name="teilprozess", backend="web", steps=[Step("http_request", {"url": "https://x"})])
    main = Workflow(name="main", backend="web", steps=[Step("run_workflow", {"workflow": "teilprozess"})])

    engine = WorkflowEngine(DryRunBackend(_RealWebBackend))
    engine.run(main, dry_run=True, sub_workflows={"teilprozess": sub})  # must not raise


# --- request_approval (human-in-the-loop, see engine.py's _run_request_approval) --


def test_request_approval_requires_a_title():
    workflow = Workflow(name="t", backend="web", steps=[Step("request_approval", {})])

    with pytest.raises(StepError):
        WorkflowEngine(RecordingBackend()).run(workflow)


def test_request_approval_without_a_handler_raises_a_clear_step_error():
    """Bare `uiflow run` (no orchestrator) has nothing that can wait on a
    human - see run()'s on_request_approval docstring."""
    workflow = Workflow(name="t", backend="web", steps=[Step("request_approval", {"title": "Freigeben?"})])

    with pytest.raises(StepError) as excinfo:
        WorkflowEngine(RecordingBackend()).run(workflow)

    assert "request_approval" in str(excinfo.value)


def test_request_approval_calls_the_handler_with_title_and_message_and_stores_the_decision():
    calls = []

    def handler(title, message, variables):
        calls.append((title, message, dict(variables)))
        return {"approved": True, "comment": "sieht gut aus", "decided_by": "alice"}

    workflow = Workflow(
        name="t",
        backend="web",
        steps=[
            Step(
                "request_approval",
                {"title": "Rechnung {var.betrag}€ freigeben", "message": "Bitte prüfen"},
                save_as="decision",
            )
        ],
    )

    engine = WorkflowEngine(RecordingBackend())
    engine.run(workflow, variables={"betrag": 12000}, on_request_approval=handler)

    assert calls[0][0] == "Rechnung 12000€ freigeben"
    assert calls[0][1] == "Bitte prüfen"
    assert engine.variables["decision"] == {"approved": True, "comment": "sieht gut aus", "decided_by": "alice"}


def test_request_approval_rejection_is_just_a_normal_decision_not_a_failure():
    """A rejection doesn't raise - it's a value in `variables`, same as any
    other step result; a workflow author branches on it with a normal `if`."""

    def handler(title, message, variables):
        return {"approved": False, "comment": "zu hoch", "decided_by": "bob"}

    workflow = Workflow(
        name="t", backend="web", steps=[Step("request_approval", {"title": "x"}, save_as="decision")]
    )

    engine = WorkflowEngine(RecordingBackend())
    engine.run(workflow, on_request_approval=handler)  # must not raise

    assert engine.variables["decision"]["approved"] is False


def test_request_approval_dry_run_auto_approves_without_calling_the_handler():
    def handler(title, message, variables):
        raise AssertionError("must not wait on a human during a dry run")

    workflow = Workflow(
        name="t", backend="web", steps=[Step("request_approval", {"title": "x"}, save_as="decision")]
    )

    from uiflow.backends.dry_run import DryRunBackend

    class _RealWebBackend:
        pass

    engine = WorkflowEngine(DryRunBackend(_RealWebBackend))
    engine.run(workflow, dry_run=True, on_request_approval=handler)

    assert engine.variables["decision"]["approved"] is True
    assert engine.variables["decision"]["dry_run"] is True


def test_request_approval_propagates_into_a_sub_workflow():
    def handler(title, message, variables):
        return {"approved": True, "comment": "", "decided_by": None}

    sub = Workflow(
        name="teilprozess", backend="web", steps=[Step("request_approval", {"title": "x"}, save_as="decision")]
    )
    main = Workflow(
        name="main",
        backend="web",
        steps=[Step("run_workflow", {"workflow": "teilprozess", "outputs": {"decision": "sub_decision"}})],
    )

    engine = WorkflowEngine(RecordingBackend())
    engine.run(main, sub_workflows={"teilprozess": sub}, on_request_approval=handler)

    assert engine.variables["sub_decision"]["approved"] is True
