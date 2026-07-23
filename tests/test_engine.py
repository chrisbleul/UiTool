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

    def on_breakpoint(index, step, variables):
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

    def on_breakpoint(index, step, variables):
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

    def on_breakpoint(index, step, variables):
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
