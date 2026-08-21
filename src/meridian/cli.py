"""The Typer app — the only user-facing entry point.

Progress goes to stderr, results go to stdout, and the two are never interleaved
(`HANDOFF §9.3`) so that `meridian run ... > results.txt` stays useful.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from meridian import exit_codes
from meridian.suites.loader import load_suite
from meridian.suites.validate import SuiteValidationError
from meridian.version import __version__

app = typer.Typer(
    name="meridian",
    help="Agent evaluation with per-trial isolation, outcome grading, and pass^k.",
    no_args_is_help=True,
    add_completion=False,
)

suite_app = typer.Typer(help="Author, validate, and publish task suites.", no_args_is_help=True)
app.add_typer(suite_app, name="suite")


def err(message: str) -> None:
    """Progress and diagnostics — stderr, always."""
    print(message, file=sys.stderr)


def out(message: str) -> None:
    """Results — stdout, always."""
    print(message)


def _report_validation_failure(exc: SuiteValidationError) -> None:
    err(f"suite validation failed with {len(exc.issues)} issue(s):")
    width = max(len(issue.code) for issue in exc.issues)
    for issue in exc.issues:
        location = issue.file if issue.task_slug is None else f"{issue.file} [{issue.task_slug}]"
        field = f".{issue.field}" if issue.field and issue.field != "<root>" else ""
        err(f"  {issue.code:<{width}}  {location}{field}")
        err(f"  {'':<{width}}  └─ {issue.message}")


@app.command()
def version() -> None:
    """Print the harness version that will be written into run manifests."""
    out(__version__)


@suite_app.command("publish")
def suite_publish(
    path: Path = typer.Argument(..., help="Path to the suite directory."),
) -> None:
    """Validate a suite and print its content hash.

    The hash is what the manifest pins. It is stable across checkouts of the same
    commit and changes when any task changes — that is the whole contract.
    """
    try:
        suite = load_suite(path)
    except SuiteValidationError as exc:
        _report_validation_failure(exc)
        raise typer.Exit(exit_codes.HARNESS_ERROR) from exc

    err(f"suite {suite.slug} v{suite.version} — {len(suite.tasks)} task(s)")
    scored = suite.scored_tasks
    excluded = suite.excluded_tasks

    out(f"suite:         {suite.slug}")
    out(f"version:       {suite.version}")
    out(f"adapter:       {suite.adapter_spec}")
    out(f"content_hash:  {suite.content_hash()}")
    out("")
    out(f"{'task':<26} {'state':<12} {'role':<14} assertions")
    out("-" * 68)
    for task in suite.tasks:
        out(f"{task.slug:<26} {task.state:<12} {task.role:<14} {len(task.outcome_assertions)}")
    out("")
    out(f"scored tasks:   {len(scored)}")
    if excluded:
        names = ", ".join(t.slug for t in excluded)
        out(f"excluded:       {len(excluded)} ({names})")


if __name__ == "__main__":  # pragma: no cover
    app()
