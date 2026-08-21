"""The Typer app — the only user-facing entry point.

Progress goes to stderr, results go to stdout, and the two are never interleaved
(`HANDOFF §9.3`) so that `meridian run ... > results.txt` stays useful.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from meridian import exit_codes
from meridian.docker_client import DockerUnavailableError, get_client
from meridian.snapshots.build import (
    SnapshotBuildError,
    build_snapshot,
    current_platform,
    rewrite_suite_snapshots,
)
from meridian.snapshots.registry import read_snapshot_ref
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

snapshot_app = typer.Typer(
    help="Build environment snapshots and pin them by digest.", no_args_is_help=True
)
app.add_typer(snapshot_app, name="snapshot")


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


@snapshot_app.command("build")
def snapshot_build(
    path: Path = typer.Argument(..., help="Path to the environment directory."),
    tag: str = typer.Option(..., "--tag", help="Human-readable tag for the built image."),
    write_ref: bool = typer.Option(
        False, "--write-ref", help="Record the digest in <env>/snapshot-ref.json."
    ),
    context: Path | None = typer.Option(
        None,
        "--context",
        help="Build context, when the image ships files from outside its own directory.",
    ),
    update_suite: Path | None = typer.Option(
        None,
        "--update-suite",
        help="Rewrite every task in this suite to pin the digest just built.",
    ),
) -> None:
    """Build an environment image and print its digest.

    An image ID is architecture-specific, so the digest committed from a laptop
    will not exist on an amd64 runner. `--update-suite` is how CI closes that
    gap: rebuild, then repin, then run.
    """
    try:
        client = get_client()
        digest = build_snapshot(client, path, tag=tag, write_ref=write_ref, context=context)
    except (DockerUnavailableError, SnapshotBuildError) as exc:
        err(f"snapshot build failed: {exc}")
        raise typer.Exit(exit_codes.HARNESS_ERROR) from exc

    err(f"built {tag} for {current_platform()}")
    if update_suite is not None:
        changed = rewrite_suite_snapshots(update_suite, digest)
        err(f"repinned {len(changed)} task file(s) in {update_suite}")
    out(digest)


@snapshot_app.command("show")
def snapshot_show(
    path: Path = typer.Argument(..., help="Path to the environment directory."),
) -> None:
    """Print the digest most recently built for this environment."""
    digest = read_snapshot_ref(path)
    if digest is None:
        err(f"no snapshot-ref.json in {path}; run `meridian snapshot build --write-ref` first")
        raise typer.Exit(exit_codes.HARNESS_ERROR)
    out(digest)


@app.command()
def run(
    suite: Path = typer.Option(..., "--suite", help="Path to the suite directory."),
    n: int | None = typer.Option(None, "--n", help="Trials per task."),
    k: int | None = typer.Option(None, "--k", help="The k in pass^k."),
    proxy_mode: str | None = typer.Option(
        None, "--proxy-mode", help="record | replay | passthrough."
    ),
    sut: Path = typer.Option(Path("fixtures"), "--sut", help="System under test root."),
    cassettes: Path | None = typer.Option(None, "--cassettes", help="Cassette directory."),
    budget: int | None = typer.Option(None, "--budget", help="Run cost cap, in cents."),
    concurrency: int | None = typer.Option(None, "--concurrency", help="Concurrent trials."),
    include_probes: bool = typer.Option(
        False, "--include-probes", help="Also run the harness's own contamination probes."
    ),
    unsafe_shared_env: bool = typer.Option(
        False,
        "--unsafe-shared-env",
        help="Share one workdir across every trial. Breaks Rule 1 on purpose, so the "
        "contamination probe has a failing direction. Rejected in gate mode.",
    ),
    gate_mode: bool = typer.Option(
        False, "--gate-mode", help="Apply gate-mode configuration rules."
    ),
    out: Path | None = typer.Option(None, "--out", help="Write the run result as JSON."),
    live_provider: bool = typer.Option(
        False,
        "--live-provider",
        help="Record against the real provider instead of the deterministic stub.",
    ),
) -> None:
    """Run a suite and print per-task pass@k and pass^k.

    Exits non-zero only if the harness itself failed. An agent that performed
    badly is a result, not an error — see the exit-code contract in HANDOFF §8.4.
    """
    import asyncio

    from meridian.config import RunMode, load_config
    from meridian.report import table
    from meridian.runtime.orchestrator import RunRequest, default_cassette_dir, execute

    try:
        loaded = load_suite(suite)
    except SuiteValidationError as exc:
        _report_validation_failure(exc)
        raise typer.Exit(exit_codes.HARNESS_ERROR) from exc

    config = load_config(
        "meridian.yaml",
        overrides={
            "execution": {
                "n_trials": n,
                "k": k,
                "proxy_mode": proxy_mode,
                "budget_cents": budget,
                "max_concurrent_trials": concurrency,
                "unsafe_shared_env": unsafe_shared_env or None,
            }
        },
    )
    mode = RunMode.GATE if gate_mode else RunMode.EXPLORATORY

    try:
        config.validate_for(mode)
        client = get_client()
    except (ValueError, DockerUnavailableError) as exc:
        err(str(exc))
        raise typer.Exit(exit_codes.HARNESS_ERROR) from exc

    request = RunRequest(
        suite=loaded,
        config=config,
        cassette_dir=cassettes or default_cassette_dir(loaded),
        sut_root=sut.resolve() if sut else None,
        mode=mode,
        include_probes=include_probes,
        use_stub_provider=not live_provider,
        progress=err,
    )

    try:
        result = asyncio.run(execute(client, request))
    except Exception as exc:
        err(f"run failed: {type(exc).__name__}: {exc}")
        raise typer.Exit(exit_codes.HARNESS_ERROR) from exc

    out_text = table.render(result)
    print(out_text)
    detail = table.failures(result)
    if detail:
        print("")
        print("failing trials:")
        print(detail)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        err(f"wrote {out}")


@app.command()
def sweep(
    run_id: str | None = typer.Option(None, "--run-id", help="Limit the sweep to one run."),
) -> None:
    """Remove containers and volumes a previous run left behind.

    An interrupted harness leaves containers holding CPU and memory and volumes
    holding disk, and neither announces itself. Run this after a crash, and it
    runs automatically before each new run.
    """
    from meridian.runtime.trial_runner import sweep_orphans

    try:
        client = get_client()
    except DockerUnavailableError as exc:
        err(f"sweep failed: {exc}")
        raise typer.Exit(exit_codes.HARNESS_ERROR) from exc

    removed = sweep_orphans(client, run_id=run_id)
    if not removed:
        err("nothing to sweep")
        return
    for name in removed:
        out(name)
    err(f"removed {len(removed)} orphaned resource(s)")


if __name__ == "__main__":  # pragma: no cover
    app()
