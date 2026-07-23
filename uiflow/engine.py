from __future__ import annotations

import builtins
import logging
import re
from typing import Any, Callable, Optional

from .models import Step, Workflow

logger = logging.getLogger("uiflow")

OnBreakpoint = Callable[[int, Step, "dict[str, Any]"], None]
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
    (`if`, `switch`, `for_each`, `try`, `assign`, `increment`, `read_excel`,
    `write_excel`, `http_request`, `get_credential`, `send_email`,
    `read_emails`, `read_pdf`, `ocr_image`), since they operate on
    workflow-run-scoped `variables` (or external services) rather than the UI.

    Step numbering ("[N] ...") is a single counter across the whole run, in
    execution order - branches that aren't taken never consume a number, so
    there's no meaningful upfront "total steps" to log (unlike the old flat-only
    engine), only a running count.
    """

    def __init__(self, backend: object):
        self.backend = backend
        self.variables: dict[str, Any] = {}
        # Values pulled in via `get_credential`, tracked so step-parameter
        # logging (see _redact) can mask them instead of writing secrets to
        # the job log / console in plain text.
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
        logger.info("Running workflow '%s' on backend=%s", workflow.name, workflow.backend)
        self._run_steps(workflow.steps, on_breakpoint, should_stop)
        logger.info("Workflow '%s' completed successfully", workflow.name)

    def _run_steps(
        self, steps: list[Step], on_breakpoint: Optional[OnBreakpoint], should_stop: Optional[ShouldStop]
    ) -> None:
        for step in steps:
            if should_stop is not None and should_stop():
                index = self._counter + 1
                logger.info("Workflow abgebrochen vor Schritt %d", index)
                raise WorkflowCancelled(index)
            self._counter += 1
            index = self._counter

            if step.breakpoint and on_breakpoint is not None:
                logger.info("[%d] Haltepunkt bei '%s'", index, step.action)
                on_breakpoint(index, step, self._redact_secrets(dict(self.variables)))
                if should_stop is not None and should_stop():
                    logger.info("Workflow abgebrochen vor Schritt %d", index)
                    raise WorkflowCancelled(index)

            if step.action == "if":
                self._run_if(step, index, on_breakpoint, should_stop)
            elif step.action == "switch":
                self._run_switch(step, index, on_breakpoint, should_stop)
            elif step.action == "for_each":
                self._run_for_each(step, index, on_breakpoint, should_stop)
            elif step.action == "try":
                self._run_try(step, index, on_breakpoint, should_stop)
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
        self, step: Step, index: int, on_breakpoint: Optional[OnBreakpoint], should_stop: Optional[ShouldStop]
    ) -> None:
        condition = step.params.get("condition", "False")
        try:
            result = bool(safe_eval(condition, self.variables))
        except ValueError as exc:
            raise StepError(index, step, exc) from exc
        logger.info("[%d] if %s -> %s", index, condition, result)
        branch = step.params.get("then") if result else step.params.get("else")
        self._run_steps(self._sub_steps(branch), on_breakpoint, should_stop)

    def _run_switch(
        self, step: Step, index: int, on_breakpoint: Optional[OnBreakpoint], should_stop: Optional[ShouldStop]
    ) -> None:
        expression = step.params.get("expression", "")
        try:
            value = safe_eval(expression, self.variables)
        except ValueError as exc:
            raise StepError(index, step, exc) from exc
        cases = step.params.get("cases") or {}
        branch = cases.get(str(value))
        if branch is None:
            branch = step.params.get("default")
        logger.info("[%d] switch %s == %r", index, expression, value)
        self._run_steps(self._sub_steps(branch), on_breakpoint, should_stop)

    def _run_for_each(
        self, step: Step, index: int, on_breakpoint: Optional[OnBreakpoint], should_stop: Optional[ShouldStop]
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
        logger.info("[%d] for_each %s -> %d item(s)", index, items_expr, len(items))
        for i, value in enumerate(items):
            self.variables[item_var] = value
            if index_var:
                self.variables[index_var] = i
            self._run_steps(body, on_breakpoint, should_stop)

    def _run_try(
        self, step: Step, index: int, on_breakpoint: Optional[OnBreakpoint], should_stop: Optional[ShouldStop]
    ) -> None:
        try_body = self._sub_steps(step.params.get("steps"))
        catch_body = self._sub_steps(step.params.get("catch"))
        error_var = step.params.get("error_var")
        logger.info("[%d] try", index)
        try:
            self._run_steps(try_body, on_breakpoint, should_stop)
        except WorkflowCancelled:
            raise  # a user-requested stop must propagate, not be swallowed as a "handled" error
        except (StepError, ValueError) as exc:
            message = str(exc)
            logger.info("[%d] try: caught error -> %s", index, message)
            if error_var:
                self.variables[error_var] = message
            self._run_steps(catch_body, on_breakpoint, should_stop)

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
        logger.info("[%d] assign %s = %r", index, name, value)
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
        logger.info("[%d] increment %s -> %s", index, name, new_value)
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
        logger.info("[%d] read_excel '%s' -> %d row(s) into '%s'", index, path, len(rows), save_as)
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
        logger.info("[%d] write_excel '%s' -> %d row(s)", index, path, count)
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
        logger.info("[%d] http_request %s %s -> %s", index, resolved.get("method", "GET"), url, result["status_code"])
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
        logger.info("[%d] get_credential '%s' -> stored in '%s'", index, name, step.save_as)
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
        logger.info("[%d] send_email -> %s", index, resolved.get("to"))
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
            )
        except Exception as exc:  # noqa: BLE001 - wrap any imaplib error with step context
            raise StepError(index, step, exc) from exc
        logger.info("[%d] read_emails -> %d message(s) into '%s'", index, len(messages), step.save_as)
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
        logger.info("[%d] read_pdf '%s' -> %d char(s) into '%s'", index, path, len(text), step.save_as)
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
        logger.info("[%d] ocr_image '%s' -> %d char(s) into '%s'", index, path, len(text), step.save_as)
        self.variables[step.save_as] = text

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
        logger.info("[%d] %s(%s)", index, step.action, self._redact_secrets(resolved_params))
        try:
            result = handler(**resolved_params)
        except Exception as exc:  # noqa: BLE001 - wrap any backend failure with step context
            raise StepError(index, step, exc) from exc
        if step.save_as:
            self.variables[step.save_as] = result
