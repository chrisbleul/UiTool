from __future__ import annotations

import argparse
import logging
import sys

from .engine import StepError, WorkflowEngine
from .models import Step, Workflow


def _cli_breakpoint(index: int, step: Step, variables: dict) -> None:
    print(f"\n>> Haltepunkt bei Schritt {index} ('{step.action}'). Weiter mit Enter...")
    if variables:
        print(f"   Variablen: {variables}")
    input()


def _make_backend(name: str, headless: bool, browser_channel: str | None = None):
    if name == "web":
        from .backends.web import WebBackend

        return WebBackend(headless=headless, channel=browser_channel)
    if name == "desktop":
        from .backends.desktop import DesktopBackend

        return DesktopBackend()
    raise ValueError(f"Unknown backend '{name}'")


def cmd_run(args: argparse.Namespace) -> int:
    from .orchestrator import db

    workflow = Workflow.load(args.workflow)
    backend = _make_backend(workflow.backend, headless=args.headless, browser_channel=workflow.browser_channel)
    engine = WorkflowEngine(backend)
    # The same global variables a job run would see, so running a workflow
    # straight from the CLI isn't subtly different from queuing it.
    db.init_db()
    try:
        engine.run(workflow, on_breakpoint=_cli_breakpoint, global_variables=db.get_global_variables())
        return 0
    except StepError as exc:
        logging.error(str(exc))
        return 1
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            close()


def cmd_inspect_desktop(args: argparse.Namespace) -> int:
    """Print the UI Automation element tree of a running window, to help build selectors."""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    # Desktop().windows() returns raw UIAWrapper objects, which lack
    # print_control_identifiers. Find the matching window's process, then build a
    # WindowSpecification (which does have it) scoped uniquely to that process.
    match = next((w for w in desktop.windows() if args.title.lower() in w.window_text().lower()), None)
    if match is None:
        print(f"No visible window matching title '{args.title}'")
        return 1
    desktop.window(process=match.process_id()).print_control_identifiers(depth=args.depth)
    return 0


def cmd_studio(args: argparse.Namespace) -> int:
    """Launch the local low-code workflow builder (Flask web UI) in the browser."""
    import threading
    import webbrowser

    from .studio.app import create_app

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    if not args.no_worker:
        from .orchestrator.worker import run_scheduler_loop, run_worker_loop

        threading.Thread(target=run_worker_loop, kwargs={"worker_id": "studio-embedded"}, daemon=True).start()
        threading.Thread(target=run_scheduler_loop, daemon=True).start()
        print("Embedded worker + scheduler started (pass --no-worker to run 'uiflow worker' separately instead)")

    print(f"uiflow studio running at {url} (Ctrl+C to stop)")
    create_app().run(host=args.host, port=args.port, threaded=True)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    """Run a standalone worker that claims and executes queued jobs - either
    directly against the local orchestrator.db (default), or, with
    --remote-url, against a Studio server on a different machine over HTTP
    (see orchestrator/remote_store.py) - a worker with no filesystem access to
    that machine's orchestrator.db at all."""
    from .orchestrator.worker import run_worker_loop

    store = None
    if args.remote_url:
        import getpass

        from .orchestrator.remote_store import RemoteStore, RemoteStoreError

        password = args.remote_password or getpass.getpass(f"Passwort für {args.remote_url}: ")
        store = RemoteStore(args.remote_url)
        try:
            store.login(password, username=args.remote_username)
        except RemoteStoreError as exc:
            print(f"Anmeldung an {args.remote_url} fehlgeschlagen: {exc}")
            return 1
        print(f"uiflow worker '{args.worker_id or '(auto)'}' polling {args.remote_url} for jobs (Ctrl+C to stop)")
    else:
        print(f"uiflow worker '{args.worker_id or '(auto)'}' polling for jobs (Ctrl+C to stop)")

    kwargs: dict = {
        "worker_id": args.worker_id,
        "poll_interval": args.poll_interval,
        "heartbeat_interval": args.heartbeat_interval,
    }
    if store is not None:
        kwargs["store"] = store
    run_worker_loop(**kwargs)
    return 0


def cmd_scheduler(args: argparse.Namespace) -> int:
    """Run a standalone scheduler that enqueues jobs for due cron schedules and
    sweeps jobs whose worker has gone silent (see orchestrator/db.py)."""
    from .orchestrator.worker import run_scheduler_loop

    print("uiflow scheduler polling for due schedules (Ctrl+C to stop)")
    run_scheduler_loop(poll_interval=args.poll_interval, stale_job_timeout=args.stale_job_timeout)
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    """Creates (or, with --update, updates the password/role of) a Studio user
    account - the entry point for switching an installation from the default,
    frictionless single-user mode into per-account login/RBAC (see
    orchestrator/db.py's users table). The moment one account exists,
    studio/app.py requires per-account login instead of the shared
    UIFLOW_STUDIO_PASSWORD gate (or no gate at all)."""
    import getpass

    from werkzeug.security import generate_password_hash

    from .orchestrator import db

    db.init_db()
    existing = db.get_user(args.username)
    if existing and not args.update:
        print(f"User '{args.username}' existiert bereits (--update verwenden, um Passwort/Rolle zu ändern)")
        return 1
    if not existing and args.update:
        print(f"User '{args.username}' existiert noch nicht (--update weglassen, um ihn anzulegen)")
        return 1

    password = args.password
    if not password:
        password = getpass.getpass("Passwort: ")
        if password != getpass.getpass("Passwort (Wiederholung): "):
            print("Passwörter stimmen nicht überein")
            return 1
    if not password:
        print("Passwort darf nicht leer sein")
        return 1

    password_hash = generate_password_hash(password)
    if existing:
        db.set_user_password(args.username, password_hash)
        db.set_user_role(args.username, args.role)
        print(f"User '{args.username}' aktualisiert (Rolle: {args.role})")
    else:
        db.create_user(args.username, password_hash, args.role)
        print(f"User '{args.username}' angelegt (Rolle: {args.role})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uiflow", description="MVP UI automation for desktop and web apps")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a workflow YAML file")
    run_p.add_argument("workflow", help="Path to workflow YAML file")
    run_p.add_argument("--headless", action="store_true", help="Run the web backend headless")
    run_p.set_defaults(func=cmd_run)

    inspect_p = sub.add_parser(
        "inspect-desktop", help="Print the UI Automation element tree of an open window (to find selectors)"
    )
    inspect_p.add_argument("title", help="Substring of the window title to inspect")
    inspect_p.add_argument("--depth", type=int, default=4, help="Tree depth to print (default: 4)")
    inspect_p.set_defaults(func=cmd_inspect_desktop)

    studio_p = sub.add_parser("studio", help="Launch the local low-code workflow builder (web UI)")
    studio_p.add_argument("--host", default="127.0.0.1")
    studio_p.add_argument("--port", type=int, default=8787)
    studio_p.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser tab")
    studio_p.add_argument(
        "--no-worker",
        action="store_true",
        help="Don't embed a worker thread; run 'uiflow worker' separately instead",
    )
    studio_p.set_defaults(func=cmd_studio)

    worker_p = sub.add_parser("worker", help="Run a standalone worker that executes queued orchestrator jobs")
    worker_p.add_argument("--worker-id", default=None, help="Identifier for this worker (default: auto-generated)")
    worker_p.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between queue polls when idle")
    worker_p.add_argument(
        "--remote-url",
        default=None,
        help="Studio server to poll over HTTP instead of a local orchestrator.db "
        "(e.g. http://studio-host:8787) - for a worker on a different machine",
    )
    worker_p.add_argument(
        "--remote-username", default=None, help="Only needed if the server uses per-account login (RBAC)"
    )
    worker_p.add_argument(
        "--remote-password", default=None, help="Prompted interactively if omitted (with --remote-url)"
    )
    worker_p.add_argument(
        "--heartbeat-interval",
        type=float,
        default=15.0,
        help="Seconds between heartbeats while a job is running, so 'uiflow scheduler' can tell a crashed "
        "worker apart from one still working (default: 15)",
    )
    worker_p.set_defaults(func=cmd_worker)

    scheduler_p = sub.add_parser("scheduler", help="Run a standalone scheduler that enqueues jobs for due cron schedules")
    scheduler_p.add_argument("--poll-interval", type=float, default=20.0, help="Seconds between schedule checks")
    scheduler_p.add_argument(
        "--stale-job-timeout",
        type=float,
        default=90.0,
        help="Seconds without a heartbeat before a running job is treated as orphaned (its worker likely "
        "crashed): marked 'error', any queue item it still held is handed back to the queue (default: 90)",
    )
    scheduler_p.set_defaults(func=cmd_scheduler)

    user_p = sub.add_parser(
        "create-user", help="Create or update a Studio user account (switches the Studio into per-account login/RBAC)"
    )
    user_p.add_argument("username")
    user_p.add_argument("--role", choices=["viewer", "operator", "admin"], default="admin")
    user_p.add_argument("--password", default=None, help="Set non-interactively (e.g. for scripting); otherwise prompted")
    user_p.add_argument(
        "--update", action="store_true", help="Update an existing user's password/role instead of creating a new one"
    )
    user_p.set_defaults(func=cmd_create_user)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
