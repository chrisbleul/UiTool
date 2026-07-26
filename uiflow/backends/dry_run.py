"""A backend that never touches a real browser or Windows app - every UI
action is a no-op that logs what it would have done and returns a harmless
placeholder, so a `save_as` still gets *some* value and a later expression
referencing it doesn't raise NameError. Paired with engine.py's `dry_run`
flag (which additionally skips the handful of engine-level actions with a
genuine external side effect - http_request, send_email, read_emails,
write_excel), this lets a whole workflow run end-to-end against nothing but
itself: catches typos in Python expressions, references to undeclared
variables, and other structural mistakes, without needing the real target
application, network, or mailbox at all.

Deliberately uses __getattr__ rather than one method per known action name -
new activities added to WebBackend/DesktopBackend later are covered for
free, with no risk of this drifting out of sync with their actual method
list. `backend_class` (WebBackend or DesktopBackend, matching the workflow's
own `backend`) is only consulted to reject an action name *neither* of them
implements - the same "Backend has no action 'X'" a real run would raise
(see engine.py's _run_backend_step) - so a typo'd action name is still
caught, not silently treated as a no-op.

What this does *not* catch: a parameter name typo (e.g. "selectr" instead of
"selector") - a real backend's method signature would reject an unexpected
keyword argument, but every no-op method here accepts (and ignores) any
**kwargs at all. Checking parameter names against the actual backend classes
would be a separate, static check, not something exercised by literally
running the workflow.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("uiflow")


class DryRunBackend:
    def __init__(self, backend_class: type):
        self._backend_class = backend_class

    def close(self) -> None:
        pass

    def element_exists(self, **kwargs: Any) -> bool:
        # Optimistic default: a dry run isn't meant to validate real selector
        # matches (that's what the Studio's "Selector prüfen" button is for)
        # - assuming every element is found keeps Object Repository fallback
        # candidate selection from spuriously always picking the first one.
        return True

    def __getattr__(self, name: str) -> Any:
        if not hasattr(self._backend_class, name):
            raise AttributeError(name)

        def _noop(**kwargs: Any) -> str:
            logger.info("[dry-run] %s(%s) -> übersprungen", name, kwargs)
            return ""

        return _noop
