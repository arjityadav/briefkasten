"""Command line interface."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import click

from .config import Settings
from .models import REPLY_INTENTS
from .orchestrator import ApprovalError, Briefkasten
from .report import render
from .store import RunNotFound
from .tools.deadlines import resolve_deadline
from .tools.holidays_de import STATES


def _date(_ctx, _param, value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise click.BadParameter("use YYYY-MM-DD or DD.MM.YYYY")


def _app(need_llm: bool = True, **overrides) -> Briefkasten:
    """Approval, rejection and export never call a model, so they work without an API key."""
    from .llm import LLMError, build_router

    settings = Settings.from_env(**overrides)
    if not need_llm:
        return Briefkasten(None, settings)  # type: ignore[arg-type]
    try:
        return Briefkasten(build_router(settings), settings)
    except LLMError as e:
        raise click.ClickException(f"{e}\nTip: try `briefkasten demo` to see the pipeline without a key.") from None


def _live(app: Briefkasten, label: str | None = None):
    from .live import ConsoleProgress, attach_http_logging

    progress = ConsoleProgress()
    attach_http_logging(progress)
    r = app.router
    models = label or (
        f"{app.settings.provider} · smart {r.for_tier('smart').model} · fast {r.for_tier('fast').model}"
    )
    progress.info(f"live agent view ({models}); -q to hide")
    return progress


state_opt = click.option("--state", "bundesland", type=click.Choice(list(STATES), case_sensitive=False), help="Bundesland for public holidays (default: BRIEFKASTEN_STATE or BW)")


@click.group()
@click.version_option(package_name="briefkasten")
def main() -> None:
    """Briefkasten: understand official German mail, never miss a deadline, reply with confidence."""


@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--received", callback=_date, help="Date you received the letter (improves deadline maths)")
@click.option("--intent", type=click.Choice(list(REPLY_INTENTS)), help="Ask for a reply draft with this intent")
@state_opt
@click.option("--json", "as_json", is_flag=True, help="Print the run as JSON")
@click.option("-v", "--verbose", is_flag=True, help="Show every calculation step and the agent trace")
@click.option("-q", "--quiet", is_flag=True, help="Don't show agent steps live while the run is in progress")
def process(file: Path, received, intent, bundesland, as_json, verbose, quiet) -> None:
    """Analyse a letter (.txt, .pdf, .png, .jpg)."""
    app = _app(state=bundesland.upper() if bundesland else None)
    progress = None if quiet else _live(app)
    state = app.process_file(file.read_bytes(), file.name, received_on=received, reply_intent=intent, progress=progress)
    if progress:
        progress.info(f"run finished: {state.status}\n")
    click.echo(json.dumps(state.public_view(), indent=2, ensure_ascii=False) if as_json else render(state, verbose))
    if state.status == "awaiting_approval" and not as_json:
        click.echo(f"\nNext: briefkasten approve {state.run_id}")
    if state.status == "failed":
        raise SystemExit(1)


@main.command()
@click.argument("name", required=False, default="finanzamt_bescheid")
@click.option("-v", "--verbose", is_flag=True)
@click.option("-q", "--quiet", is_flag=True, help="Don't show agent steps live")
def demo(name: str, verbose: bool, quiet: bool) -> None:
    """Run the full pipeline offline with recorded agent replies (no API key needed)."""
    from .demo import DEMO_REDACT_TERMS, available_replays, load_replay

    if name not in available_replays():
        raise click.BadParameter(f"choose from {', '.join(available_replays())}")
    letter, intent, router = load_replay(name)
    settings = Settings.from_env(redact_terms=DEMO_REDACT_TERMS)
    app = Briefkasten(router, settings)
    progress = None if quiet else _live(app, "recorded model turns, tools run live")
    state = app.process(letter, source_name=f"{name}.txt", reply_intent=intent, progress=progress)
    if progress:
        progress.info(f"run finished: {state.status}\n")
    click.echo(click.style(f"[demo] replaying recorded agent outputs for {name}; everything else runs live\n", fg="cyan"))
    click.echo(render(state, verbose))
    click.echo(click.style(f"\n[demo] {len(state.redactions)} personal identifiers were masked before any model saw the letter.", fg="cyan"))
    if state.status == "awaiting_approval":
        click.echo(click.style(f"[demo] next: briefkasten approve {state.run_id}", fg="cyan"))


@main.command()
@click.argument("run_id")
@click.option("--edit/--no-edit", default=True, help="Open the draft in your editor before approving")
@click.option("--force", is_flag=True, help="Approve even if [placeholders] are still open")
def approve(run_id: str, edit: bool, force: bool) -> None:
    """Review, edit and approve a reply draft."""
    app = _app(need_llm=False)
    try:
        state = app.store.load(run_id)
    except RunNotFound:
        raise click.ClickException(f"No run {run_id}") from None
    if not state.draft:
        raise click.ClickException("This run has no draft")
    body = state.draft.body_de
    if edit:
        edited = click.edit(body + "\n", extension=".txt")
        body = edited if edited is not None else body
    try:
        app.approve(run_id, body_de=body, allow_open_placeholders=force)
    except ApprovalError as e:
        raise click.ClickException(str(e)) from None
    click.echo(click.style("Approved.", fg="green") + f" Export with: briefkasten export {run_id} -o reply.txt")


@main.command()
@click.argument("run_id")
@click.option("--reason")
def reject(run_id: str, reason: str | None) -> None:
    """Reject a reply draft."""
    try:
        _app(need_llm=False).reject(run_id, reason)
    except (ApprovalError, RunNotFound) as e:
        raise click.ClickException(str(e)) from None
    click.echo("Rejected.")


@main.command()
@click.argument("run_id")
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path))
def export(run_id: str, output: Path | None) -> None:
    """Export an approved reply as a letter, with your real details filled back in."""
    try:
        text = _app(need_llm=False).export_letter(run_id)
    except (ApprovalError, RunNotFound) as e:
        raise click.ClickException(str(e)) from None
    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"Saved to {output}")
    else:
        click.echo(text)


@main.command()
@click.argument("run_id", required=False)
@click.option("-v", "--verbose", is_flag=True)
def runs(run_id: str | None, verbose: bool) -> None:
    """List past runs, or show one."""
    store = Settings.from_env()
    from .store import RunStore

    rs = RunStore(store.data_dir)
    if run_id:
        try:
            click.echo(render(rs.load(run_id), verbose))
        except RunNotFound:
            raise click.ClickException(f"No run {run_id}") from None
        return
    for s in rs.list():
        nxt = min((d.due_date for d in s.deadlines if d.due_date), default=None)
        who = s.triage.sender_name if s.triage else "?"
        nxt_s = nxt.strftime("%d.%m.%Y") if nxt else "-"
        click.echo(f"{s.run_id}  {s.status:<18} {s.urgency:<8} next deadline: {nxt_s:<10}  {who}")


@main.command()
@click.option("--letter-date", callback=_date, required=True, help="Date printed on the letter")
@click.option("--months", type=int, default=0)
@click.option("--weeks", type=int, default=0)
@click.option("--days", type=int, default=0)
@click.option("--from", "start", type=click.Choice(["notification", "receipt"]), default="notification",
              help="notification: official decision (Bescheid); receipt: private letter")
@click.option("--received", callback=_date)
@click.option("--electronic", is_flag=True, help="Decision was sent electronically")
@state_opt
def deadline(letter_date, months, weeks, days, start, received, electronic, bundesland) -> None:
    """Compute a German deadline without any AI, e.g. --letter-date 13.10.2026 --months 1."""
    from .models import DeadlineRule, ExtractedDeadline

    if not (months or weeks or days):
        raise click.BadParameter("give --months, --weeks or --days")
    rule = DeadlineRule(
        kind="after_notification" if start == "notification" else "after_receipt",
        months=months, weeks=weeks, days=days,
        delivery="electronic" if electronic else "post",
    )
    dl = ExtractedDeadline(id="manual", purpose="deadline", kind="action_required", rule=rule, quote="")
    res = resolve_deadline(dl, letter_date=letter_date, received_on=received,
                           state=(bundesland or Settings.from_env().state).upper(), today=date.today())
    for s in res.steps:
        click.echo(f"  {s}")
    click.echo(click.style(f"Deadline: {res.due_date:%A, %d.%m.%Y}", bold=True) + f" ({res.days_left} days from today)")


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@main.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8000, type=int)
@click.option("--allow-public", is_flag=True, help="Serve the full app (uploads, stored runs) on a non-local address")
def serve(host: str, port: int, allow_public: bool) -> None:
    """Start the web app and API.

    The full app has no user accounts: anyone who can reach it can read every run. Off this machine,
    serve it with BRIEFKASTEN_DEMO_ONLY=1 (sample letters only), or behind your own login with --allow-public.
    """
    try:
        import uvicorn
    except ImportError:
        raise click.ClickException("Install the web extra: pip install 'briefkasten[web]'") from None
    s = Settings.from_env()
    if host not in LOCAL_HOSTS and not s.demo_only and not allow_public:
        raise click.ClickException(
            f"Refusing to serve the full app on {host}: it has no login, so anyone who can reach it could read "
            "every letter analysed here.\nSet BRIEFKASTEN_DEMO_ONLY=1 for a public demo (sample letters only), "
            "or pass --allow-public if it sits behind your own authentication."
        )
    if s.demo_only:
        click.echo("Demo-only mode: sample letters only, uploads refused, runs kept in memory for an hour.")
        if host not in LOCAL_HOSTS and not (s.imprint_url and s.privacy_url):
            click.echo(
                click.style("Note: ", fg="yellow")
                + "a public site in Germany needs an Impressum and a privacy policy. "
                "Set BRIEFKASTEN_IMPRINT_URL and BRIEFKASTEN_PRIVACY_URL to link them in the footer."
            )
    uvicorn.run("briefkasten.api:app", host=host, port=port)


if __name__ == "__main__":
    main()
