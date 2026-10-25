"""Build an environment image and resolve it to a digest.

Uses the Docker SDK rather than shelling out to the `docker` binary: shelling out
loses structured errors and makes reliable cleanup guesswork.
"""

from __future__ import annotations

import platform as platform_module
import re
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.snapshots.registry import require_digest, write_snapshot_ref

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient

# `environment.snapshot: "sha256:..."` inside a task YAML. Rewritten in place by
# `snapshot sync` so the pinned digest matches the architecture actually running.
# The value may be quoted (hand-written tasks) or bare (YAML dumped by importers).
SNAPSHOT_LINE = re.compile(r'^(?P<indent>\s*snapshot:\s*"?)sha256:[0-9a-f]{64}(?P<tail>"?.*)$')


class SnapshotBuildError(RuntimeError):
    """The image could not be built. A harness error, never a product verdict."""


def current_platform() -> str:
    machine = platform_module.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )
    return f"linux/{arch}"


def build_snapshot(
    client: DockerClient,
    path: str | Path,
    *,
    tag: str,
    write_ref: bool = False,
    context: str | Path | None = None,
) -> str:
    """Build the image at `path` and return its digest.

    Locally built images have no registry digest until they are pushed, so the
    image ID — the content hash of the image config — is the pin. It is
    content-addressed and architecture-specific, which is exactly the property
    that makes `snapshot sync` necessary.
    """
    env_path = Path(path).resolve()
    dockerfile = env_path / "Dockerfile"
    if not dockerfile.is_file():
        raise SnapshotBuildError(f"no Dockerfile at {dockerfile}")

    # The proxy image ships a slice of the harness, so its build context is the
    # repository root rather than its own directory.
    build_context = Path(context).resolve() if context is not None else env_path
    dockerfile_arg = (
        str(dockerfile.relative_to(build_context)) if context is not None else "Dockerfile"
    )

    try:
        image, _logs = client.images.build(
            path=str(build_context),
            dockerfile=dockerfile_arg,
            tag=tag,
            rm=True,
            forcerm=True,
            pull=False,
        )
    except (
        Exception
    ) as exc:  # docker.errors.BuildError and transport errors alike are harness errors
        raise SnapshotBuildError(f"building {env_path}: {exc}{_build_log_tail(exc)}") from exc

    digest = require_digest(str(image.id))
    if write_ref:
        write_snapshot_ref(env_path, digest=digest, tag=tag, platform=current_platform())
    return digest


def _build_log_tail(exc: Exception, lines: int = 15) -> str:
    """The end of the build log, where a failed Dockerfile step says why."""
    log = getattr(exc, "build_log", None)
    if not log:
        return ""
    messages = [
        str(entry.get("stream") or entry.get("error") or "").rstrip()
        for entry in log
        if isinstance(entry, dict)
    ]
    tail = [m for m in messages if m][-lines:]
    return "\n--- build log (tail) ---\n" + "\n".join(tail) if tail else ""


def rewrite_task_snapshot(task_file: str | Path, digest: str) -> bool:
    """Pin `digest` in one task file, in place. Returns whether the file changed.

    Same line-level rewrite as `rewrite_suite_snapshots`, for suites where every
    task has its own image.
    """
    require_digest(digest)
    path = Path(task_file)
    original = path.read_text(encoding="utf-8")
    lines = []
    for line in original.splitlines(keepends=True):
        match = SNAPSHOT_LINE.match(line.rstrip("\n"))
        if match:
            newline = "\n" if line.endswith("\n") else ""
            lines.append(f"{match.group('indent')}{digest}{match.group('tail')}{newline}")
        else:
            lines.append(line)
    rewritten = "".join(lines)
    if rewritten == original:
        return False
    path.write_text(rewritten, encoding="utf-8")
    return True


def rewrite_suite_snapshots(suite_path: str | Path, digest: str) -> list[Path]:
    """Point every task in a suite at `digest`, in place.

    This is a line-level rewrite rather than a YAML round-trip on purpose:
    round-tripping reorders keys and drops comments, and a task file that changes
    shape every time CI runs is a task file nobody can review.
    """
    require_digest(digest)
    tasks_dir = Path(suite_path) / "tasks"
    changed: list[Path] = []
    for task_file in sorted(tasks_dir.glob("*.yaml")):
        original = task_file.read_text(encoding="utf-8")
        rewritten_lines = []
        touched = False
        for line in original.splitlines(keepends=True):
            match = SNAPSHOT_LINE.match(line.rstrip("\n"))
            if match:
                newline = "\n" if line.endswith("\n") else ""
                rewritten_lines.append(
                    f"{match.group('indent')}{digest}{match.group('tail')}{newline}"
                )
                touched = True
            else:
                rewritten_lines.append(line)
        if touched:
            rewritten = "".join(rewritten_lines)
            if rewritten != original:
                task_file.write_text(rewritten, encoding="utf-8")
                changed.append(task_file)
    return changed
