"""Describes, per backend, which actions exist and which parameter fields the
Studio UI should render for each. This is a UI concern only - the engine itself
just calls whatever method matches the step's `action` name (see engine.py),
except the engine-level actions in _CONTROL_FLOW_AND_VARIABLES below (if,
switch, for_each, try, run_workflow, assign, increment, read_excel,
write_excel, http_request, get_credential, send_email, read_emails, read_pdf,
ocr_image),
which the engine handles itself (not backend methods) and are therefore
identical for both backends."""

# Field types the Studio frontend knows how to render: text, number, checkbox,
# select (dropdown), json (textarea holding a JSON value - used for genuinely
# free-form JSON like http_request's headers/body), steps (a nested, fully
# editable list of sub-steps - used for if/for_each/try branches), cases (a
# switch statement's {value: [steps]} map, rendered as editable key + nested
# step list per case), hotkey (text input + "record" button that captures a
# key combo from the browser and fills it in), workflow (text input backed by a
# datalist of the workflows that exist, for referencing a sub-workflow by name).

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
    "run_workflow": [
        {"name": "workflow", "label": "Workflow", "type": "workflow", "required": True},
        {
            "name": "arguments",
            "label": 'Argumente (JSON, z.B. {"kunde": "{var.name}"})',
            "type": "json",
            "required": False,
        },
        {
            "name": "outputs",
            "label": 'Rückgaben (JSON: Variable im Unterprozess -> Variable hier)',
            "type": "json",
            "required": False,
        },
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
        {"name": "mark_as_read", "label": "Abgerufene Mails als gelesen markieren", "type": "checkbox", "required": False},
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
            {
                "name": "button",
                "label": "Maustaste",
                "type": "select",
                "options": ["left", "right", "middle"],
                "required": False,
            },
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "drag": [
            {"name": "control_type", "label": "Control type", "type": "text", "required": False},
            {"name": "title", "label": "Title", "type": "text", "required": False},
            {"name": "auto_id", "label": "Auto ID", "type": "text", "required": False},
            {"name": "to_x", "label": "Ziel X (Bildschirm-Pixel)", "type": "number", "required": True},
            {"name": "to_y", "label": "Ziel Y (Bildschirm-Pixel)", "type": "number", "required": True},
            {
                "name": "button",
                "label": "Maustaste",
                "type": "select",
                "options": ["left", "right", "middle"],
                "required": False,
            },
            {"name": "timeout", "label": "Timeout (s)", "type": "number", "required": False},
        ],
        "scroll": [
            {"name": "control_type", "label": "Control type", "type": "text", "required": False},
            {"name": "title", "label": "Title", "type": "text", "required": False},
            {"name": "auto_id", "label": "Auto ID", "type": "text", "required": False},
            {"name": "amount", "label": "Menge (positiv = hoch, negativ = runter)", "type": "number", "required": False},
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


# --- activity catalog metadata ---------------------------------------------
#
# ACTION_SCHEMAS says which *fields* an action has; the catalog needs the other
# half - what the activity is called in plain German, what it does, and where it
# belongs in the palette. Kept separate so the field schemas stay the single
# source of truth for the form rendering, and a missing entry here degrades to
# "uncategorised" rather than breaking the builder.
#
# `keywords` exist purely for the catalog search: they let "schleife" find
# for_each and "wenn" find if, without those words having to appear in the
# label. `primary` lists, in order of preference, the fields a collapsed
# activity card may show as its one-line summary (see stepSummary in app.js) -
# a list rather than one name because the same action can target different
# fields per backend, e.g. web `click` has a selector where desktop `click`
# has a window title.

CATEGORY_ORDER = [
    "Anwendung",
    "UI-Interaktion",
    "Warten",
    "Ablaufsteuerung",
    "Variablen",
    "Dateien & Dokumente",
    "Integration",
]

ACTION_META: dict[str, dict] = {
    "launch": {
        "label": "Anwendung starten",
        "category": "Anwendung",
        "description": "Startet eine Desktop-Anwendung und macht sie zum Scope des Workflows.",
        "keywords": ["exe", "programm", "öffnen", "start"],
        "primary": ["path"],
    },
    "connect": {
        "label": "Anwendung verbinden",
        "category": "Anwendung",
        "description": "Hängt sich an ein bereits laufendes Fenster an, statt es neu zu starten.",
        "keywords": ["attach", "fenster", "vorhandene"],
        "primary": ["title"],
    },
    "navigate": {
        "label": "Seite öffnen",
        "category": "UI-Interaktion",
        "description": "Ruft eine URL im Browser auf.",
        "keywords": ["url", "browser", "webseite", "goto"],
        "primary": ["url"],
    },
    "click": {
        "label": "Klicken",
        "category": "UI-Interaktion",
        "description": "Klickt das angegebene Element an.",
        "keywords": ["maus", "button", "schaltfläche", "drücken", "rechtsklick"],
        "primary": ["selector", "title", "auto_id"],
    },
    "drag": {
        "label": "Ziehen (Drag & Drop)",
        "category": "UI-Interaktion",
        "description": "Zieht das angegebene Element per Maus an eine Bildschirmposition.",
        "keywords": ["drag", "ziehen", "verschieben", "drop"],
        "primary": ["title", "auto_id"],
    },
    "scroll": {
        "label": "Scrollen",
        "category": "UI-Interaktion",
        "description": "Bewegt das Mausrad über dem angegebenen Element.",
        "keywords": ["scrollen", "mausrad", "wheel", "runter", "hoch"],
        "primary": ["title", "auto_id"],
    },
    "type": {
        "label": "Text eingeben",
        "category": "UI-Interaktion",
        "description": "Tippt Text in ein Eingabefeld.",
        "keywords": ["tippen", "schreiben", "eingabe", "formular"],
        "primary": ["text"],
    },
    "get_text": {
        "label": "Text auslesen",
        "category": "UI-Interaktion",
        "description": "Liest den Text eines Elements in eine Variable.",
        "keywords": ["lesen", "auslesen", "scrapen", "inhalt"],
        "primary": ["selector", "title", "auto_id"],
    },
    "send_hotkey": {
        "label": "Tastenkombination senden",
        "category": "UI-Interaktion",
        "description": "Sendet eine Tastenkombination wie Strg+S an die Anwendung.",
        "keywords": ["shortcut", "tastatur", "strg", "hotkey"],
        "primary": ["keys"],
    },
    "screenshot": {
        "label": "Screenshot aufnehmen",
        "category": "UI-Interaktion",
        "description": "Speichert ein Bildschirmfoto als Datei.",
        "keywords": ["bild", "foto", "beweis", "png"],
        "primary": ["path"],
    },
    "wait": {
        "label": "Feste Zeit warten",
        "category": "Warten",
        "description": "Pausiert den Workflow für eine feste Anzahl Sekunden.",
        "keywords": ["pause", "sleep", "verzögerung", "sekunden"],
        "primary": ["seconds"],
    },
    "wait_for_selector": {
        "label": "Auf Element warten",
        "category": "Warten",
        "description": "Wartet, bis ein Element auf der Seite erscheint.",
        "keywords": ["laden", "erscheinen", "timeout", "sichtbar"],
        "primary": ["selector"],
    },
    "wait_for_element": {
        "label": "Auf Element warten",
        "category": "Warten",
        "description": "Wartet, bis ein Fenster-Element verfügbar ist.",
        "keywords": ["laden", "erscheinen", "timeout", "sichtbar"],
        "primary": ["title", "auto_id"],
    },
    "if": {
        "label": "Wenn / Sonst",
        "category": "Ablaufsteuerung",
        "description": "Führt je nach Bedingung den Dann- oder den Sonst-Zweig aus.",
        "keywords": ["bedingung", "verzweigung", "entscheidung", "prüfen"],
        "primary": ["condition"],
    },
    "switch": {
        "label": "Fallunterscheidung",
        "category": "Ablaufsteuerung",
        "description": "Wählt anhand eines Werts einen von mehreren Fällen aus.",
        "keywords": ["case", "mehrfach", "auswahl", "verzweigung"],
        "primary": ["expression"],
    },
    "for_each": {
        "label": "Für jedes Element",
        "category": "Ablaufsteuerung",
        "description": "Wiederholt die enthaltenen Schritte für jeden Eintrag einer Liste.",
        "keywords": ["schleife", "loop", "wiederholen", "iterieren"],
        "primary": ["items"],
    },
    "try": {
        "label": "Versuchen / Bei Fehler",
        "category": "Ablaufsteuerung",
        "description": "Fängt Fehler der enthaltenen Schritte ab und behandelt sie.",
        "keywords": ["fehler", "catch", "exception", "absichern"],
        "primary": ["error_var"],
    },
    "run_workflow": {
        "label": "Unterprozess ausführen",
        "category": "Ablaufsteuerung",
        "description": "Führt einen anderen Workflow als Baustein aus, mit eigenen Variablen.",
        "keywords": ["unterprozess", "aufrufen", "wiederverwenden", "baustein", "invoke"],
        "primary": ["workflow"],
    },
    "assign": {
        "label": "Variable zuweisen",
        "category": "Variablen",
        "description": "Schreibt einen Wert oder das Ergebnis eines Ausdrucks in eine Variable.",
        "keywords": ["setzen", "wert", "berechnen", "speichern"],
        "primary": ["variable"],
    },
    "increment": {
        "label": "Zähler erhöhen",
        "category": "Variablen",
        "description": "Erhöht eine numerische Variable um einen Betrag.",
        "keywords": ["zähler", "counter", "addieren", "hochzählen"],
        "primary": ["variable"],
    },
    "read_excel": {
        "label": "Excel lesen",
        "category": "Dateien & Dokumente",
        "description": "Liest die Zeilen einer Excel-Datei als Liste von Datensätzen.",
        "keywords": ["xlsx", "tabelle", "import", "zeilen"],
        "primary": ["path"],
    },
    "write_excel": {
        "label": "Excel schreiben",
        "category": "Dateien & Dokumente",
        "description": "Schreibt eine Liste von Datensätzen in eine Excel-Datei.",
        "keywords": ["xlsx", "tabelle", "export", "speichern"],
        "primary": ["path"],
    },
    "read_pdf": {
        "label": "PDF-Text lesen",
        "category": "Dateien & Dokumente",
        "description": "Extrahiert den Text aus einer PDF-Datei.",
        "keywords": ["pdf", "dokument", "text", "extrahieren"],
        "primary": ["path"],
    },
    "ocr_image": {
        "label": "Bild per OCR lesen",
        "category": "Dateien & Dokumente",
        "description": "Erkennt Text in einem Bild per Texterkennung.",
        "keywords": ["ocr", "scan", "bild", "texterkennung"],
        "primary": ["path"],
    },
    "http_request": {
        "label": "HTTP-Anfrage",
        "category": "Integration",
        "description": "Ruft eine REST-Schnittstelle auf und speichert die Antwort.",
        "keywords": ["api", "rest", "webservice", "get", "post"],
        "primary": ["url"],
    },
    "get_credential": {
        "label": "Anmeldedaten holen",
        "category": "Integration",
        "description": "Lädt ein Geheimnis aus dem Anmeldeinformationsspeicher in eine Variable.",
        "keywords": ["passwort", "secret", "keyring", "zugangsdaten"],
        "primary": ["name"],
    },
    "send_email": {
        "label": "E-Mail senden",
        "category": "Integration",
        "description": "Verschickt eine E-Mail über einen SMTP-Server.",
        "keywords": ["mail", "smtp", "versenden", "benachrichtigung"],
        "primary": ["to"],
    },
    "read_emails": {
        "label": "E-Mails lesen",
        "category": "Integration",
        "description": "Holt Nachrichten per IMAP aus einem Postfach.",
        "keywords": ["mail", "imap", "posteingang", "abrufen"],
        "primary": ["folder"],
    },
}


def activity_catalog() -> dict:
    """Catalog payload for the Studio's activity palette: every action that
    exists for a backend, enriched with its ACTION_META entry (or a plain
    fallback, so a newly added action still shows up instead of vanishing)."""
    catalog: dict[str, list[dict]] = {}
    for backend, actions in ACTION_SCHEMAS.items():
        entries = []
        for name in actions:
            meta = ACTION_META.get(name, {})
            entries.append(
                {
                    "name": name,
                    "label": meta.get("label", name),
                    "category": meta.get("category", "Weitere"),
                    "description": meta.get("description", ""),
                    "keywords": meta.get("keywords", []),
                    "primary": meta.get("primary", []),
                }
            )
        catalog[backend] = entries
    return {"categories": CATEGORY_ORDER, "activities": catalog}
