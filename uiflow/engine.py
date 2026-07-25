from __future__ import annotations

import builtins
import logging
import re
from typing import Any, Callable, Optional

from .models import Step, Workflow, load_workflow_by_name

logger = logging.getLogger("uiflow")

# (executed-step number, step, variables snapshot, structural step path)
OnBreakpoint = Callable[[int, Step, "dict[str, Any]", str], None]
ShouldStop = Callable[[], bool]

# {item.field} reads from the current queue item's payload (variables["item"]);
# {var.name} reads any other workflow variable. Two explicit namespaces rather
# than one generic one, matching how they're introduced to workflow authors.
_PLACEHOLDER_RE = re.compile(r"\{(item|var)\.([a-zA-Z0-9_]+)\}")


def _resolve_placeholder(namespace: str, name: str, variables: dict[str, Any]) -> str:
    if namespace == "item":
        item = variables.get("item") or {}
        return str(item.get(name, ""))
    return str(variables.get(name, ""))


def substitute_variables(value: Any, variables: dict[str, Any]) -> Any:
    """Recursively replaces {item.x}/{var.x} placeholders in strings (and inside
    nested dicts/lists) using the current variables. Non-string values pass
    through unchanged - e.g. a `by: 1` int in an `increment` step's params."""
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda m: _resolve_placeholder(m.group(1), m.group(2), variables), value)
    if isinstance(value, dict):
        return {k: substitute_variables(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_variables(v, variables) for v in value]
    return value


_SAFE_BUILTIN_NAMES = {
    "len", "str", "int", "float", "bool", "abs", "round", "min", "max", "sum",
    "sorted", "list", "dict", "tuple", "set", "range", "enumerate", "zip", "any", "all",
}
_SAFE_BUILTINS = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}


def safe_eval(expression: str, variables: dict[str, Any]) -> Any:
    """Evaluates a Python expression (used for if/switch conditions and assign
    expressions) with variable names resolved from `variables` and only a small
    curated set of harmless builtins (len, str, int, ...) - blocks the obvious
    footguns (__import__, open, exec, eval) but, like any eval()-based approach,
    isn't hardened against a determined author deliberately trying to escape it
    (e.g. via dunder attribute introspection). That's an accepted tradeoff here:
    workflow definitions are authored by the same person running them, like a
    local script, not untrusted remote input."""
    try:
        return eval(expression, {"__builtins__": _SAFE_BUILTINS}, dict(variables))  # noqa: S307
    except Exception as exc:  # noqa: BLE001 - wrapped with context by the caller
        raise ValueError(f"Could not evaluate expression '{expression}': {exc}") from exc


class StepError(RuntimeError):
    def __init__(self, index: int, step: Step, original: Exception):
        super().__init__(f"Step {index} ('{step.action}') failed: {original}")
        self.index = index
        self.step = step
        self.original = original


class WorkflowCancelled(RuntimeError):
    def __init__(self, index: int):
        super().__init__(f"Cancelled before step {index}")
        self.index = index


class WorkflowEngine:
    """Runs a Workflow by dispatching each Step to a same-named method on the
    backend - except a handful of action names the engine handles itself
    (`if`, `switch`, `for_each`, `try`, `run_workflow`, `assign`, `increment`,
    `read_excel`, `write_excel`, `http_request`, `get_credential`, `send_email`,
    `read_emails`, `read_pdf`, `ocr_image`), since they operate on
    workflow-run-scoped `variables` (or external services) rather than the UI.

    Step numbering ("[N] ...") is a single counter across the whole run, in
    execution order - branches that aren't taken never consume a number, so
    there's no meaningful upfront "total steps" to log (unlike the old flat-only
    engine), only a running count.

    Because that counter depends on which branches actually ran, it can't
    identify a step in the Studio's statically rendered canvas - so breakpoints
    additionally report a `path`, the step's structural address in the workflow
    definition (see _run_steps).
    """

    def __init__(self, backend: object):
        self.backend = backend
        self.variables: dict[str, Any] = {}
        # Values pulled in via `get_credential`, tracked so step logging
        # (see _log/_redact_secrets) can mask them instead of writing secrets
        # to the job log / console in plain text.
        self._secrets: set[str] = set()

    def run(
        self,
        workflow: Workflow,
        on_breakpoint: Optional[OnBreakpoint] = None,
        should_stop: Optional[ShouldStop] = None,
        variables: Optional[dict[str, Any]] = None,
    ) -> None:
        self.variables = dict(variables) if variables else {}
        self._secrets = set()
        self._counter = 0
        self._backend_name = workflow.backend
        # Names of the workflows currently on the call stack, so `run_workflow`
        # can refuse a cycle instead of recursing until Python's stack gives out.
        self._workflow_stack = [workflow.name]
        logger.info("Running workflow '%s' on backend=%s", workflow.name, workflow.backend)
        self._run_steps(workflow.steps, on_breakpoint, should_stop)
        logger.info("Workflow '%s' completed successfully", workflow.name)

    def _run_steps(
        self,
        steps: list[Step],
        on_breakpoint: Optional[OnBreakpoint],
        should_stop: Optional[ShouldStop],
        prefix: str = "",
    ) -> None:
        for position, step in enumerate(steps):
            if should_stop is not None and should_stop():
                index = self._counter + 1
                logger.info("Workflow abgebrochen vor Schritt %d", index)
                raise WorkflowCancelled(index)
            self._counter += 1
            index = self._counter
            # Where this step *sits* in the workflow definition ("3.then.0"),
            # as opposed to `index`, which is how many steps have *run*. Only
            # the path can be matched back to a card in the Studio canvas,
            # which is rendered from the definition, not from the run.
            path = f"{prefix}.{position}" if prefix else str(position)

            if step.breakpoint and on_breakpoint is not None:
                self._log("[%d] Haltepunkt bei '%s'", index, step.action)
                on_breakpoint(index, step, self._redact_secrets(dict(self.variables)), path)
                if should_stop is not None and should_stop():
                    logger.info("Workflow abgebrochen vor Schritt %d", index)
                    raise WorkflowCancelled(index)

            if step.action == "if":
                self._run_if(step, index, path, on_breakpoint, should_stop)
            elif step.action == "switch":
                self._run_switch(step, index, path, on_breakpoint, should_stop)
            elif step.action == "for_each":
                self._run_for_each(step, index, path, on_breakpoint, should_stop)
            elif step.action == "try":
                self._run_try(step, index, path, on_breakpoint, should_stop)
            elif step.action == "run_workflow":
                self._run_sub_workflow(step, index, path, on_breakpoint, should_stop)
            elif step.action == "assign":
                self._run_assign(step, index)
            elif step.action == "increment":
                self._run_increment(step, index)
            elif step.action == "read_excel":
                self._run_read_excel(step, index)
            elif step.action == "write_excel":
                self._run_write_excel(step, index)
            elif step.action == "http_request":
                self._run_http_request(step, index)
            elif step.action == "get_credential":
                self._run_get_credential(step, index)
            elif step.action == "send_email":
                self._run_send_email(step, index)
            elif step.action == "read_emails":
                self._run_read_emails(step, index)
            elif step.action == "read_pdf":
                self._run_read_pdf(step, index)
            elif step.action == "ocr_image":
                self._run_ocr_image(step, index)
            else:
                self._run_backend_step(step, index)

    @staticmethod
    def _sub_steps(raw: Any) -> list[Step]:
        if not isinstance(raw, list):
            return []
        return [Step.from_dict(dict(item)) for item in raw]

    def _run_if(
        self,
        step: Step,
        index: int,
        path: str,
        on_breakpoint: Optional[OnBreakpoint],
        should_stop: Optional[ShouldStop],
    ) -> None:
        condition = step.params.get("condition", "False")
        try:
            result = bool(safe_eval(condition, self.variables))
        except ValueError as exc:
            raise StepError(index, step, exc) from exc
        self._log("[%d] if %s -> %s", index, condition, result)
        field = "then" if result else "else"
        self._run_steps(self._sub_steps(step.params.get(field)), on_breakpoint, should_stop, f"{path}.{field}")

    def _run_switch(
        self,
        step: Step,
        index: int,
        path: str,
        on_breakpoint: Optional[OnBreakpoint],
        should_stop: Optional[ShouldStop],
    ) -> None:
        expression = step.params.get("expression", "")
        try:
            value = safe_eval(expression, self.variables)
        except ValueError as exc:
            raise StepError(index, step, exc) from exc
        cases = step.params.get("cases") or {}
        key = str(value)
        branch = cases.get(key)
        prefix = f"{path}.cases.{key}"
        if branch is None:
            branch = step.params.get("default")
            prefix = f"{path}.default"
        self._log("[%d] switch %s == %r", index, expression, value)
        self._run_steps(self._sub_steps(branch), on_breakpoint, should_stop, prefix)

    def _run_for_each(
        self,
        step: Step,
        index: int,
        path: str,
        on_breakpoint: Optional[OnBreakpoint],
        should_stop: Optional[ShouldStop],
    ) -> None:
        items_expr = step.params.get("items", "[]")
        try:
            items = safe_eval(items_expr, self.variables)
        except ValueError as exc:
            raise StepError(index, step, exc) from exc
        try:
            items = list(items)
        except TypeError as exc:
            raise StepError(index, step, ValueError(f"'{items_expr}' is not iterable")) from exc

        item_var = step.params.get("item_var") or "item"
        index_var = step.params.get("index_var")
        body = self._sub_steps(step.params.get("steps"))
        self._log("[%d] for_each %s -> %d item(s)", index, items_expr, len(items))
        for i, value in enumerate(items):
            self.variables[item_var] = value
            if index_var:
                self.variables[index_var] = i
            self._run_steps(body, on_breakpoint, should_stop, f"{path}.steps")

    def _run_try(
        self,
        step: Step,
        index: int,
        path: str,
        on_breakpoint: Optional[OnBreakpoint],
        should_stop: Optional[ShouldStop],
    ) -> None:
        try_body = self._sub_steps(step.params.get("steps"))
        catch_body = self._sub_steps(step.params.get("catch"))
        error_var = step.params.get("error_var")
        self._log("[%d] try", index)
        try:
            self._run_steps(try_body, on_breakpoint, should_stop, f"{path}.steps")
        except WorkflowCancelled:
            raise  # a user-requested stop must propagate, not be swallowed as a "handled" error
        except (StepError, ValueError) as exc:
            message = str(exc)
            self._log("[%d] try: caught error -> %s", index, message)
            if error_var:
                self.variables[error_var] = message
            self._run_steps(catch_body, on_breakpoint, should_stop, f"{path}.catch")

    def _resolve_argument(self, value: Any) -> Any:
        """Resolves one `run_workflow` argument.

        A value consisting of nothing but a placeholder passes the variable
        through *with its type*: `"{var.kunden}"` hands over the list itself,
        not "[{'nr': 1}, ...]". Ordinary substitution stringifies everything,
        which is right when a placeholder sits inside a larger string, but would
        silently flatten a table or number handed to a sub-workflow.
        """
        if isinstance(value, str):
            whole = _PLACEHOLDER_RE.fullmatch(value.strip())
            if whole:
                namespace, name = whole.group(1), whole.group(2)
                if namespace == "item":
                    return (self.variables.get("item") or {}).get(name)
                return self.variables.get(name)
        return substitute_variables(value, self.variables)

    def _run_sub_workflow(
        self,
        step: Step,
        index: int,
        path: str,
        on_breakpoint: Optional[OnBreakpoint],
        should_stop: Optional[ShouldStop],
    ) -> None:
        """Runs another workflow file as a building block of this one.

        Variables do *not* leak in either direction: the sub-workflow starts
        with only what `arguments` passes in, and only what `outputs` names comes
        back. Sharing the caller's variables would make a sub-workflow silently
        depend on - and overwrite - names it never declared, which is exactly the
        coupling that reusing it is supposed to avoid.

        It runs on the caller's *existing* backend rather than a second one, so
        the browser or application already opened stays the one being driven.
        """
        name = substitute_variables(step.params.get("workflow", ""), self.variables)
        if not name:
            raise StepError(index, step, ValueError("run_workflow requires 'workflow'"))
        if name in self._workflow_stack:
            chain = " -> ".join([*self._workflow_stack, name])
            raise StepError(index, step, ValueError(f"Sub-workflow cycle: {chain}"))

        try:
            sub = load_workflow_by_name(name)
        except Exception as exc:  # noqa: BLE001 - wrap a missing/broken file with step context
            raise StepError(index, step, exc) from exc
        if sub.backend != self._backend_name:
            raise StepError(
                index,
                step,
                ValueError(
                    f"Sub-workflow '{name}' expects backend '{sub.backend}', "
                    f"but it is called from a '{self._backend_name}' workflow"
                ),
            )

        raw_arguments = step.params.get("arguments") or {}
        if not isinstance(raw_arguments, dict):
            raise StepError(index, step, ValueError("run_workflow 'arguments' must be a mapping"))
        arguments = {arg: self._resolve_argument(value) for arg, value in raw_arguments.items()}
        outputs = step.params.get("outputs") or {}
        if not isinstance(outputs, dict):
            raise StepError(index, step, ValueError("run_workflow 'outputs' must be a mapping"))

        self._log("[%d] run_workflow '%s' (%d step(s), arguments: %s)", index, name, len(sub.steps), arguments)

        caller_variables = self.variables
        self.variables = dict(arguments)
        self._workflow_stack.append(name)
        try:
            # The path prefix carries the sub-workflow's name because its steps
            # live in another file: nothing on the calling workflow's canvas has
            # that path, so a breakpoint inside it pauses without the Studio
            # highlighting an unrelated card (see studio/static/app.js).
            self._run_steps(sub.steps, on_breakpoint, should_stop, f"{path}@{name}")
            produced = self.variables
        finally:
            self.variables = caller_variables
            self._workflow_stack.pop()
            # self._secrets is deliberately *not* restored: a credential read
            # inside the sub-workflow must stay masked in the caller's log too.

        for sub_name, caller_name in outputs.items():
            caller_variables[caller_name] = produced.get(sub_name)
        if outputs:
            self._log("[%d] run_workflow '%s' -> %s", index, name, self._redact_secrets(dict(outputs)))

    def _run_assign(self, step: Step, index: int) -> None:
        name = step.params.get("variable")
        if not name:
            raise StepError(index, step, ValueError("assign requires 'variable'"))
        if "expression" in step.params:
            try:
                value = safe_eval(step.params["expression"], self.variables)
            except ValueError as exc:
                raise StepError(index, step, exc) from exc
        else:
            value = substitute_variables(step.params.get("value", ""), self.variables)
        self._log("[%d] assign %s = %r", index, name, value)
        self.variables[name] = value

    def _run_increment(self, step: Step, index: int) -> None:
        name = step.params.get("variable")
        if not name:
            raise StepError(index, step, ValueError("increment requires 'variable'"))
        try:
            new_value = float(self.variables.get(name, 0)) + float(step.params.get("by", 1))
        except (TypeError, ValueError) as exc:
            raise StepError(index, step, exc) from exc
        if new_value == int(new_value):
            new_value = int(new_value)
        self._log("[%d] increment %s -> %s", index, name, new_value)
        self.variables[name] = new_value

    def _run_read_excel(self, step: Step, index: int) -> None:
        path = step.params.get("path")
        save_as = step.save_as or step.params.get("variable")
        if not path or not save_as:
            raise StepError(index, step, ValueError("read_excel requires 'path' and save_as"))
        try:
            from .excel import read_excel_rows

            rows = read_excel_rows(path, sheet=step.params.get("sheet"))
        except Exception as exc:  # noqa: BLE001 - wrap any openpyxl/file error with step context
            raise StepError(index, step, exc) from exc
        self._log("[%d] read_excel '%s' -> %d row(s) into '%s'", index, path, len(rows), save_as)
        self.variables[save_as] = rows

    def _run_write_excel(self, step: Step, index: int) -> None:
        path = step.params.get("path")
        data_expr = step.params.get("data")
        if not path or not data_expr:
            raise StepError(index, step, ValueError("write_excel requires 'path' and 'data'"))
        try:
            rows = safe_eval(data_expr, self.variables)
        except ValueError as exc:
            raise StepError(index, step, exc) from exc
        try:
            from .excel import write_excel_rows

            count = write_excel_rows(path, list(rows), sheet=step.params.get("sheet"))
        except Exception as exc:  # noqa: BLE001 - wrap any openpyxl/file error with step context
            raise StepError(index, step, exc) from exc
        self._log("[%d] write_excel '%s' -> %d row(s)", index, path, count)
        if step.save_as:
            self.variables[step.save_as] = count

    def _run_http_request(self, step: Step, index: int) -> None:
        resolved = substitute_variables(step.params, self.variables)
        url = resolved.get("url")
        if not url:
            raise StepError(index, step, ValueError("http_request requires 'url'"))
        try:
            from .http_client import send_http_request

            result = send_http_request(
                method=resolved.get("method", "GET"),
                url=url,
                headers=resolved.get("headers"),
                params=resolved.get("params"),
                json_body=resolved.get("json"),
                data=resolved.get("data"),
                timeout=float(resolved.get("timeout", 30)),
            )
        except Exception as exc:  # noqa: BLE001 - wrap any network/requests error with step context
            raise StepError(index, step, exc) from exc
        self._log("[%d] http_request %s %s -> %s", index, resolved.get("method", "GET"), url, result["status_code"])
        if step.save_as:
            self.variables[step.save_as] = result

    def _run_get_credential(self, step: Step, index: int) -> None:
        name = substitute_variables(step.params.get("name", ""), self.variables)
        if not name or not step.save_as:
            raise StepError(index, step, ValueError("get_credential requires 'name' and save_as"))
        try:
            from .credentials import get_credential

            value = get_credential(name)
        except Exception as exc:  # noqa: BLE001 - wrap any keyring/backend error with step context
            raise StepError(index, step, exc) from exc
        # Deliberately never logs `value` - see _redact_secrets, which uses this set to
        # mask later step-parameter logging (e.g. if the credential is used in a `type` step).
        self._secrets.add(value)
        self._log("[%d] get_credential '%s' -> stored in '%s'", index, name, step.save_as)
        self.variables[step.save_as] = value

    def _run_send_email(self, step: Step, index: int) -> None:
        resolved = substitute_variables(step.params, self.variables)
        try:
            from .email_client import send_email

            send_email(
                smtp_host=resolved.get("smtp_host"),
                username=resolved.get("username"),
                password=resolved.get("password"),
                to=resolved.get("to"),
                subject=resolved.get("subject", ""),
                body=resolved.get("body", ""),
                smtp_port=int(resolved.get("smtp_port", 587)),
                use_tls=bool(resolved.get("use_tls", True)),
                from_addr=resolved.get("from_addr"),
            )
        except Exception as exc:  # noqa: BLE001 - wrap any smtplib error with step context
            raise StepError(index, step, exc) from exc
        self._log("[%d] send_email -> %s", index, resolved.get("to"))
        if step.save_as:
            self.variables[step.save_as] = {"sent": True}

    def _run_read_emails(self, step: Step, index: int) -> None:
        if not step.save_as:
            raise StepError(index, step, ValueError("read_emails requires save_as"))
        resolved = substitute_variables(step.params, self.variables)
        try:
            from .email_client import read_emails

            messages = read_emails(
                imap_host=resolved.get("imap_host"),
                username=resolved.get("username"),
                password=resolved.get("password"),
                folder=resolved.get("folder", "INBOX"),
                limit=int(resolved.get("limit", 10)),
                unseen_only=bool(resolved.get("unseen_only", True)),
                use_ssl=bool(resolved.get("use_ssl", True)),
                mark_as_read=bool(resolved.get("mark_as_read", False)),
            )
        except Exception as exc:  # noqa: BLE001 - wrap any imaplib error with step context
            raise StepError(index, step, exc) from exc
        self._log("[%d] read_emails -> %d message(s) into '%s'", index, len(messages), step.save_as)
        self.variables[step.save_as] = messages

    def _run_read_pdf(self, step: Step, index: int) -> None:
        path = step.params.get("path")
        if not path or not step.save_as:
            raise StepError(index, step, ValueError("read_pdf requires 'path' and save_as"))
        try:
            from .documents import read_pdf_text

            text = read_pdf_text(path, pages=step.params.get("pages"))
        except Exception as exc:  # noqa: BLE001 - wrap any pypdf/file error with step context
            raise StepError(index, step, exc) from exc
        self._log("[%d] read_pdf '%s' -> %d char(s) into '%s'", index, path, len(text), step.save_as)
        self.variables[step.save_as] = text

    def _run_ocr_image(self, step: Step, index: int) -> None:
        path = step.params.get("path")
        if not path or not step.save_as:
            raise StepError(index, step, ValueError("ocr_image requires 'path' and save_as"))
        try:
            from .documents import ocr_image_text

            text = ocr_image_text(path, lang=step.params.get("lang", "eng"))
        except Exception as exc:  # noqa: BLE001 - wrap any pytesseract/Tesseract-binary error with step context
            raise StepError(index, step, exc) from exc
        self._log("[%d] ocr_image '%s' -> %d char(s) into '%s'", index, path, len(text), step.save_as)
        self.variables[step.save_as] = text

    def _log(self, message: str, *args: Any) -> None:
        """Logs a step line with every interpolated argument run through
        _redact_secrets first. Step runners log *values* (an assigned variable,
        a switch subject, a resolved URL), any of which can carry a credential
        that get_credential pulled in - so redaction belongs here, once, rather
        than being remembered at each individual call site."""
        logger.info(message, *(self._redact_secrets(arg) for arg in args))

    def _redact_secrets(self, value: Any) -> Any:
        """Masks any credential value pulled in via get_credential before it's
        written to the job log - see _run_get_credential."""
        if not self._secrets:
            return value
        if isinstance(value, str):
            for secret in self._secrets:
                if secret and secret in value:
                    value = value.replace(secret, "***")
            return value
        if isinstance(value, dict):
            return {k: self._redact_secrets(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_secrets(v) for v in value]
        return value

    def _run_backend_step(self, step: Step, index: int) -> None:
        handler = getattr(self.backend, step.action, None)
        if not callable(handler):
            raise StepError(index, step, AttributeError(f"Backend has no action '{step.action}'"))
        resolved_params = substitute_variables(step.params, self.variables)
        self._log("[%d] %s(%s)", index, step.action, resolved_params)
        try:
            result = handler(**resolved_params)
        except Exception as exc:  # noqa: BLE001 - wrap any backend failure with step context
            raise StepError(index, step, exc) from exc
        if step.save_as:
            self.variables[step.save_as] = result
