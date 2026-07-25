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
- **Selector prüfen** (neben jedem Selector-/Control-Feld): prüft die bereits eingetragenen Felder gegen
  die laufende Anwendung und meldet, wie viele Elemente sie treffen und als was. Bei Web geschieht das
  gegen die (per vorherigem `navigate`-Schritt bekannte) Seite in einem unsichtbaren Browser (Tag,
  sichtbarer Text, sichtbar/unsichtbar); bei Desktop gegen den Element-Baum der per Scope
  (`launch`/`connect`) bekannten Anwendung (Control-Type, Title, Auto-ID) — in beiden Fällen, ohne dass
  der Workflow dafür erst laufen muss. Ein einzelner Treffer wird bestätigt, kein Treffer und mehr als
  ein Treffer werden ausdrücklich als Problem markiert: ein mehrdeutiger Selector ist die klassische
  Ursache dafür, dass ein Bot später das falsche Element anklickt.
- **↶ Rückgängig** (oder Strg+Z) macht die letzte Änderung rückgängig — Schritt hinzugefügt/gelöscht/
  verschoben, Haltepunkt umgeschaltet, Feld bearbeitet, Backend gewechselt, Selektor übernommen.
- **Flowchart** (Button neben "Variablen"): zeigt den aktuell geöffneten Workflow als Kästchen-und-
  Pfeile-Diagramm statt als verschachtelte Kartenliste — reine Nur-Ansicht desselben Ablaufs, kein
  eigenes Format. Verzweigende Aktivitäten (`if`s Dann/Sonst, `switch`s Fälle/Standard-Fall,
  `for_each`s Schleifenkörper, `try`s Versuchen/Bei Fehler) laufen dabei als eigene Spalten nach unten
  auseinander und danach wieder in der Hauptspalte zusammen. Ein Klick auf ein Kästchen schließt das
  Flowchart und wählt den Schritt im Builder aus, um ihn dort zu bearbeiten.
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

Mehrere Worker (auf derselben oder verteilt auf anderen Maschinen) können parallel gegen dieselbe
Queue arbeiten — `claim_next_job`/`claim_next_queue_item` beanspruchen Zeilen atomar
(`UPDATE ... WHERE status='queued'`), sodass zwei Worker nie denselben Job doppelt ausführen.

**Remote-Worker (andere Maschine, kein Datei-Zugriff auf `orchestrator.db`)**: mit `--remote-url`
spricht `uiflow worker` die Job-Queue über HTTP an (`/api/worker/*` auf dem Studio-Server) statt
`orchestrator.db` direkt zu öffnen — für einen Worker, der auf einer anderen Maschine läuft und die
Datei gar nicht sehen kann (z.B. kein gemeinsames Netzlaufwerk):

```powershell
python -m uiflow.cli worker --remote-url http://studio-host:8787 --worker-id robot-1
```

Meldet sich genauso an wie jeder andere Client (siehe Login oben): ohne `--remote-username` mit dem
gemeinsamen `UIFLOW_STUDIO_PASSWORD`, mit `--remote-username` über ein per `uiflow create-user
robot1 --role operator` angelegtes Konto — kein separater API-Key-Mechanismus, sondern dieselbe
Session/Rollenprüfung wie im Browser. Ohne `--remote-password` wird das Passwort interaktiv abgefragt.
Claiming, Logging, Haltepunkte, Queue-Items und Job-Abschluss laufen dabei über dieselben Endpunkte,
die auch ein lokaler Worker letztlich anspricht (`orchestrator/worker.py` kennt intern nur noch einen
austauschbaren `store` — lokal `orchestrator/db.py` direkt, remote `orchestrator/remote_store.py` über
HTTP; die Ausführungslogik selbst ist in beiden Fällen identisch).

**Heartbeat & automatisches Requeuing bei einem abgestürzten Worker**: jeder laufende Job schreibt
alle 15s einen Heartbeat (`--heartbeat-interval` bei `uiflow worker`), unabhängig davon, was der
Workflow gerade tut — auch während er an einem Haltepunkt auf eine Person wartet, damit ein
wartender Job nicht wie ein abgestürzter aussieht. `uiflow scheduler` prüft bei jedem Durchlauf, ob
ein `running`-Job seit `--stale-job-timeout` (Standard 90s) keinen Heartbeat mehr geschrieben hat —
sein Worker ist dann vermutlich abgestürzt (oder seine Maschine ausgefallen). So ein Job wird als
`error` markiert; ein Queue-Item, das er noch `in_progress` hielt, geht dabei automatisch zurück in
die Queue (`new`, ohne einen Retry zu verbrauchen — es wurde ja nie tatsächlich versucht-und-
fehlgeschlagen, sein Worker ist nur verschwunden), sodass ein anderer Worker es aufgreifen kann.
Ein einzelner, nicht queue-gesteuerter Job wird dabei bewusst **nicht** automatisch neu gestartet —
seine Seiteneffekte bis zum Absturz sind unbekannt, "als Fehler markieren, damit ihn jemand bewusst
erneut anstößt" ist die einzige sichere automatische Reaktion.

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

#### Fachlicher vs. technischer Fehler (`fail`)

`complete_queue_item` (siehe "Orchestrator" unten) behandelte bisher jeden Fehler eines Queue-Items
gleich und versuchte ihn bis `max_retries` erneut — eine ungültige Rechnung wurde dadurch mehrfach
ungültig verarbeitet, obwohl schon der erste Versuch abschließend war. `fail` löst einen Fehler
ausdrücklich als einen von zwei Typen aus:

```yaml
steps:
  - action: fail
    message: "Rechnung {item.rechnungsnummer}: Betrag außerhalb des zulässigen Bereichs"
    type: business       # wird bei einem Queue-Item NIE wiederholt

  - action: fail
    message: "Zielseite nicht erreichbar"
    type: technical       # Standard - verhält sich wie jeder andere Step-Fehler
```

`type: business` markiert das Queue-Item sofort als `failed`, ohne einen Versuch von `max_retries` zu
verbrauchen und ohne Backoff-Wartezeit — genau richtig für einen Fehler, der beim nächsten Versuch
identisch wäre. `type: technical` (auch der Standard, wenn `type` weggelassen wird) verhält sich wie
ein gewöhnlicher Step-Fehler: bei einem Queue-Item zählt er normal gegen `max_retries`. Ein `try` fängt
beide Arten gleichermaßen ab (unverändert gegenüber jedem anderen Fehler); `on_error` an genau diesem
`fail`-Step dagegen nicht — "wiederholen" oder "fortsetzen" an einem bewusst ausgelösten Fehler
anzubringen wäre widersprüchlich.

#### REFramework-Vorlage

Ein Vorbild ist UiPaths REFramework: Initialisierung, Transaktion holen, Transaktion verarbeiten,
Abschluss, mit fachlicher/technischer Fehlerunterscheidung. Drei der vier Bausteine gibt es hier
bereits, aus vorhandenen Teilen zusammengesetzt statt als zweite Engine:

- **"Transaktion holen"** übernimmt der Orchestrator selbst (`claim_next_queue_item`) — kein
  Workflow-Schritt dafür nötig.
- **Aufräumen zwischen Versuchen** passiert automatisch: ein Queue-Item bekommt bei jedem Versuch
  (erster Versuch wie Retry gleichermaßen) eine frische Backend-Instanz — `_run_workflow_once` öffnet
  sie vor dem Lauf und schließt sie danach, ganz gleich ob der Lauf erfolgreich war, fehlgeschlagen ist
  oder gerade dabei fehlgeschlagen ist. Der nächste Versuch startet also nie auf einem vom vorigen
  Versuch kaputt hinterlassenen Bildschirmzustand.
- **Konfiguration statt Werte in den Schritten**: dafür gibt es bereits **Globale Variablen** (Basis-URL,
  Postfach, Schwellwerte — installationsweit) und **Deklarierte Workflow-Variablen** (Startwerte, die zu
  genau diesem Workflow gehören) — siehe beide Abschnitte unten.
- **Fachlich vs. technisch**: siehe `fail` oben.

`workflows/beispiel_reframework.yaml` zeigt das komponiert an einem Rechnungs-Beispiel: Anmeldung,
fachliche Prüfung *vor* jeder Automatisierung (schlägt sie fehl, startet die eigentliche Automatisierung
gar nicht erst), die Automatisierung selbst in `try`/`catch` (nimmt bei einem Fehler einen Screenshot auf
und löst ihn danach ausdrücklich erneut als `type: technical` aus, damit die Queue ihn trotz `try`/`catch`
weiterhin als Fehlversuch sieht und normal wiederholt).

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

## Deklarierte Workflow-Variablen

Bislang entstand jede Workflow-eigene Variable stillschweigend beim ersten `assign` — nirgends stand,
welche ein Workflow überhaupt verwendet, und `{var.tippfehler}` wurde stumm zu einem leeren String,
während derselbe Tippfehler in einer `condition`/`expression` mit einem Fehler abbrach. Der Button
**Variablen** im Builder öffnet ein Panel, in dem sich Variablen des *aktuell geöffneten* Workflows mit
Namen und optionalem Startwert deklarieren lassen — analog zu UiPaths Variablen-Panel:

```yaml
name: Rechnung buchen
backend: web
variables:
  zaehler: 0          # Startwert - eine Zahl bleibt eine Zahl, eine Liste eine Liste (wie bei Anmeldedaten/globalen Variablen: JSON statt Text)
  kunden: []
  status:             # ohne Startwert (Name nur reserviert) - entsteht wie bisher erst beim ersten assign
steps:
  - action: increment
    variable: zaehler
```

Drei Punkte dazu:

- **Der Startwert steht ab dem allerersten Schritt.** Anders als bei einem `assign` mitten im Ablauf ist
  `{var.zaehler}` schon vor dem ersten Schritt `0`, nicht erst danach — nützlich für einen Zähler, der
  vor der ersten `increment` gelesen wird, oder eine Liste, die per `for_each` durchlaufen wird, bevor
  irgendein Schritt sie befüllt.
- **Ein Name ohne Startwert reserviert nur den Namen** (erscheint im Eigenschaften-Panel als Vorschlag),
  ändert aber nichts am bisherigen Verhalten — die Variable entsteht weiterhin erst beim ersten `assign`.
- **Eine gleichnamige Variable in `arguments` beim Aufruf eines Unterprozesses gewinnt** über dessen
  eigenen Startwert, genau wie ein Funktionsargument einen Default überschreibt — der Unterprozess bringt
  seinen Startwert nur mit, wenn der Aufrufer ihn nicht selbst setzt.

Die Felder `Variable` (`assign`, `increment`) und `Fehlermeldung speichern als` (`try`) bieten die
deklarierten Namen im Eigenschaften-Panel als Vorschlag an, ersetzen freies Tippen aber nicht — eine neue
Variable entsteht weiterhin einfach durch Eintippen eines neuen Namens.

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

## Object Repository (wiederverwendbare Selektoren)

Jede Aktivität trägt sonst ihren Selektor inline — bei Web das Feld `selector`, bei Desktop das Tripel
`control_type`/`title`/`auto_id`. Ein Element, das an zehn Stellen angesprochen wird, steht damit
zehnmal im Workflow: ändert die Zielanwendung ihre Oberfläche, müssen alle zehn Stellen gefunden und
einzeln nachgezogen werden. Das **Object Repository** ist ein zentraler Speicher benannter Elemente,
gruppiert nach Anwendung/Scope:

```yaml
# workflows/_object_repository.yaml - kein Workflow, wird aus der Werkzeugliste ausgeschlossen
MeineApp:
  Anmelden-Knopf:
    control_type: Button
    auto_id: btnOK
  Rechnungsnummer-Feld:
    control_type: Edit
    auto_id: txtRechnung
example.com:
  Suchfeld:
    selector: "#search"
```

Ein Step referenziert ein Element über `element: "Scope/Name"` statt eigener Selektor-Felder:

```yaml
steps:
  - action: click
    element: "MeineApp/Anmelden-Knopf"
```

Die Engine löst `element` auf, bevor der Step ans Backend geht (`_resolve_element_reference` in
`engine.py`) — die Felder aus dem Repository ersetzen dabei jeden inline eingetragenen Selektor auf
demselben Step vollständig, ein Step trägt also entweder eine Repository-Referenz oder einen eigenen
Selektor, nie beide gleichzeitig. `element` erlaubt wie jeder andere Parameter auch `{var.x}` — eine
Referenz kann also zur Laufzeit bestimmt werden, nicht nur wörtlich in der YAML stehen.

Im Studio erscheint bei jedem Selector-/Control-Feld ein Abschnitt **Object Repository**: eine Auswahl
vorhandener `Scope/Name`-Einträge (leer = eigener Selektor bleibt aktiv) sowie ein Button, der die
aktuell eingetragenen Felder des Steps unter einem neuen Namen im Repository ablegt. Drei Punkte dazu:

- **Ablage**: anders als Anmeldedaten ist ein Selektor kein Geheimnis und gehört versioniert neben die
  Workflows (`workflows/_object_repository.yaml`), nicht in `orchestrator.db` — so lässt sich eine
  Änderung an der Oberfläche mit den betroffenen Workflows zusammen reviewen und zurückrollen.
- **Scope ist frei wählbar.** Für Desktop bietet sich der `launch`-Pfad oder `connect`-Titel an (der
  Vorschlag im Speichern-Dialog übernimmt ihn automatisch), für Web mangels eines vergleichbaren
  Scope-Begriffs meist die Domain — beides ist aber nur ein Vorschlag, kein erzwungenes Schema.
- **Auflösung, nicht Textersatz.** Weil ein Desktop-Element auf *mehrere* Felder abbildet
  (`control_type`/`title`/`auto_id`), reicht eine reine `{var.x}`-Textersetzung nicht — die Engine löst
  die Referenz deshalb als eigenen Schritt vor dem Backend-Dispatch auf, nicht über das
  Platzhalter-Muster.

### Fallback-Selektoren

Ein Element im Repository kann mehrere alternative Feldsätze tragen, der Reihe nach versucht, bis
einer tatsächlich passt — die Fallback-Strategie, die UiPath für Legacy- oder instabile Oberflächen
anbietet:

```yaml
MeineApp:
  Suchfeld:
    - selector: "#search"          # zuerst versucht
    - selector: "input[name=q]"    # Fallback, falls #search nicht (mehr) existiert
```

Kann das Backend prüfen, ob ein Kandidat gerade existiert (`element_exists` — für Web per Playwright-
Selektor, für Desktop per `pywinauto child_window(...).exists()`), probiert die Engine die Kandidaten
der Reihe nach durch und nimmt den ersten, der zutrifft. Passt keiner (oder kann das Backend das nicht
prüfen), wird trotzdem der erste Kandidat verwendet — der eigentliche Schritt läuft dann normal weiter
und meldet einen gewohnten, klaren Fehler, statt dass der Schritt still übersprungen wird.

Im Studio erscheint der Button **+ Alternative aufnehmen** (dasselbe Auswahl-Symbol wie beim normalen
Picker) anstelle von "Als Element speichern", sobald eine Repository-Referenz aktiv ist — er startet
denselben Klick-zu-Auswählen-Ablauf, hängt das Ergebnis aber als zusätzlichen Kandidaten an, statt es
zu überschreiben.

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
gebunden wird). Das ist ein einfaches Shared-Password-Gate ohne einzelne Konten oder Rollen — für mehrere
Personen mit getrennten Konten und Berechtigungen siehe den nächsten Abschnitt.

## Benutzer & Rollen (RBAC)

Sobald mindestens ein Benutzer angelegt wurde, schaltet die Studio-Oberfläche automatisch von der
einfachen Passwort-Anmeldung auf individuelle Konten um — kein Neustart und keine zusätzliche
Umgebungsvariable nötig, es reicht:

```powershell
python -m uiflow.cli create-user alice --role admin
```

`--role` ist `viewer`, `operator` oder `admin` (Standard `admin` für den allerersten Account). Ohne
`--password` fragt der Befehl das Passwort interaktiv ab (mit Wiederholung). Ein bestehendes Konto lässt
sich mit `--update` neu setzen (Passwort und/oder Rolle):

```powershell
python -m uiflow.cli create-user alice --update --role operator
```

Die drei Rollen sind hierarchisch (`viewer` < `operator` < `admin`):

- **viewer** — nur lesen: Workflows, Runs und Queues ansehen, aber nichts ausführen oder speichern.
- **operator** — zusätzlich Workflows bauen/speichern, Runs starten/stoppen, Queues befüllen, Zeitpläne
  anlegen. Kein Zugriff auf Anmeldedaten, globale Variablen oder die Benutzerverwaltung selbst.
- **admin** — alles, inklusive Anmeldedaten, globale Variablen und Benutzerverwaltung (Tab **Benutzer**,
  nur für Admins sichtbar).

Ein Admin kann sich selbst weder die Admin-Rolle entziehen noch das eigene Konto löschen (schützt davor,
dass sich eine Installation versehentlich aussperrt). Solange kein einziger Benutzer angelegt wurde,
verhält sich `uiflow studio` exakt wie zuvor (kein Login oder das einfache `UIFLOW_STUDIO_PASSWORD`-Gate)
— die Umstellung ist rein additiv und ändert nichts an bestehenden Installationen.

## Tests

```powershell
pip install -e ".[dev]"
pytest
```

Die Tests decken Modell-Parsing, Engine-Dispatch-Logik und den Orchestrator-DB-Layer ab (mit einem
gemockten Backend bzw. temporärer SQLite-Datei, ohne echten Browser/Windows-App).

## Was fehlt bis zu einem "echten" Tool (Roadmap)

- ~~**Orchestrator über eine Maschine hinaus**~~ — erledigt: `uiflow worker --remote-url ...` spricht
  die Job-Queue über HTTP an (`/api/worker/*`, siehe "Remote-Worker" oben), statt `orchestrator.db`
  direkt zu öffnen — ein Worker auf einer anderen Maschine ohne jeden Datei-Zugriff auf diese Datenbank
  funktioniert damit genauso wie einer auf derselben Maschine. `uiflow scheduler` bleibt bewusst
  serverseitig (er *erzeugt* nur Jobs in derselben Queue, die dann ganz normal von jedem Worker —
  lokal oder remote — abgeholt werden), ebenso wie Studios eingebetteter Scheduler-Thread. Ein
  abgestürzter Worker (lokal oder remote) wird über den Heartbeat/Sweep-Mechanismus erkannt und sein
  Job/Queue-Item automatisch abgeräumt bzw. requeued — siehe "Heartbeat & automatisches Requeuing"
  oben.
- ~~**Echtes Multi-User-/Rechte-System**~~ — erledigt: `uiflow create-user` legt einzelne Konten mit
  Rollen (`viewer`/`operator`/`admin`) an, siehe "Benutzer & Rollen (RBAC)" oben. Was bewusst fehlt: keine
  Gruppen/Teams, keine fein granularen Rechte pro Workflow oder Queue (nur die drei globalen Rollen), kein
  SSO/OAuth — für den Einsatz durch mehrere Personen mit unterschiedlichen Berechtigungsstufen reicht das
  aktuell.
- **REFramework-Vorlage im Studio erzeugen**: Die Bausteine (fachlich/technischer Fehler via `fail`,
  Konfiguration via globale/deklarierte Variablen, "Transaktion holen" via Orchestrator, Aufräumen
  zwischen Versuchen via frischer Backend-Instanz pro Item) gibt es inzwischen alle — siehe
  "REFramework-Vorlage" oben und `workflows/beispiel_reframework.yaml`. Was fehlt, ist ein Komfort im
  Studio selbst: ein Button "Neu aus REFramework-Vorlage" (analog zu "+ Neuer Workflow"), der einen neuen
  Workflow direkt mit diesem Gerüst vorausfüllt, statt dass man die Beispieldatei von Hand kopiert.
- ~~**Flowchart-Ansicht**~~ — teilweise erledigt: der Button **Flowchart** (siehe oben) zeigt den
  Ablauf als automatisch angeordnetes Kästchen-und-Pfeile-Diagramm mit Verzweigungsspalten für
  `if`/`switch`/`for_each`/`try`. Das ist bewusst eine reine *Visualisierung* des bestehenden
  sequenziellen YAML-Formats (read-only, automatisches Layout), **nicht** UiPaths/n8ns freies
  Flowchart mit unabhängig platzierbaren Knoten, beliebiger Kantenführung und einem eigenen
  Graph-Format als Speicherformat — dafür bräuchte es eine eigene, editierbare Graph-Repräsentation
  samt eigenem Ausführungsmodell (auch für Zyklen), was ein deutlich größerer, separater Umbau wäre.
- **UI Explorer (Oberflächen analysieren)**: Zum Erkunden einer Zielanwendung gibt es heute drei
  Teilstücke — `uiflow inspect-desktop "Fenstertitel" --depth 4` druckt den UI-Automation-Baum als Text
  in die Konsole, der Picker markiert während der Aufnahme das Element unter dem Mauszeiger, und der
  Button **Selector prüfen** (Web *und* Desktop, siehe oben) beantwortet die wichtigste Einzelfrage
  direkt im Eigenschaften-Panel: wie viele Elemente die bereits eingetragenen Felder auf der Seite bzw.
  in der Zielanwendung treffen, und als was — ein mehrdeutiger Treffer wird dabei ausdrücklich als
  solcher geflaggt, nicht nur gezählt, weil das die klassische Ursache dafür ist, dass ein Bot das
  falsche Element anklickt. Was fehlt, ist das Werkzeug *dazwischen*: den Baum interaktiv durchklicken
  und alle Eigenschaften eines Elements sehen, statt nur bereits eingetragene Felder zu prüfen. Weder
  `inspect-desktop` (nur Text in der Konsole) noch der Prüfen-Button (nur bereits eingetragene Felder)
  lassen sich interaktiv durchklicken. Der Explorer bräuchte dafür eine gemeinsame Darstellung über
  beide Backends hinweg (Name, Typ, Eigenschaften, Kinder) — die erzeugt `picker.py` im Kern bereits,
  bisher nur nicht als Baum.

  Zusammen mit dem Object Repository (siehe oben) ergäbe das den natürlichen Arbeitsablauf: im Explorer
  suchen und prüfen, von dort direkt als benanntes Element speichern, statt den Umweg über "Selector
  prüfen" -> Feld ausfüllen -> "Als Element speichern" zu nehmen.
- **Bild-basierte Elementerkennung** (wie UiPath es für Legacy-Apps ohne brauchbare UI-Automation-Baum
  anbietet). Fallback-Selektoren für das Object Repository gibt es inzwischen (siehe oben, mehrere
  alternative `control_type`/`title`/`auto_id`- bzw. `selector`-Kandidaten, der Reihe nach versucht) —
  eine Erkennung rein über ein Bildmuster auf dem Bildschirm, unabhängig von jedem Automation-Baum, ist
  davon unabhängig und fehlt weiterhin.

### Weitere Ideen (unpriorisiert, unvalidiert)

Unten gesammelt, aber noch nicht bewertet oder gegen den tatsächlichen Bedarf geprüft — einfach
Ideen, was als Nächstes sinnvoll sein könnte. Reihenfolge ist keine Priorität.

- **Granulare Berechtigungen pro Workflow/Ordner/Queue**: RBAC kennt heute nur drei globale Rollen
  (viewer/operator/admin, siehe "Benutzer & Rollen" oben) — kein "Team A darf nur Workflows in Ordner
  X sehen/starten". Setzt vermutlich eine Ordner-/Projektstruktur (siehe unten) als Voraussetzung
  voraus, bevor sich Rechte überhaupt sinnvoll darauf beziehen lassen.
- **Audit-Log**: wer hat wann welchen Workflow gespeichert, gestartet, gestoppt, ein Credential
  geändert oder einen Benutzer angelegt? Mit RBAC gibt es jetzt einzelne Konten, aber keine Historie
  ihrer Aktionen — relevant, sobald mehr als eine Person an derselben Installation arbeitet.
- **Proaktive Fehlerbenachrichtigung** (E-Mail/Slack/Webhook, wenn ein Job oder Queue-Item endgültig
  fehlschlägt): heute muss man aktiv die Runs-Ansicht oder `/api/jobs?status=error` abfragen. Die
  Bausteine (`send_email`-Aktion, `http_request`) existieren im Workflow selbst bereits — hier ginge es
  um eine Orchestrator-seitige Benachrichtigung, unabhängig vom Workflow-Inhalt.
- **Workflow-Versionierung**: "Speichern" überschreibt die YAML-Datei ohne Historie — kein Diff
  zwischen zwei Ständen, kein Zurückrollen auf eine ältere Version direkt im Studio. Ließe sich
  entweder über echtes Git im Hintergrund lösen oder über eine einfache eigene Versions-Tabelle
  (ähnlich den Job-Snapshots, die es für einzelne Läufe schon gibt).
- **Testbarkeit von Workflows**: ein "Dry-Run"-Modus, der Ausdrücke/Variablen/Selektoren gegen einen
  Fake-Backend validiert, ohne den echten Browser/Desktop anzufassen — würde Tippfehler in Python-
  Ausdrücken oder fehlende Variablen schon beim Speichern statt erst beim echten Lauf auffangen.
- **Business-Kalender für Zeitpläne**: Cron-Ausdrücke allein kennen keine Feiertage oder "nur an
  Werktagen" — ein Zeitplan, der z.B. jeden Monatsersten läuft, feuert damit auch an einem Feiertag
  oder Wochenende, falls der auf den Ersten fällt.
- **Human-in-the-loop / Action Center**: ein Schritt-Typ, der auf eine menschliche Entscheidung wartet
  (z.B. "Rechnung > 10.000€ manuell freigeben") über ein Web-Formular — nicht dasselbe wie ein
  Haltepunkt, der eine Person direkt am Studio voraussetzt, sondern eine asynchrone Freigabe, die auch
  jemand anderes später erledigen kann.
- **Ordner-/Projektstruktur**: Workflows, Queues und Credentials liegen heute alle flach nebeneinander
  (`workflows/*.yaml`, eine gemeinsame Queue-/Credential-Liste). Ab einer gewissen Anzahl Workflows
  fehlt eine Gruppierung (Ordner oder Projekte) — auch Voraussetzung für granulare Rechte pro Ordner
  (siehe oben).
- **Reporting/Analytics über die Zeit**: die Übersicht zeigt heute eine Momentaufnahme (Erfolgsquote,
  offene Queue-Items). Ein Trend über Zeit (Erfolgsquote pro Woche, durchschnittliche Laufzeit pro
  Workflow, Engpässe in einer Queue) fehlt.
- **Versionierte, projektübergreifende Bibliotheken**: ein `run_workflow`-Schritt referenziert heute
  eine Datei im selben `workflows/`-Verzeichnis per Namen — keine Versionierung, kein Teilen eines
  wiederverwendbaren Sub-Workflows über mehrere unabhängige Installationen hinweg.
- **Mobile-/App-Automatisierung** (Android/iOS, z.B. über Appium) als dritter Backend-Typ neben
  Web und Desktop — bisher nicht einmal angedacht, wäre ein eigener Backend + eigene
  Aktivitäten-Kategorie im Katalog.
