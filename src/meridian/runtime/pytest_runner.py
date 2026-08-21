"""Running pytest against extracted state, in a throwaway container.

The test files come from a directory the agent controlled. Running them on the
host would give an evaluated agent code execution on the machine doing the
evaluating, which is the one thing an eval harness must never do.

So grading-by-pytest gets its own container: the task's own snapshot, the same
isolation options as a trial, and the extracted state streamed in on stdin. It
is destroyed afterwards like any other.
"""

from __future__ import annotations

import contextlib
import io
import socket
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from meridian.grading.graders.pytest_grader import PytestOutcome
from meridian.runtime.isolation import RUN_LABEL, TRIAL_GID, TRIAL_LABEL, TRIAL_UID

if TYPE_CHECKING:  # pragma: no cover - typing only
    from docker import DockerClient

GRADING_TIMEOUT_SECONDS = 120
PYTEST_INTERNAL_ERROR = 3


def _archive(state_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in sorted(state_dir.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            info = tarfile.TarInfo(name=path.relative_to(state_dir).as_posix())
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid, info.gid = TRIAL_UID, TRIAL_GID
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class ContainerPytestRunner:
    """A `PytestRunner` backed by a throwaway container."""

    def __init__(
        self,
        client: DockerClient,
        *,
        image: str,
        run_id: str,
        workdir: str = "/work",
    ) -> None:
        self._client = client
        self._image = image
        self._run_id = run_id
        self._workdir = workdir

    def __call__(self, state_dir: Path, target: str, selector: str | None) -> PytestOutcome:
        relative = target.removeprefix(self._workdir).lstrip("/") or "."
        argument = f"{relative}::{selector}" if selector else relative
        command = (
            f'tar -xf - -C "{self._workdir}" && cd "{self._workdir}" '
            f'&& python3 -m pytest -q "{argument}"'
        )

        volume = self._client.volumes.create(
            name=f"meridian-grade-{self._run_id}-{abs(hash(target)) % 10**8}",
            labels={RUN_LABEL: self._run_id},
        )
        container = None
        try:
            container = self._client.containers.create(
                image=self._image,
                entrypoint=["/bin/sh", "-c"],
                command=[command],
                user=f"{TRIAL_UID}:{TRIAL_GID}",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                # Grading runs agent-authored code, so it gets no network at all
                # — not even the proxy. A grader has nothing to say to a model.
                network_mode="none",
                pids_limit=128,
                mem_limit="1024m",
                tmpfs={"/tmp": "size=64m,mode=1777"},
                volumes={volume.name: {"bind": self._workdir, "mode": "rw"}},
                working_dir=self._workdir,
                environment={},
                labels={RUN_LABEL: self._run_id, TRIAL_LABEL: "grading"},
                stdin_open=True,
                detach=True,
                auto_remove=False,
            )
            container.start()
            self._send(container, _archive(state_dir))
            status = container.wait(timeout=GRADING_TIMEOUT_SECONDS)
            output = container.logs().decode("utf-8", errors="replace")
            return PytestOutcome(exit_code=int(status.get("StatusCode", 1)), output=output)
        except Exception as exc:
            return PytestOutcome(
                exit_code=PYTEST_INTERNAL_ERROR,
                output=f"the grading container failed: {type(exc).__name__}: {exc}",
            )
        finally:
            if container is not None:
                with contextlib.suppress(Exception):
                    container.remove(force=True, v=True)
            with contextlib.suppress(Exception):
                volume.remove(force=True)

    @staticmethod
    def _send(container: Any, payload: bytes) -> None:
        attached = container.attach_socket(params={"stdin": 1, "stream": 1})
        raw = getattr(attached, "_sock", attached)
        response = getattr(attached, "_response", None)
        try:
            raw.sendall(payload)
            raw.shutdown(socket.SHUT_WR)
        finally:
            for closeable in (response, attached, raw):
                if closeable is not None:
                    with contextlib.suppress(Exception):
                        closeable.close()
