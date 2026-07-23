"""Describes, per backend, which actions exist and which parameter fields the
Studio UI should render for each. This is a UI concern only - the engine itself
just calls whatever method matches the step's `action` name (see engine.py),
except the engine-level actions in _CONTROL_FLOW_AND_VARIABLES below (if,
switch, for_each, try, assign, increment, read_excel, write_excel,
http_request, get_credential, send_email, read_emails, read_pdf, ocr_image),
which the engine handles itself (not backend methods) and are therefore
identical for both backends."""

# Field types the Studio frontend knows how to render: text, number, checkbox,
# select (dropdown), json (textarea holding a JSON value - used for genuinely
# free-form JSON like http_request's headers/body), steps (a nested, fully
# editable list of sub-steps - used for if/for_each/try branches), cases (a
# switch statement's {value: [steps]} map, rendered as editable key + nested
# step list per case), hotkey (text input + "record" button that captures a
# key combo from the browser and fills it in).

_CONTROL_FLOW_AND_VARIABLES: dict[str, list[dict]] = {
    "if": [
        {"name": "condition", "label": "Bedingung (Python-Ausdruck, z.B. status == 'ok')", "type": "text", "required": True},
        {"name": "then", "label": "Dann", "type": "steps", "required": False},
        {"name": "else", "label": "Sonst", "type": "steps", "required": False},
    ],
    "switch": [
        {"name": "expression", "label": "Ausdruck (Python, z.B. land)", "type": "text", "required": True},
        {"name": "cases", "label": "Fälle", "type": "cases", "required": False},
        {"name": "default", "label": "Standard-Fall", "type": "steps", "required": False},
    ],
    "assign": [
        {"name": "variable", "label": "Variable", "type": "text", "required": True},
        {"name": "value", "label": "Wert (Text, erlaubt {var.x}/{item.x})", "type": "text", "required": False},
        {"name": "expression", "label": "oder: Ausdruck (Python, z.B. a + b)", "type": "text", "required": False},
    ],
    "increment": [
        {"name": "variable", "label": "Variable", "type": "text", "required": True},
        {"name": "by", "label": "Um wie viel", "type": "number", "required": False},
    ],
    "read_excel": [
        {"name": "path", "label": "Datei-Pfad (.xlsx)", "type": "text", "required": True},
        {"name": "sheet", "label": "Tabellenblatt (optional)", "type": "text", "required": False},
    ],
    "write_excel": [
        {"name": "path", "label": "Datei-Pfad (.xlsx)", "type": "text", "required": True},
        {"name": "data", "label": "Daten (Python-Ausdruck, z.B. kunden)", "type": "text", "required": True},
        {"name": "sheet", "label": "Tabellenblatt (optional)", "type": "text", "required": False},
    ],
    "for_each": [
        {"name": "items", "label": "Liste (Python-Ausdruck, z.B. kunden)", "type": "text", "required": True},
        {"name": "item_var", "label": "Variablenname pro Element (Standard: item)", "type": "text", "required": False},
        {"name": "index_var", "label": "Variablenname für Index (optional)", "type": "text", "required": False},
        {"name": "steps", "label": "Schleifenkörper", "type": "steps", "required": False},
    ],
    "try": [
        {"name": "steps", "label": "Versuchen", "type": "steps", "required": False},
        {"name": "catch", "label": "Bei Fehler", "type": "steps", "required": False},
        {"name": "error_var", "label": "Fehlermeldung speichern als (optional)", "type": "text", "required": False},
    ],
    "http_request": [
        {
            "name": "method",
            "label": "Methode",
            "type": "select",
            "options": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            "required": False,
        },
        {"name": "url", "label": "URL", "type": "text", "required": True},
        {"name": "headers", "label": "Headers (JSON)", "type": "json", "required": False},
        {"name": "params", "label": "Query-Parameter (JSON)", "type": "json", "required": False},
        {"name": "json", "label": "Body (JSON)", "type": "json", "required": False},
        {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
    ],
    "get_credential": [
        {"name": "name", "label": "Anmeldedaten-Name", "type": "text", "required": True},
    ],
    "send_email": [
        {"name": "smtp_host", "label": "SMTP-Server", "type": "text", "required": True},
        {"name": "smtp_port", "label": "SMTP-Port", "type": "number", "required": False},
        {"name": "username", "label": "Benutzername", "type": "text", "required": True},
        {"name": "password", "label": "Passwort (z.B. {var.smtp_password})", "type": "text", "required": True},
        {"name": "to", "label": "An", "type": "text", "required": True},
        {"name": "subject", "label": "Betreff", "type": "text", "required": True},
        {"name": "body", "label": "Text", "type": "text", "required": True},
        {"name": "use_tls", "label": "TLS verwenden", "type": "checkbox", "required": False},
    ],
    "read_emails": [
        {"name": "imap_host", "label": "IMAP-Server", "type": "text", "required": True},
        {"name": "username", "label": "Benutzername", "type": "text", "required": True},
        {"name": "password", "label": "Passwort (z.B. {var.imap_password})", "type": "text", "required": True},
        {"name": "folder", "label": "Ordner (Standard: INBOX)", "type": "text", "required": False},
        {"name": "limit", "label": "Max. Anzahl", "type": "number", "required": False},
        {"name": "unseen_only", "label": "Nur ungelesene", "type": "checkbox", "required": False},
        {"name": "use_ssl", "label": "SSL verwenden", "type": "checkbox", "required": False},
    ],
    "read_pdf": [
        {"name": "path", "label": "Datei-Pfad (.pdf)", "type": "text", "required": True},
        {"name": "pages", "label": "Seiten (z.B. 1,3-5, optional = alle)", "type": "text", "required": False},
    ],
    "ocr_image": [
        {"name": "path", "label": "Datei-Pfad (Bild)", "type": "text", "required": True},
        {"name": "lang", "label": "Sprache (z.B. eng, deu)", "type": "text", "required": False},
    ],
}

ACTION_SCHEMAS: dict[str, dict[str, list[dict]]] = {
    "web": {
        "navigate": [
            {"name": "url", "label": "URL", "type": "text", "required": True},
        ],
        "click": [
            {"name": "selector", "label": "Selector", "type": "text", "required": True},
            {"name": "timeout", "label": "Timeout (ms)", "type": "number", "required": False},
        ],
        "type": [
            {"name": "selector", "label": "Selector", "type": "text", "required": True},
            {"name": "text", "label": "Text", "type": "text", "required": True},
            {"name": "timeout", "label": "Timeout (ms)", "type": "number", "required": False},
        ],
        "get_text": [
            {"name": "selector", "label": "Selector", "type": "text", "required": True},
            {"name": "timeout", "label": "Timeout (ms)", "type": "number", "required": False},
        ],
        "send_hotkey": [
            {"name": "keys", "label": "Tastenkombination", "type": "hotkey", "required": True},
        ],
        "wait_for_selector": [
            {"name": "selector", "label": "Selector", "type": "text", "required": True},
            {"name": "timeout", "label": "Timeout (ms)", "type": "number", "required": False},
            {
                "name": "state",
                "label": "State",
                "type": "select",
                "options": ["visible", "hidden", "attached", "detached"],
                "required": False,
            },
        ],
        "wait": [
            {"name": "seconds", "label": "Seconds", "type": "number", "required": True},
        ],
        "screenshot": [
            {"name": "path", "label": "Output path", "type": "text", "required": True},
        ],
        **_CONTROL_FLOW_AND_VARIABLES,
    },
    "desktop": {
        "launch": [
            {"name": "path", "label": "Executable path", "type": "text", "required": True},
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "connect": [
            {"name": "title", "label": "Window title", "type": "text", "required": False},
            {"name": "title_re", "label": "Window title (regex)", "type": "text", "required": False},
            {"name": "process", "label": "Process ID", "type": "number", "required": False},
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "wait_for_element": [
            {"name": "control_type", "label": "Control type", "type": "text", "required": False},
            {"name": "title", "label": "Title", "type": "text", "required": False},
            {"name": "auto_id", "label": "Auto ID", "type": "text", "required": False},
            {
                "name": "state",
                "label": "Zustand",
                "type": "select",
                "options": ["exists", "gone"],
                "required": False,
            },
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "click": [
            {"name": "control_type", "label": "Control type", "type": "text", "required": False},
            {"name": "title", "label": "Title", "type": "text", "required": False},
            {"name": "auto_id", "label": "Auto ID", "type": "text", "required": False},
            {"name": "double", "label": "Double click", "type": "checkbox", "required": False},
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "type": [
            {"name": "control_type", "label": "Control type", "type": "text", "required": False},
            {"name": "title", "label": "Title", "type": "text", "required": False},
            {"name": "auto_id", "label": "Auto ID", "type": "text", "required": False},
            {"name": "text", "label": "Text", "type": "text", "required": True},
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "get_text": [
            {"name": "control_type", "label": "Control type", "type": "text", "required": False},
            {"name": "title", "label": "Title", "type": "text", "required": False},
            {"name": "auto_id", "label": "Auto ID", "type": "text", "required": False},
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "send_hotkey": [
            {"name": "keys", "label": "Tastenkombination", "type": "hotkey", "required": True},
        ],
        "wait": [
            {"name": "seconds", "label": "Seconds", "type": "number", "required": True},
        ],
        "screenshot": [
            {"name": "path", "label": "Output path", "type": "text", "required": True},
        ],
        **_CONTROL_FLOW_AND_VARIABLES,
    },
}
