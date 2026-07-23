# uiflow

MVP eines UI-Automatisierungstools (à la UiPath), das **Windows-Desktop-Apps** und
**Browser** über ein gemeinsames YAML-Workflow-Format steuert.

> Hinweis: `DoubleUI.exe` im Repo-Root ist ein unabhängiges Altprojekt (eine
> WPF-Banktransaktions-Simulation) und hat mit `uiflow` nichts zu tun.

## Architektur

```
uiflow/
  models.py          Workflow/Step-Datenmodell + YAML-Loader
  engine.py           WorkflowEngine: führt Steps sequenziell auf einem Backend aus,
                      inkl. aller Engine-Level-Aktionen (if/switch/for_each/try/... - siehe unten)
  excel.py             Excel lesen/schreiben (openpyxl)
  http_client.py        HTTP/REST-Requests (requests)
  email_client.py        E-Mail senden (SMTP) / lesen (IMAP)
  documents.py           PDF-Text-Extraktion (pypdf) + OCR (pytesseract, braucht Tesseract-Binary)
  credentials.py          Anmeldedaten-Speicher über den OS-Credential-Store (keyring)
  backends/
    web.py            Browser-Automatisierung via Playwright
    desktop.py         Windows-Automatisierung via pywinauto (UI Automation)
  cli.py               `uiflow run ...` / `uiflow inspect-desktop ...` / `uiflow studio` /
                      `uiflow worker` / `uiflow scheduler`
  orchestrator/
    db.py                Persistenter Job-/Log-/Queue-/Credential-Namen-/Zeitplan-Store (SQLite, WAL-Modus)
    worker.py             Job-Ausführung (run_worker_loop) + Cron-Zeitpläne (run_scheduler_loop)
  studio/
    schema.py           Beschreibt pro Backend, welche Actions es gibt und welche Formularfelder sie brauchen
    app.py              Flask-App: REST-API (Schema/Workflows/Jobs/Queues/Credentials/Schedules/Pick)
                        + SSE-Log-Streaming + optionales Login
    picker.py            "Element wählen": Klick-zu-Selektor-Erkennung für Web (Playwright) und Desktop (pynput + UI Automation)
    static/              Low-Code-Builder-Frontend (HTML/CSS/Vanilla JS, kein Build-Step)
workflows/             Beispiel-Workflows (werden auch vom Studio gelesen/geschrieben)
orchestrator.db         SQLite-Datei (wird beim ersten Start angelegt, nicht eingecheckt)
tests/                 Unit-Tests für Modell + Engine + Orchestrator-DB + Studio-API (Backends/externe
                       Dienste sind gemockt)
```

Ein Workflow ist eine Liste von Steps. Jeder Step hat einen `action`-Namen, der 1:1 auf
eine Methode des gewählten Backends gemappt wird (`click`, `type`, `navigate`, `launch`, ...).
Das hält den Kern klein: neue Fähigkeiten = neue Backend-Methode, kein Engine-Umbau.

## Setup

```powershell
cd "Sandbox\UiTool"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium   # nur für Web-Workflows nötig
```

## Nutzung

Web-Workflow ausführen:

```powershell
python -m uiflow.cli run workflows\example_web.yaml
```

Desktop-Workflow ausführen (öffnet Notepad, tippt Text):

```powershell
python -m uiflow.cli run workflows\example_desktop.yaml
```

Elemente eines offenen Fensters inspizieren, um Selektoren für einen Desktop-Workflow zu finden:

```powershell
python -m uiflow.cli inspect-desktop "Editor" --depth 3
```

## Low-Code Builder (uiflow studio)

Lokale Web-App zum Bauen von Workflows per Formular statt YAML von Hand:

```powershell
python -m uiflow.cli studio
```

Öffnet `http://127.0.0.1:8787` im Browser. Dort:
- Workflow-Name + Backend (Web/Desktop) wählen
- Schritte per "+ Schritt hinzufügen" ergänzen, Action aus Dropdown wählen, Parameter im Formular ausfüllen
- Schritte per Drag & Drop (Griff "⠿" links) oder ↑/↓ umsortieren, per ✕ löschen
- Der kleine Kreis links neben der Schritt-Nummer setzt/entfernt einen **Haltepunkt** auf diesem Schritt
  (rot = aktiv). Beim Ausführen pausiert der Workflow direkt davor; ein "Weiter"-Button erscheint im
  Log-Panel, um fortzufahren. Haltepunkte werden mit in die YAML gespeichert (`breakpoint: true`).
- **Speichern** schreibt die YAML-Datei in `workflows/`
- **Workflow laden...** lädt eine bestehende YAML-Datei zurück in den Builder
- **Ausführen** startet den Workflow im Hintergrund und zeigt die Log-Ausgabe live (Server-Sent Events);
  war der letzte Schritt ein `screenshot`, wird das Ergebnisbild direkt im Log-Panel angezeigt
- **Stoppen** bricht einen laufenden Workflow ab (auch während er an einem Haltepunkt pausiert).
  Der Abbruch greift vor dem jeweils nächsten Schritt — ein bereits laufender einzelner Schritt
  (z.B. ein sehr langes `wait`) läuft noch zu Ende, bevor gestoppt wird.
- **🎯 Element auf dem Bildschirm wählen**: erscheint bei Web-Steps mit einem Selector-Feld
  (öffnet dazu einen sichtbaren Browser auf der URL des vorherigen `navigate`-Steps, oder fragt
  danach) bzw. bei Desktop-Steps mit `control_type`/`title`/`auto_id`-Feldern. Klick auf den Button,
  dann auf das gewünschte Element klicken — der/die Selektor-Felder werden automatisch befüllt.
  - **Anwendungs-Scope**: Enthält der Workflow bereits einen `launch`- oder `connect`-Schritt,
    gilt dessen Anwendung als Scope — sie wird vor der Aufnahme automatisch in den Vordergrund
    geholt (kein manuelles Alt-Tab nötig). Dasselbe passiert auch bei der echten Ausführung: jede
    Desktop-Aktion (`click`/`type`/`wait_for_element`) holt die Scope-Anwendung zuerst in den
    Vordergrund, bevor sie das Element anspricht.
  - **Timeout**: vor jeder Aufnahme wird nach einer Wartezeit (Sekunden) gefragt — mit erkanntem
    Scope reicht meist `0` (Vordergrund-Holen ist bereits genug Zeit), ohne Scope Default `3`, um
    manuell zur Zielanwendung zu wechseln.
  - **Live-Umrandung (nur Desktop)**: während der Aufnahme wird das Element unter dem Mauszeiger
    laufend mit einem roten Rahmen markiert (wie UiPaths "Auf Bildschirm anzeigen"), bevor geklickt wird.
- **↶ Rückgängig** (oder Strg+Z) macht die letzte Änderung rückgängig — Schritt hinzugefügt/gelöscht/
  verschoben, Haltepunkt umgeschaltet, Feld bearbeitet, Backend gewechselt, Selektor übernommen.
- **Anwendungs-Scope & Sequenz**: Ist der erste Schritt eines Desktop-Workflows `launch` oder `connect`,
  wird er visuell als übergeordneter Scope dargestellt, alle weiteren Schritte erscheinen eingerückt
  darunter als "Sequenz" — analog zu UiPaths "Use Application/Browser"-Aktivität.
- **🔴 Aufnahme starten** (im Scope-Bereich, sobald ein `launch`/`connect`-Schritt existiert): zeichnet
  echte Klicks und Texteingaben in der Zielanwendung live als Workflow-Schritte auf. Text wird gepuffert
  und beim nächsten Klick oder mit Tab/Enter als `type`-Schritt übernommen; Klicks außerhalb der
  Scope-Anwendung werden ignoriert. **⏹ Aufnahme stoppen** beendet die Sitzung (verbleibender
  Text wird noch als letzter Schritt übernommen).

`--port`/`--host`/`--no-browser` stehen als Flags zur Verfügung. `uiflow run` (CLI ohne Studio)
unterstützt Haltepunkte ebenfalls: die Ausführung stoppt im Terminal mit "Weiter mit Enter...".

## Orchestrator: Jobs, Logging, Queues

`uiflow studio` klickt Workflows nicht mehr direkt an — "Ausführen" reiht einen **Job** in eine
persistente SQLite-Warteschlange (`orchestrator.db`) ein, ein **Worker** holt ihn sich ab und führt
ihn aus. Der Studio-Prozess startet standardmäßig einen eingebetteten Worker-Thread mit (`--no-worker`
zum Abschalten), sodass "ausführen" sich weiterhin wie gewohnt anfühlt — dieselbe Job-Queue kann aber
auch von einem eigenständigen Worker-Prozess bedient werden:

```powershell
python -m uiflow.cli worker --worker-id robot-1
```

Mehrere Worker (auf derselben oder später verteilt auf anderen Maschinen) können parallel gegen
dieselbe Queue arbeiten — `claim_next_job`/`claim_next_queue_item` beanspruchen Zeilen atomar
(`UPDATE ... WHERE status='queued'`), sodass zwei Worker nie denselben Job doppelt ausführen.

**Jobs sind jetzt durable**: Status, Zeitstempel und die komplette Log-Historie überleben einen
Neustart des Studio-Prozesses und sind über die API abrufbar, nicht nur live per SSE:

```
POST /api/run              Job einreihen (wie bisher; optional queue_name für Queue-gesteuerte Jobs)
GET  /api/jobs             Job-Liste (Filter: ?status=queued|running|success|error|cancelled)
GET  /api/jobs/<id>        Job-Detail inkl. Workflow-Snapshot zum Zeitpunkt des Einreihens
GET  /api/jobs/<id>/logs   Komplette persistente Log-Historie
```

**Work-Item-Queues** sind Datenstrukturen für "N Elemente abarbeiten" (wie UiPaths Queues):

```
POST /api/queues                    Queue anlegen ({"name": "rechnungen"})
POST /api/queues/<name>/items       Items hinzufügen ({"items": [{"payload": {...}}, ...]})
GET  /api/queues                    Liste mit Zählern (new/in_progress/success/failed)
GET  /api/queues/<name>/items       Items auflisten (Filter: ?status=...)
```

Referenziert ein Job beim Einreihen eine Queue (`queue_name`), verarbeitet der Worker sie im
"Process Transaction"-Muster: ein Workflow-Lauf pro Item, bis die Queue leer ist oder gestoppt wird.
Fehlgeschlagene Items werden automatisch bis `max_retries` erneut versucht, bevor sie als `failed`
markiert werden. Innerhalb der Step-Parameter stehen Platzhalter `{item.<feld>}` zur Verfügung, die
pro Item aus dessen `payload` ersetzt werden:

```yaml
name: Rechnung buchen
backend: web
queue_name: rechnungen   # nicht Teil der YAML selbst, sondern beim Einreihen (POST /api/run) angegeben
steps:
  - action: navigate
    url: "https://intern/rechnung/{item.rechnungsnummer}"
  - action: type
    selector: "#betrag"
    text: "{item.betrag}"
```

## Workflow-Format

```yaml
name: Beispielname
backend: web        # oder: desktop
steps:
  - action: navigate
    url: "https://example.com"
  - action: click
    selector: "#submit"
```

Web-Actions (siehe `uiflow/backends/web.py`): `navigate`, `click`, `type`, `get_text`, `send_hotkey`,
`wait_for_selector`, `wait`, `screenshot`.

Desktop-Actions (siehe `uiflow/backends/desktop.py`): `launch`, `connect`, `wait_for_element`, `click`,
`type`, `get_text`, `send_hotkey`, `wait`, `screenshot`.
Desktop-Selektoren sind beliebige `pywinauto` `child_window(**kwargs)`-Argumente, z.B. `control_type`, `title`, `auto_id`, `class_name`.

Jeder Step kann zusätzlich `save_as: <name>` tragen — der Rückgabewert der Aktion (z.B. der von
`get_text` gelesene Text) landet dann in einer Variable, die spätere Steps als `{var.name}` verwenden können.

## Engine-Level-Aktionen (Variablen, Kontrollfluss, Excel, HTTP, E-Mail, PDF/OCR)

Diese Aktionen sind **backend-unabhängig** (funktionieren in `web`- und `desktop`-Workflows gleich),
weil sie nicht an eine UI-Interaktion gebunden sind, sondern die `variables` der laufenden
Workflow-Instanz lesen/verändern oder einen externen Dienst ansprechen (siehe `uiflow/engine.py`).
Im Studio-Builder werden `then`/`else`/`cases`/`default`/Schleifenkörper/`try`/`catch` als **echte,
verschachtelte Schritt-Listen** bearbeitet (eigene Aktions-Auswahl, Felder, Hinzufügen/Verschieben/
Löschen je Ebene) — kein rohes JSON mehr.

### Variablen & Kontrollfluss

```yaml
steps:
  - action: assign
    variable: zaehler
    value: "0"                    # Text (erlaubt {var.x}/{item.x}) ...
  - action: assign
    variable: summe
    expression: "a + b"           # ... oder ein Python-Ausdruck über vorhandene Variablen

  - action: increment
    variable: zaehler
    by: 1                         # Default: 1

  - action: if
    condition: "status == 'ok'"   # Python-Ausdruck; wahr/falsch entscheidet den Zweig
    then:
      - action: click
        selector: "#weiter"
    else:
      - action: screenshot
        path: "fehler.png"

  - action: switch
    expression: "land"
    cases:
      DE: [{action: navigate, url: "https://x.de"}]
      US: [{action: navigate, url: "https://x.com"}]
    default: [{action: navigate, url: "https://x.com/intl"}]

  - action: for_each
    items: "kunden"                # Python-Ausdruck, meist eine zuvor gespeicherte Liste/Datentabelle
    item_var: kunde                 # Standard: item
    index_var: i                    # optional
    steps:
      - action: type
        selector: "#name"
        text: "{var.kunde}"

  - action: try
    steps:
      - action: click
        selector: "#riskant"
    catch:
      - action: screenshot
        path: "fehler.png"
    error_var: fehlermeldung        # optional - Exception-Text als Variable
```

`condition`/`expression`-Auswertung läuft über ein eingeschränktes `eval()` (kein `__import__`, `open`,
`exec`; eine kuratierte Liste harmloser Funktionen wie `len`, `str`, `int`, `sum`, `sorted` ist erlaubt).
Das ist keine vollständige Sandbox gegen einen böswilligen Autor — Workflows werden von derselben Person
geschrieben und ausgeführt, wie ein lokales Skript, nicht von nicht vertrauenswürdiger Fremdeingabe.
`try`/`catch` fängt Step-Fehler innerhalb seines eigenen `steps`-Blocks ab (nicht nur einzelne Schritte,
sondern beliebig verschachtelte Sub-Blöcke); ein per Stop-Button angefordertes Abbrechen wird davon
absichtlich **nicht** abgefangen.

### Excel, HTTP, PDF/OCR

```yaml
steps:
  - action: read_excel           # erste Zeile = Spaltenüberschriften
    path: "kunden.xlsx"
    save_as: kunden                # -> Liste von Dicts, z.B. [{"name": "Anna", "betrag": 10}, ...]

  - action: write_excel
    path: "ergebnis.xlsx"
    data: "kunden"                  # Python-Ausdruck -> Liste von Dicts oder Listen; Datei wird neu angelegt

  - action: http_request
    method: POST
    url: "https://api.example.com/kunden/{var.kundennummer}"
    headers: {"Authorization": "Bearer {var.token}"}
    json: {"status": "erledigt"}
    save_as: antwort                # -> {status_code, headers, text, json}

  - action: read_pdf
    path: "rechnung.pdf"
    pages: "1,3-5"                  # optional, Standard: alle Seiten
    save_as: rechnungstext

  - action: ocr_image               # braucht die Tesseract-OCR-Engine als installiertes Binary,
    path: "scan.png"                 # NICHT nur das Python-Paket - siehe Hinweis unten
    lang: deu
    save_as: erkannter_text
```

**Excel-Zeilen als Queue verarbeiten** (statt `read_excel`/`for_each` + eigene Schleife) ist oft der
einfachere Weg für "pro Zeile einmal ausführen": im Studio-Queues-Panel eine `.xlsx`-Datei hochladen —
jede Zeile wird automatisch ein Queue-Item, nutzbar über `{item.<spalte>}` in einem Queue-gesteuerten
Job (siehe Abschnitt "Orchestrator" oben). `for_each` eignet sich dagegen für "alles in einem Lauf
verarbeiten" (z.B. eine Datentabelle innerhalb eines einzelnen Workflow-Durchlaufs iterieren).

`ocr_image` ruft `pytesseract` auf, das selbst nur ein dünner Wrapper um die **Tesseract-OCR-Engine**
ist — ein separates Binary, das auf dem Rechner installiert und im PATH sein muss (nicht Teil von
`pip install`). Ohne installiertes Tesseract schlägt der Schritt mit einer klaren Fehlermeldung fehl.

### E-Mail

```yaml
steps:
  - action: get_credential
    name: smtp_password
    save_as: smtp_pw

  - action: send_email
    smtp_host: smtp.example.com
    smtp_port: 587
    username: bot@example.com
    password: "{var.smtp_pw}"
    to: kunde@example.com
    subject: "Bestätigung"
    body: "Ihr Auftrag wurde bearbeitet."

  - action: read_emails
    imap_host: imap.example.com
    username: bot@example.com
    password: "{var.smtp_pw}"
    folder: INBOX
    limit: 10
    unseen_only: true
    save_as: eingang                # -> Liste von {subject, from, date, body}
```

## Anmeldedaten (Credentials)

`get_credential` liest ein Geheimnis (Passwort, API-Key, ...) zur Laufzeit in eine Variable ein, ohne
dass es im Klartext in der Workflow-YAML steht. Verwaltet werden Werte im Studio über den Button
**🔑 Anmeldedaten** (Name + Wert eintragen, Liste vorhandener Namen, löschen). Der Wert selbst landet
**nicht** in `orchestrator.db`, sondern im Anmeldeinformationsspeicher des Betriebssystems (Windows
Credential Manager, über das `keyring`-Paket) — die Datenbank merkt sich nur, welche *Namen* vergeben
wurden, damit die Liste im UI angezeigt werden kann. Innerhalb eines laufenden Workflows wird ein per
`get_credential` geladener Wert außerdem aus dem Job-Log maskiert (`***`), falls er später in einem
anderen Step-Parameter auftaucht (z.B. in einer `type`- oder `send_email`-Aktion).

## Zeitpläne (Scheduling)

Der Button **⏱ Zeitplan** plant den aktuell im Builder geöffneten Workflow (inkl. Queue-Name, falls
gesetzt) über einen Cron-Ausdruck (`Minute Stunde Tag Monat Wochentag`, z.B. `0 2 * * *` für "täglich um
2 Uhr"). Ein Scheduler-Thread prüft periodisch fällige Zeitpläne und reiht dann ganz normal einen neuen
**Job** ein (derselbe Mechanismus wie ein manuelles "Ausführen") — die eigentliche Ausführung übernimmt
weiterhin ein Worker. `uiflow studio` startet den Scheduler standardmäßig eingebettet mit (zusammen mit
dem Worker, siehe `--no-worker`); eigenständig läuft er über:

```powershell
python -m uiflow.cli scheduler
```

## Login (optional)

`uiflow studio` läuft standardmäßig ohne Anmeldung — als lokales Single-User-Tool auf `127.0.0.1`.
Setzt man vor dem Start die Umgebungsvariable `UIFLOW_STUDIO_PASSWORD`, verlangt die Studio-Oberfläche
ein gemeinsames Passwort, bevor UI und API erreichbar sind (z.B. wenn `--host` auf mehr als Loopback
gebunden wird). Das ist bewusst ein einfaches Shared-Password-Gate, **kein** vollständiges
Multi-User-/Rechte-System (siehe Roadmap) — für mehrere Personen mit getrennten Konten/Berechtigungen
wäre mehr nötig.

## Tests

```powershell
pip install -e ".[dev]"
pytest
```

Die Tests decken Modell-Parsing, Engine-Dispatch-Logik und den Orchestrator-DB-Layer ab (mit einem
gemockten Backend bzw. temporärer SQLite-Datei, ohne echten Browser/Windows-App).

## Was fehlt bis zu einem "echten" Tool (Roadmap)

- **Orchestrator über eine Maschine hinaus**: `uiflow worker`/`uiflow scheduler` können heute schon
  mehrfach gegen dieselbe `orchestrator.db` laufen, aber nur, wenn alle Prozesse Zugriff auf dieselbe
  Datei haben (z.B. Netzlaufwerk). Für echte Remote-Worker fehlt noch ein Netzwerk-Layer (Worker
  registrieren sich per HTTP statt direktem DB-Zugriff).
- **Echtes Multi-User-/Rechte-System**: das optionale Login (`UIFLOW_STUDIO_PASSWORD`) ist ein
  einzelnes geteiltes Passwort ohne einzelne Konten, Rollen oder Berechtigungen — bewusst minimal
  gehalten, kein Ersatz für echtes RBAC.
- **Recorder-Feinschliff**: der Aufnahme-Modus (`uiflow/studio/recorder.py`) deckt Klick + Texteingabe
  ab; Drag, Rechtsklick, Scroll, Tastenkombinationen und Mehrfach-Fenster-Scopes werden noch nicht
  erfasst.
- **Fehlerbehandlung pro einzelnem Step** (Retry/Continue direkt an einem Step, unabhängig von einem
  umschließenden `try`): aktuell fängt nur ein expliziter `try`/`catch`-Block Fehler ab; ein Step ohne
  `try` drumherum bricht den Workflow beim ersten Fehler weiterhin ab (Queue-Items haben ihr eigenes,
  davon unabhängiges Retry via `max_retries`).
- **Visueller Flow-Canvas** statt Formular-Liste (Drag&Drop wie UiPath Studio/n8n) — aktuell ist
  der Builder bewusst eine (jetzt auch verschachtelbare) Schritt-Liste mit Formularen, kein
  Node-Canvas, siehe `uiflow studio`.
- **Selectors robuster machen** (Fallback-Strategien, Bild-basierte Erkennung wie UiPath es
  für Legacy-Apps anbietet).
