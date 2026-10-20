"""Command line: ``keptwise --help``."""

from __future__ import annotations

import contextlib
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import click
from click.core import ParameterSource

from . import db as storage
from . import reports
from .importer import ImportProblem, import_path
from .money import fmt
from .rules import categorise
from .seed import seed

DEFAULT_PORT = 8765  # 5000 clashes with AirPlay Receiver on macOS


def _connect(db_path: str | None, *, backup: bool = False):
    if backup:
        storage.backup(db_path)
    conn = storage.connect(db_path)
    seed(conn)
    return conn


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--db",
    "db_path",
    envvar=["KEPTWISE_DB", "BOOKKEEPING_DB"],
    type=click.Path(dir_okay=False),
    help="Database file (default: data/keptwise.db in a source checkout, else your data folder).",
)
@click.pass_context
def app(ctx: click.Context, db_path: str | None) -> None:
    """Import bank statements, track spending and plan monthly investments."""
    ctx.obj = {"db": db_path}


@app.command()
@click.option("--port", default=DEFAULT_PORT, show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--no-browser", is_flag=True, help="Don't open a browser tab.")
@click.option(
    "--idle-timeout",
    default=0,
    show_default=True,
    metavar="MINUTES",
    help="Stop by itself after this long without use (0 = keep running).",
)
@click.option("--debug", is_flag=True, help="Auto-reload on code changes.")
@click.pass_context
def serve(
    ctx: click.Context, port: int, host: str, no_browser: bool, idle_timeout: int, debug: bool
) -> None:
    """Start the app and open it in your browser."""
    url = f"http://{host}:{port}/"
    if is_running(url):
        click.echo(f"Keptwise is already running at {url}")
        if not no_browser:
            webbrowser.open(url)
        return

    from .web import create_app

    flask_app = create_app(ctx.obj["db"])
    if not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if debug:
        click.secho(f"Keptwise is running at {url}  (Ctrl+C to stop)", fg="green")
        flask_app.run(host=host, port=port, debug=True, use_reloader=True)
        return

    import logging

    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no per-request log lines
    server = make_server(host, port, flask_app, threaded=True)
    last_used = [time.monotonic()]

    @flask_app.before_request
    def _touch() -> None:
        last_used[0] = time.monotonic()

    def stop() -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    flask_app.config["SHUTDOWN"] = stop
    if idle_timeout > 0:

        def watch() -> None:
            while True:
                time.sleep(15)
                if time.monotonic() - last_used[0] > idle_timeout * 60:
                    server.shutdown()
                    return

        threading.Thread(target=watch, daemon=True).start()

    stops = f", stops after {idle_timeout} min unused" if idle_timeout else ""
    click.secho(f"Keptwise is running at {url}  (Ctrl+C to stop{stops})", fg="green")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    click.echo("Keptwise stopped.")


def is_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "healthz", timeout=1) as response:
            return response.read() == b"ok"
    except OSError:
        return False


@app.command()
@click.option("--port", default=DEFAULT_PORT, show_default=True)
def stop(port: int) -> None:
    """Stop an app started in the background."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/shutdown", method="POST")
    try:
        urllib.request.urlopen(request, timeout=2).close()
    except OSError:
        click.echo("Keptwise isn't running.")
        return
    click.echo("Keptwise stopped.")


@app.command("import")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def import_command(ctx: click.Context, files: tuple[str, ...]) -> None:
    """Import one or more bank CSV statements."""
    conn = _connect(ctx.obj["db"], backup=True)
    failed = False
    for path in files:
        try:
            result = import_path(conn, path)
        except ImportProblem as exc:
            click.secho(f"{Path(path).name}: {exc}", fg="red", err=True)
            failed = True
            continue
        click.echo(result.summary)
    uncategorised = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL"
    ).fetchone()[0]
    if uncategorised:
        noun = "transaction has" if uncategorised == 1 else "transactions have"
        click.secho(f"{uncategorised} {noun} no category yet.", fg="yellow")
    if failed:
        raise SystemExit(1)


@app.command()
@click.argument("month", required=False)
@click.pass_context
def summary(ctx: click.Context, month: str | None) -> None:
    """Show a month's summary and investment plan (default: latest month)."""
    conn = _connect(ctx.obj["db"])
    months = reports.months(conn)
    if not months:
        raise click.ClickException("Nothing imported yet. Try: keptwise import FILE.csv")
    month = month or months[-1]
    if month not in months:
        raise click.ClickException(f"No transactions for {month}. Months: {', '.join(months)}")
    s = reports.summarise(conn, month)
    click.secho(s.label, bold=True)
    for key in ("regular", "cash", "adhoc", "investment"):
        b = s.buckets[key]
        click.echo(
            f"  {b.label:<12} in {fmt(b.money_in):>11}   out {fmt(b.money_out):>11}"
            f"   net {fmt(b.net):>11}"
        )
    click.echo(f"  {'Kept':<12} {fmt(s.surplus):>51}")
    if s.surplus > 0:
        plan = ", ".join(f"{fmt(a.amount)} into {a.name}" for a in s.allocations if a.share)
        click.secho(f"In {s.next_label}: put {plan}.", fg="green")
    else:
        click.secho(f"Over by {fmt(-s.surplus)}: nothing to invest in {s.next_label}.", fg="red")
    from .insights import month_flags

    flags = month_flags(conn, month)
    if flags:
        click.echo("\nWorth a look:")
        for flag in flags:
            click.echo(f"  • {flag.message}")
    if s.uncategorised_count:
        n = s.uncategorised_count
        click.secho(
            f"{n} transaction{'s' if n != 1 else ''} need{'s' if n == 1 else ''} a category.",
            fg="yellow",
        )


@app.command()
@click.pass_context
def recategorise(ctx: click.Context) -> None:
    """Re-apply rules to every transaction you haven't categorised by hand."""
    conn = _connect(ctx.obj["db"])
    with conn:
        changed = categorise(conn)
    click.echo(f"{changed} transactions changed category.")


@app.command()
@click.option("--months", default=12, show_default=True, help="How many months to make up.")
@click.option("--port", default=DEFAULT_PORT + 1, show_default=True)
@click.option("--no-serve", is_flag=True, help="Only create the demo database.")
@click.option("--no-browser", is_flag=True, help="Don't open a browser tab.")
@click.pass_context
def demo(ctx: click.Context, months: int, port: int, no_serve: bool, no_browser: bool) -> None:
    """Try the app with a made-up year of data (kept apart from your own).

    Stop it with ``keptwise stop --port 8766``.
    """
    from . import demo as demo_data

    # Only an explicit --db is honoured here: KEPTWISE_DB points at your real data.
    explicit = ctx.parent.get_parameter_source("db_path") == ParameterSource.COMMANDLINE
    if explicit:
        path = Path(ctx.obj["db"])
    else:
        path = storage.default_db_path().parent / "demo.db"
        for old in (path, path.with_name(path.name + "-journal")):
            old.unlink(missing_ok=True)  # always start the demo afresh
    conn = _connect(str(path))
    try:
        added = demo_data.build(conn, months=months)
    except ValueError as exc:
        raise click.ClickException(f"{path} already has transactions. {exc}") from exc
    finally:
        conn.close()
    click.echo(f"Made up {added} transactions over {months} months in {path}")
    if not no_serve:
        ctx.obj["db"] = str(path)
        ctx.invoke(serve, port=port, no_browser=no_browser)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
