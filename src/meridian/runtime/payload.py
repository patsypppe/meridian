"""Build the tar the runner streams into a trial container over stdin.

Three delivery mechanisms were available and two of them are wrong:

- A **bind mount** is a shared-state channel between host and trial, and between
  trials if the path is reused. That defeats Rule 1 by construction.
- The **archive API** (`put_archive`) is refused outright on a read-only rootfs,
  and even without that, the workdir is a tmpfs: anything staged before the
  container starts is masked when the mount appears.

So the payload goes in on **stdin**, which is a pipe and cares about neither.
EOF marks the end — no marker file, no polling, no race, and no host path the
next trial could reach.
"""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Iterable
from pathlib import Path

from meridian.models.run import TrialSpec
from meridian.models.task import TaskDefinition

PAYLOAD_DIR = ".meridian"

# A launcher, generated rather than shipped, so the payload has exactly one
# well-known entry path regardless of how the harness package is laid out.
LAUNCHER = '''"""Generated launcher. Puts the shipped harness subset on the path."""
import sys
from pathlib import Path

payload = Path(__file__).resolve().parent
sys.path.insert(0, str(payload))
sys.path.insert(0, str(payload / "sut"))

from meridian.adapters.entrypoint import main

raise SystemExit(main(sys.argv))
'''

# The subset of the harness shipped into the container. It is deliberately small
# and contains no Docker, no store, and no statistics — a compromised agent gets
# a model contract and a subprocess runner, nothing else.
SHIPPED_MODULES = (
    "__init__.py",
    "version.py",
    "models/__init__.py",
    "models/adapter.py",
    "adapters/__init__.py",
    "adapters/base.py",
    "adapters/entrypoint.py",
    "adapters/subprocess_adapter.py",
    "adapters/langgraph_adapter.py",
)


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, *, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = mode
    # A fixed mtime keeps the archive byte-identical across trials, which makes
    # a diff between two payloads mean something.
    info.mtime = 0
    info.uid = 10001
    info.gid = 10001
    tar.addfile(info, io.BytesIO(data))


def _add_tree(tar: tarfile.TarFile, source: Path, prefix: str) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        arcname = f"{prefix}/{path.relative_to(source).as_posix()}"
        _add_bytes(tar, arcname, path.read_bytes())


def context_payload(
    task: TaskDefinition,
    spec: TrialSpec,
    *,
    prompt: str,
    deadline_unix_ms: int,
) -> dict[str, object]:
    """The serialized `TrialContext`, plus the adapter spec that resolves it."""
    return {
        "run_id": spec.run_id,
        "task_slug": spec.task_slug,
        "trial_index": spec.trial_index,
        "seed": spec.seed,
        "workdir": task.environment.workdir,
        "prompt": prompt,
        "model_base_url": spec.proxy_base_url,
        "max_tokens": spec.max_tokens,
        "deadline_unix_ms": deadline_unix_ms,
        "adapter_spec": spec.adapter_spec,
    }


def build_payload(
    task: TaskDefinition,
    spec: TrialSpec,
    *,
    suite_root: Path,
    harness_root: Path,
    sut_root: Path | None,
    deadline_unix_ms: int,
) -> bytes:
    """Return a tar archive to unpack at the workdir root."""
    prompt = (suite_root / task.input.prompt_file).read_text(encoding="utf-8")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        _add_bytes(
            tar,
            f"{PAYLOAD_DIR}/context.json",
            json.dumps(
                context_payload(task, spec, prompt=prompt, deadline_unix_ms=deadline_unix_ms),
                indent=2,
                sort_keys=True,
            ).encode("utf-8"),
        )
        _add_bytes(tar, f"{PAYLOAD_DIR}/prompt.txt", prompt.encode("utf-8"))
        _add_bytes(tar, f"{PAYLOAD_DIR}/entrypoint.py", LAUNCHER.encode("utf-8"))

        for relative in SHIPPED_MODULES:
            source = harness_root / relative
            if source.is_file():
                _add_bytes(tar, f"{PAYLOAD_DIR}/meridian/{relative}", source.read_bytes())

        if sut_root is not None and sut_root.is_dir():
            _add_tree(tar, sut_root, f"{PAYLOAD_DIR}/sut")

        workdir = task.environment.workdir.rstrip("/")
        for seed in task.input.files:
            relative_dest = seed.dest[len(workdir) :].lstrip("/")
            _add_bytes(tar, relative_dest, (suite_root / seed.src).read_bytes())

    return buffer.getvalue()


def extract_archive(
    chunks: Iterable[bytes], destination: Path, *, strip_components: int = 1
) -> None:
    """Unpack a `get_archive` stream, stripping the leading path component.

    `get_archive("/work")` yields members prefixed `work/`. Assuming a path
    instead of handling the archive is §16's gotcha about extraction, and it
    fails as a confusingly empty state directory rather than an error.
    """
    buffer = io.BytesIO(b"".join(chunks))
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=buffer, mode="r") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts[strip_components:]
            if not parts:
                continue
            target = destination.joinpath(*parts)
            # Refuse anything that would escape the destination. The archive
            # comes from a container the agent controlled.
            if not target.resolve().is_relative_to(destination.resolve()):
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(extracted.read())
