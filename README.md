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
    db.py                Persistenter Job-/Log-/Queue-/Credential-Namen-/Zeitplan-Store und
                         globale Variablen (SQLite, WAL-Modus)
    worker.py             Job-Ausführung (run_worker_loop) + Cron-Zeitpläne (run_scheduler_loop)
  studio/
    schema.py           Beschreibt pro Backend, welche Actions es gibt und welche Formularfelder sie
                        brauchen, plus die Katalog-Metadaten (Label/Kategorie/Beschreibung/Synonyme)
    app.py              Flask-App: REST-API (Schema/Aktivitäten/Workflows/Jobs/Queues/Credentials/
                        Globals/Schedules/Pick) + SSE-Log-Streaming + optionales Login
    picker.py            "Element wählen": Klick-zu-Selektor-Erkennung für Web (Playwright) und Desktop (pynput + UI Automation)
    static/              Builder-Frontend (HTML/CSS/Vanilla JS, kein Build-Step); static/vendor/
                         enthält SortableJS (MIT) für das Drag & Drop des Sequenz-Designers
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

Lokale Web-App zum Bauen von Workflows statt YAML von Hand. Der Builder ist ein
**Sequenz-Designer** im Stil von UiPath Studio: links ein durchsuchbarer Aktivitäten-Katalog,
in der Mitte der Ablauf als Karten, rechts die Eigenschaften der ausgewählten Aktivität.

```powershell
python -m uiflow.cli studio
```

Öffnet `http://127.0.0.1:8787` im Browser. Dort:
- Workflow-Name + Backend (Web/Desktop) wählen
- **Aktivitäten-Katalog** (links): alle Aktivitäten des gewählten Backends, nach Kategorie gruppiert
  (Anwendung, UI-Interaktion, Warten, Ablaufsteuerung, Variablen, Dateien & Dokumente, Integration).
  Das Suchfeld filtert über Name, Beschreibung und Synonyme — "schleife" findet `for_each`, "wenn"
  findet `if`. Eine Aktivität wird per **Drag & Drop** in den Ablauf gezogen; ein Klick hängt sie
  stattdessen ans Ende an (gleiche Wirkung, auch per Tastatur erreichbar).
- **Drag & Drop über Container-Grenzen**: eine Karte lässt sich am Griff "⠿" an jede Position ziehen —
  auch in einen `Dann`-Zweig hinein, aus einem Schleifenkörper heraus oder von einem `try` in ein
  `catch`. Eine Container-Aktivität kann nicht in ihren eigenen Zweig gezogen werden.
- **Eigenschaften-Panel** (rechts): zeigt die Parameter der ausgewählten Aktivität. Die Karten im
  Ablauf bleiben dadurch kompakt (Name + wichtigster Parameter als Zusammenfassung), sodass auch ein
  langer Workflow als Ganzes lesbar bleibt.
- Karten per ✕ löschen
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
- **Anwendungs-Scope**: Ist der erste Schritt eines Desktop-Workflows `launch` oder `connect`, gilt er
  als Scope des Workflows — analog zu UiPaths "Use Application/Browser"-Aktivität. Er ist als erste
  Karte fixiert: nicht verschiebbar, und es lässt sich nichts darüber ablegen, weil alle folgenden
  Schritte sich auf ihn beziehen.
- **🔴 Aufnahme starten** (im Scope-Bereich, sobald ein `launch`/`connect`-Schritt existiert): zeichnet
  echte Interaktionen in der Zielanwendung live als Workflow-Schritte auf:
  - **Klicks** werden als `click` übernommen; ein **Rechtsklick** trägt zusätzlich `button: right`
    (Mittelklick entsprechend `middle`).
  - **Ziehen** (Maustaste gedrückt halten und über eine kurze Distanz bewegen, z.B. eine Zeile per
    Drag & Drop verschieben) wird als `drag` mit der Zielposition (`to_x`/`to_y`) aufgezeichnet, nicht
    als Klick — kleine Zitterbewegungen unterhalb weniger Pixel zählen weiterhin als Klick.
  - **Scrollen** wird als `scroll` aufgezeichnet; mehrere Mausrad-Bewegungen über demselben Element
    hintereinander werden zu einem einzigen `scroll`-Schritt zusammengefasst (wie Text: erst ein
    Elementwechsel oder eine andere Aktion committet ihn), statt einen Schritt pro Rad-Kerbe zu erzeugen.
  - **Tastenkombinationen** mit gehaltenem Strg oder Alt (z.B. Strg+S) werden als `send_hotkey` erfasst,
    nicht als Text. Reines Umschalttaste-Tippen (Großbuchstaben, Sonderzeichen) bleibt normaler Text —
    Umschalttaste allein löst keine Tastenkombination aus.
  - Text wird weiterhin gepuffert und beim nächsten Klick, einem `send_hotkey` oder mit Tab/Enter als
    `type`-Schritt übernommen.
  - **Scope über mehrere Fenster**: Interaktionen zählen nicht nur im ursprünglich verbundenen Fenster,
    sondern auch in Fenstern, die von diesem *besessen* werden (Windows' eigene Owner-Beziehung, z.B.
    ein modaler "Speichern unter"-Dialog) — selbst wenn ein solches Fenster technisch unter einer
    anderen Prozess-ID läuft. Interaktionen außerhalb dieses Scopes werden ignoriert.
  - **⏹ Aufnahme stoppen** beendet die Sitzung (verbleibender Text bzw. eine noch offene Scroll-Serie
    wird noch als letzter Schritt übernommen).

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
markiert werden — jeweils erst nach einer exponentiell wachsenden Wartezeit (5s, 10s, 20s, ...,
gedeckelt auf 60s), damit ein Retry auch tatsächlich eine Chance hat, dass sich eine vorübergehende
Störung legt. Ein einzelnes fehlgeschlagenes Item bricht den Job nicht ab, der Job endet danach aber
im Status `error` statt `success`. Innerhalb der Step-Parameter stehen Platzhalter `{item.<feld>}`
zur Verfügung, die pro Item aus dessen `payload` ersetzt werden:

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
`drag`, `scroll`, `type`, `get_text`, `send_hotkey`, `wait`, `screenshot`.
Desktop-Selektoren sind beliebige `pywinauto` `child_window(**kwargs)`-Argumente, z.B. `control_type`, `title`, `auto_id`, `class_name`.
`click` nimmt optional `button` (`left`/`right`/`middle`, Standard `left`) für Rechts-/Mittelklick.
`drag` zieht das aufgelöste Element per gedrückter Maustaste zu einer absoluten Bildschirmposition
(`to_x`/`to_y`) — das Ziel ist bewusst ein Punkt statt eines zweiten Selektors, weil eine Ablagestelle
oft kein eigenes Element ist (leere Listenfläche, Lücke zwischen Zeilen). `scroll` bewegt das Mausrad
über der Mitte des Elements; `amount` folgt der Windows-Konvention (positiv = hoch, negativ = runter).

Jeder Step kann zusätzlich `save_as: <name>` tragen — der Rückgabewert der Aktion (z.B. der von
`get_text` gelesene Text) landet dann in einer Variable, die spätere Steps als `{var.name}` verwenden können.

## Engine-Level-Aktionen (Variablen, Kontrollfluss, Excel, HTTP, E-Mail, PDF/OCR)

Diese Aktionen sind **backend-unabhängig** (funktionieren in `web`- und `desktop`-Workflows gleich),
weil sie nicht an eine UI-Interaktion gebunden sind, sondern die `variables` der laufenden
Workflow-Instanz lesen/verändern oder einen externen Dienst ansprechen (siehe `uiflow/engine.py`).
Im Studio-Builder sind `then`/`else`/`cases`/`default`/Schleifenkörper/`try`/`catch` **echte
Ablagezonen**: Aktivitäten werden direkt hineingezogen, und Karten lassen sich zwischen Zweigen
verschieben.

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

  - action: run_workflow
    workflow: "Rechnung buchen"     # Name einer YAML-Datei in workflows/
    arguments:                       # was der Unterprozess sieht (Werte im Kontext des Aufrufers)
      rechnungsnummer: "{item.nr}"
      betrag: "{var.summe}"
    outputs:                         # Variable im Unterprozess -> Variable hier
      belegnummer: letzter_beleg
```

`condition`/`expression`-Auswertung läuft über ein eingeschränktes `eval()` (kein `__import__`, `open`,
`exec`; eine kuratierte Liste harmloser Funktionen wie `len`, `str`, `int`, `sum`, `sorted` ist erlaubt).
Das ist keine vollständige Sandbox gegen einen böswilligen Autor — Workflows werden von derselben Person
geschrieben und ausgeführt, wie ein lokales Skript, nicht von nicht vertrauenswürdiger Fremdeingabe.
`try`/`catch` fängt Step-Fehler innerhalb seines eigenen `steps`-Blocks ab (nicht nur einzelne Schritte,
sondern beliebig verschachtelte Sub-Blöcke); ein per Stop-Button angefordertes Abbrechen wird davon
absichtlich **nicht** abgefangen.

#### Fehlerbehandlung pro Step (`on_error`)

Jeder einzelne Step kann zusätzlich `on_error` tragen — unabhängig davon, ob er in einem `try`/`catch`
steckt:

```yaml
steps:
  - action: click
    selector: "#lade-daten"
    on_error: retry            # oder: continue
    retry_count: 5              # Standard: 3 - nur bei on_error: retry relevant
    retry_delay: 3               # Sekunden zwischen Versuchen, Standard: 2 - nur bei on_error: retry relevant

  - action: click
    selector: "#optionaler-hinweis-schließen"
    on_error: continue           # Fehler wird geloggt, der Workflow läuft trotzdem weiter
```

Ohne `on_error` bricht ein Step den Workflow beim ersten Fehler weiterhin ab (wie bisher), sofern kein
umschließendes `try` ihn abfängt. `retry` wiederholt genau diesen Step an Ort und Stelle bis zu
`retry_count`-mal, mit `retry_delay` Sekunden Wartezeit dazwischen — schlägt auch der letzte Versuch fehl,
bricht der Step wie ohne `on_error` ab. `continue` protokolliert den Fehler und macht beim nächsten Step
weiter, ohne den Rest des Workflows abzubrechen. Ein per Stop-Button angefordertes Abbrechen wird von
keiner der beiden Optionen abgefangen — auch nicht während der Wartezeit zwischen zwei Retry-Versuchen,
die dafür in kurzen Schritten geprüft wird, statt sie ungeprüft durchzuschlafen. Ist der Step selbst ein
Container (`if`, `for_each`, `try`, ...), wiederholt bzw. überspringt `on_error` den gesamten Container —
bei `for_each` also den kompletten Schleifendurchlauf, nicht nur das zuletzt fehlgeschlagene Element
(dafür bleibt weiterhin das eigene, unabhängige `max_retries` der Queue-Items zuständig, siehe
"Orchestrator" unten).

#### Unterprozesse (`run_workflow`)

`run_workflow` führt eine andere Workflow-Datei als Baustein aus — für Schrittfolgen, die in mehreren
Automatisierungen vorkommen, und um einen langen Ablauf in benannte Teile zu zerlegen. Vier Eigenschaften
sind bewusst so gewählt:

- **Variablen fließen nicht automatisch.** Der Unterprozess startet mit genau dem, was `arguments`
  übergibt, und zurück kommt nur, was `outputs` benennt. Besteht ein Argumentwert ausschließlich aus
  einem Platzhalter (`"{var.kunden}"`), wird der Wert **mit seinem Typ** übergeben — eine Liste bleibt
  eine Liste. Steht der Platzhalter dagegen in einem längeren Text (`"https://x/{var.pfad}"`), wird
  wie überall sonst textuell ersetzt. Würde er die Variablen des Aufrufers teilen,
  hinge er still an Namen, die er nie deklariert hat — genau die Kopplung, die Wiederverwendung
  vermeiden soll. Schlägt der Unterprozess fehl, werden **keine** `outputs` übernommen.
- **Dasselbe Backend, dieselbe Anwendung.** Der Unterprozess läuft auf der bereits geöffneten
  Browser-/Anwendungssitzung des Aufrufers, nicht auf einer zweiten. Deklariert er ein anderes `backend`,
  bricht der Schritt mit einer klaren Meldung ab, statt Desktop-Aufrufe gegen einen Browser zu schicken.
- **Zyklen werden abgelehnt.** Ruft A den Prozess B auf, der wieder A aufruft, endet der Schritt mit
  `Sub-workflow cycle: A -> B -> A`.
- **Beim Einreihen aufgelöst und mit eingereiht.** `uiflow run` von der Kommandozeile löst den Namen
  direkt in `workflows/` auf — dieselbe Datei, die der Builder speichert und in der Auswahlliste anbietet.
  Ein **Job** dagegen bettet jeden referenzierten Unterprozess (rekursiv, falls dieser selbst weitere
  aufruft) beim Einreihen als Snapshot mit in die Job-Zeile ein (`sub_workflows_json`, siehe
  `models.resolve_sub_workflows`) — genau wie der aufrufende Workflow selbst schon als Snapshot vorliegt.
  Ein Job führt damit exakt die Unterprozess-Fassung aus, die beim Einreihen galt, selbst wenn die Datei
  danach bearbeitet oder gelöscht wird, bevor ein Worker sie abholt. Ein `workflow`-Wert, der kein
  wörtlicher Name ist (z.B. `"{var.ziel}"`, erst zur Laufzeit aus Job-/Item-Daten bekannt), lässt sich so
  nicht vorab festlegen und bleibt wie zuvor eine Live-Auflösung zum Ausführungszeitpunkt — ebenso ein
  zyklischer oder fehlender Verweis, der ohnehin erst beim tatsächlichen Lauf mit einer klaren
  Fehlermeldung abbricht.

Ein Haltepunkt *innerhalb* eines Unterprozesses hält den Lauf korrekt an und zeigt dessen Variablen,
markiert aber keine Karte auf der Zeichenfläche — die zeigt den aufrufenden Workflow, in dem dieser
Schritt gar nicht vorkommt.

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
    mark_as_read: false             # Standard: Lesen lässt die Mails ungelesen (IMAP BODY.PEEK)
    save_as: eingang                # -> Liste von {subject, from, date, body}
```

## Globale Variablen

Werte, die für *alle* Workflows gelten — Basis-URLs, Postfächer, Grenzwerte. Verwaltet im Studio unter
**Orchestrator → Globale Variablen**; gespeichert in `orchestrator.db` (Tabelle `global_variables`) als
JSON, damit eine Zahl eine Zahl und eine Liste eine Liste bleibt.

Genutzt werden sie auf zwei Wegen — genau wie Workflow-Variablen:

```yaml
steps:
  - action: navigate
    url: "{global.basis_url}/anmelden"     # in Parametern: eigener Namensraum

  - action: if
    condition: "betrag > max_betrag"        # in Ausdrücken: direkt unter ihrem Namen
    then:
      - action: click
        selector: "#freigabe-anfordern"
```

Vier Punkte dazu:

- **Sie gelten auch in Unterprozessen**, ohne als Argument übergeben zu werden. Ein Unterprozess ist
  gegen die Variablen seines Aufrufers abgeschottet (siehe `run_workflow`), aber nicht gegen die
  globalen — sonst müsste man Konfiguration durch jede Aufrufkette schleifen.
- **Eine gleichnamige Workflow-Variable hat in Ausdrücken Vorrang.** Ein Lauf kann einen globalen Wert
  damit lokal überschreiben, ohne ihn für andere zu ändern; `{global.name}` liest weiterhin den globalen.
- **Ein Unterprozess kann einen globalen Wert nicht für seinen Aufrufer verändern** — ein `assign` legt
  eine gewöhnliche Workflow-Variable an, nicht den globalen Eintrag.
- **Sie werden pro Lauf gelesen**, nicht beim Einreihen. Eine Änderung wirkt also auf den nächsten Lauf,
  ohne dass etwas neu eingereiht werden muss — und gilt gleichermaßen für `uiflow run` von der
  Kommandozeile wie für einen Job über den Worker.

`global`, `item` und `var` sind als Namen reserviert, weil sie die Platzhalter-Namensräume benennen.
**Keine Geheimnisse hier ablegen** — dafür gibt es Anmeldedaten, deren Werte gar nicht erst in dieser
Datenbank landen:

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
- **Deklarierte Workflow-Variablen** (UiPaths Variablen-Panel): Installationsweite globale Variablen
  gibt es inzwischen (siehe oben), die *workflow-eigenen* entstehen aber weiterhin stillschweigend beim
  ersten `assign` — nirgends steht, welche ein Workflow verwendet, und es gibt keine Startwerte oder
  Typen. Nebenwirkung: ein Tippfehler verhält sich je nach Weg unterschiedlich — `{var.tippfehler}` wird
  stumm zu einem leeren String, derselbe Name in einem Ausdruck bricht mit einem Fehler ab. Eine
  Deklarationsliste würde beides prüfbar machen und dem Eigenschaften-Panel erlauben, Variablennamen zur
  Auswahl anzubieten statt sie tippen zu lassen.
- **Framework-Vorlage nach Vorbild des UiPath REFramework**: Die Kernschleife gibt es bereits —
  `_run_queue_driven` ist das "Process Transaction"-Muster, mit Retry samt Backoff, Status je Item und
  Job-Logs. Was fehlt, ist der Rahmen darum herum, den man heute in jedem Projekt neu baut:
  - **Zustände**: Initialisierung (Konfiguration lesen, Anwendungen öffnen), Transaktion holen,
    Transaktion verarbeiten, Abschluss (Anwendungen schließen, Abschlussbericht) als vorgegebenes
    Gerüst statt als handgebauter Ablauf.
  - **Konfiguration**: eine Einstellungsdatei bzw. ein Konfigurationsblatt, aus dem der Prozess seine
    Parameter zieht, statt sie in die Schritte zu schreiben.
  - **Fachlicher vs. technischer Fehler**: der wichtigste Punkt, weil er die bestehende Retry-Logik
    betrifft. `complete_queue_item` behandelt heute *jeden* Fehler gleich und versucht es bis
    `max_retries` erneut. Eine ungültige Rechnung wird dadurch dreimal ungültig verarbeitet, obwohl
    schon der erste Versuch abschließend war — ein fachlicher Fehler darf nie wiederholt werden, nur
    ein technischer. Dafür müsste die Engine beide Arten unterscheiden können und die Queue die
    Unterscheidung mitführen.
  - **Aufräumen zwischen Fehlversuchen**: Zielanwendung nach einem Fehler schließen und neu öffnen,
    statt den nächsten Versuch auf einem kaputten Bildschirmzustand starten zu lassen.

  Sinnvollerweise als Vorlage, die aus den vorhandenen Bausteinen erzeugt wird — keine zweite Engine
  neben der bestehenden.
- **Flowchart-Ansicht** (frei platzierte Knoten mit Verbindungspfeilen, wie UiPaths Flowchart oder
  n8n). Der Builder ist ein Sequenz-Designer — er bildet den Ablauf als verschachtelte Kartenliste ab,
  passend zum sequenziellen YAML-Format. Eine Flowchart-Ansicht bräuchte ein eigenes Format mit Knoten,
  Kanten und Koordinaten und ist deshalb bewusst noch nicht gebaut.
- **UI Explorer (Oberflächen analysieren)**: Zum Erkunden einer Zielanwendung gibt es heute zwei
  Teilstücke — `uiflow inspect-desktop "Fenstertitel" --depth 4` druckt den UI-Automation-Baum als Text
  in die Konsole, und der Picker markiert während der Aufnahme das Element unter dem Mauszeiger. Was
  fehlt, ist das Werkzeug dazwischen: den Baum interaktiv durchklicken, alle Eigenschaften eines Elements
  sehen und vor allem **einen Selektor ausprobieren, bevor er in einem Workflow landet**. Drei Lücken:
  - **Wie viele Treffer?** Ein mehrdeutiger Selektor ist die klassische Ursache dafür, dass ein Bot das
    falsche Element anklickt — und genau das zeigt heute nichts an. Ein Explorer sollte einen Selektor
    gegen die laufende Anwendung prüfen und melden, wie viele Elemente passen und welche.
  - **Web fehlt komplett**: `inspect-desktop` hat kein Gegenstück für den Browser. Playwright kann einen
    Selektor auswerten und die Treffer hervorheben — die Grundlage dafür ist also schon im Projekt.
  - **Zwei Element-Modelle**: pywinauto-Eigenschaften auf der einen, DOM plus Playwright-Selektor auf der
    anderen Seite. Der Explorer bräuchte eine gemeinsame Darstellung (Name, Typ, Eigenschaften, Kinder) —
    die erzeugt `picker.py` im Kern bereits, bisher nur nicht als durchsuchbaren Baum.

  Zusammen mit dem nächsten Punkt ergibt das den natürlichen Arbeitsablauf: im Explorer suchen und prüfen,
  von dort als benanntes Element ins Repository speichern, in Aktivitäten wiederverwenden.
- **Object Repository (wiederverwendbare Selektoren)**: Heute trägt jede Aktivität ihren Selektor
  inline — bei Web das Feld `selector`, bei Desktop das Tripel `control_type`/`title`/`auto_id`.
  "Element auf dem Bildschirm wählen" schreibt genau dorthin. Ein Element, das an zehn Stellen
  angesprochen wird, steht damit zehnmal im Workflow: ändert die Zielanwendung ihre Oberfläche, müssen
  alle zehn Stellen gefunden und einzeln nachgezogen werden — der Hauptgrund, warum RPA-Automatisierungen
  im Betrieb brechen.
  Gewünscht ist stattdessen ein zentraler Speicher benannter UI-Elemente ("Anmelden-Knopf",
  "Rechnungsnummer-Feld"): einmal während der Entwicklung aufnehmen, danach in beliebigen Aktivitäten
  per Name referenzieren, und bei einer UI-Änderung an genau einer Stelle korrigieren.
  Drei Punkte, die den Zuschnitt bestimmen und deshalb vor der Umsetzung geklärt sein wollen:
  - **Auflösung in der Engine**: `substitute_variables` ersetzt heute `{var.x}`/`{item.x}` rein
    textuell in den Step-Parametern. Für ein Repository reicht das nicht, weil ein Desktop-Element auf
    *drei* Felder abbildet, nicht auf einen String. Die Engine müsste eine Element-Referenz vor dem
    Dispatch ans Backend zu den passenden Parametern auflösen — ein eigener Schritt in `_run_backend_step`,
    kein zusätzliches Platzhalter-Muster.
  - **Zuordnung zur Anwendung**: ein Selektor gilt nur innerhalb seiner Anwendung. Der bestehende
    Scope-Begriff (`launch`/`connect` als erster Schritt) benennt diese Anwendung bereits — er ist der
    naheliegende Schlüssel, unter dem Elemente gruppiert werden, analog zu UiPaths Aufteilung in
    Anwendung → Screen → Element.
  - **Ablage**: anders als Anmeldedaten sind Selektoren kein Geheimnis und gehören versioniert neben die
    Workflows (eine Datei in `workflows/`), nicht in `orchestrator.db` — sonst lässt sich eine Änderung
    an der Oberfläche nicht mit dem Workflow zusammen reviewen und zurückrollen.

  Im Studio wären das zwei Ergänzungen: `picker.py` bekommt neben "Felder füllen" ein "als Element
  speichern", und im Eigenschaften-Panel wird aus dem freien Selektor-Feld eine Auswahl über das
  Repository plus "neu aufnehmen".
- **Selectors robuster machen** (Fallback-Strategien, Bild-basierte Erkennung wie UiPath es
  für Legacy-Apps anbietet). Greift ineinander mit dem Object Repository: sind die Selektoren erst
  zentral, ist eine Fallback-Strategie eine Eigenschaft des Elements statt jeder einzelnen Aktivität.
