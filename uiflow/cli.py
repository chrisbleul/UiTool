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
    workflow = Workflow.load(args.workflow)
    backend = _make_backend(workflow.backend, headless=args.headless, browser_channel=workflow.browser_channel)
    engine = WorkflowEngine(backend)
    try:
        engine.run(workflow, on_breakpoint=_cli_breakpoint)
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
    """Run a standalone worker that claims and executes queued jobs from the orchestrator DB."""
    from .orchestrator.worker import run_worker_loop

    print(f"uiflow worker '{args.worker_id or '(auto)'}' polling for jobs (Ctrl+C to stop)")
    run_worker_loop(worker_id=args.worker_id, poll_interval=args.poll_interval)
    return 0


def cmd_scheduler(args: argparse.Namespace) -> int:
    """Run a standalone scheduler that enqueues jobs for due cron schedules (see orchestrator/db.py)."""
    from .orchestrator.worker import run_scheduler_loop

    print("uiflow scheduler polling for due schedules (Ctrl+C to stop)")
    run_scheduler_loop(poll_interval=args.poll_interval)
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
    worker_p.set_defaults(func=cmd_worker)

    scheduler_p = sub.add_parser("scheduler", help="Run a standalone scheduler that enqueues jobs for due cron schedules")
    scheduler_p.add_argument("--poll-interval", type=float, default=20.0, help="Seconds between schedule checks")
    scheduler_p.set_defaults(func=cmd_scheduler)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
