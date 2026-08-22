"""Every container security option, in one file.

This file exists so a reviewer can audit Rule 1 by reading one screen. If an
isolation-relevant option is set anywhere else in the codebase, that is a bug in
the codebase, not a shortcut.

Each option below is deliberate. The comment says why, because an option nobody
can justify is an option somebody will remove during a debugging session and
never put back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from meridian.models.task import TaskDefinition

RUN_LABEL = "meridian.run"
TRIAL_LABEL = "meridian.trial"

TMP_TMPFS = "size=64m,mode=1777"

VOLUME_PREFIX = "meridian"

TRIAL_UID = 10001
TRIAL_GID = 10001


@dataclass(frozen=True)
class IsolationPolicy:
    """How isolated a trial actually is.

    The workdir is backed by a Docker volume rather than a tmpfs, and that is not
    a convenience: a tmpfs is unmounted the moment the container stops, so there
    is nothing left to extract by the time the trial has a verdict. A volume
    created for one trial and destroyed with it is exactly as isolated and
    survives long enough to be graded.

    `unsafe_shared_env` swaps the per-trial volume for one shared across the whole
    run. That is a real contamination channel, which is the point: the probe must
    fail for a reason rather than by decree. It is genuinely reachable, and
    config validation rejects it in gate mode, because a run that shares state
    between trials must never produce a number anyone acts on.
    """

    unsafe_shared_env: bool = False

    @property
    def volume_is_per_trial(self) -> bool:
        return not self.unsafe_shared_env

    def workdir_volume(self, run_id: str, task_slug: str, trial_index: int) -> str:
        if self.unsafe_shared_env:
            return f"{VOLUME_PREFIX}-shared-{run_id}"
        return f"{VOLUME_PREFIX}-{run_id}-{task_slug}-{trial_index}-{uuid.uuid4().hex[:8]}"


ISOLATED = IsolationPolicy()


def container_kwargs(
    task: TaskDefinition,
    run_id: str,
    trial_index: int,
    *,
    workdir_volume: str,
    policy: IsolationPolicy = ISOLATED,
    network_name: str | None = None,
) -> dict[str, Any]:
    """Return the kwargs for one trial container.

    `network_name` is only consulted when the task asks for `proxy-only`; a task
    declaring `network: none` gets no network regardless, because the task
    definition is the authority on what the agent is allowed to reach.
    """
    workdir = task.environment.workdir
    resources = task.environment.resources

    kwargs: dict[str, Any] = {
        "image": task.environment.snapshot,
        # Never root. The image also sets USER, but an image is a suggestion and
        # this is the enforcement.
        "user": f"{TRIAL_UID}:{TRIAL_GID}",
        # The rootfs is immutable: the agent cannot install a package, patch a
        # library, or leave anything behind outside the mounts below.
        "read_only": True,
        # No capabilities at all. An eval agent needs none of them, and each one
        # retained is a way out of the box.
        "cap_drop": ["ALL"],
        # setuid binaries cannot escalate. Cheap, and closes the obvious path.
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": resources.pids,
        "mem_limit": f"{resources.memory_mb}m",
        # Equal to mem_limit, which means no swap. Docker otherwise defaults
        # memory-swap to twice memory, so a task asking for 2048MB silently gets
        # 4096MB of address space — the ceiling stops meaning what the task says
        # it means. Worse, a swapping trial is a slow trial, so the memory limit
        # leaks into the *timeout* and one task's ceiling starts deciding another
        # task's deadline.
        "memswap_limit": f"{resources.memory_mb}m",
        "nano_cpus": resources.nano_cpus,
        # We remove explicitly, in a finally, AFTER extracting final state.
        # auto_remove=True deletes the container out from under the extraction
        # step and turns every timeout into an unexplained empty result.
        "auto_remove": False,
        "detach": True,
        # The payload arrives on stdin, so the container must be created with
        # stdin open. It is closed as soon as the payload is sent.
        "stdin_open": True,
        # The run label is how the orphan sweeper finds containers a crashed
        # harness left behind.
        "labels": {RUN_LABEL: run_id, TRIAL_LABEL: str(trial_index)},
        # No secrets. Ever. The container talks to the proxy, which holds the
        # credential; an agent cannot exfiltrate a key it was never given.
        "environment": {"MERIDIAN_WORKDIR": workdir},
        "working_dir": workdir,
        "tmpfs": {"/tmp": TMP_TMPFS},
        "network_mode": "none",
        # The only writable mount. Per trial by default, so nothing an agent
        # writes can reach the next one.
        "volumes": {workdir_volume: {"bind": workdir, "mode": "rw"}},
    }

    if task.environment.network == "proxy-only" and network_name is not None:
        # Attached to an `internal: true` network whose only other member is the
        # proxy. The agent cannot reach the internet because there is no route,
        # not because a filter said no.
        kwargs["network_mode"] = network_name

    return kwargs


def describe(kwargs: dict[str, Any]) -> str:
    """One-line summary for logs and the runbook."""
    return (
        f"image={kwargs['image'][:19]}… user={kwargs['user']} read_only={kwargs['read_only']} "
        f"net={kwargs['network_mode']} caps=-{','.join(kwargs['cap_drop'])} "
        f"pids={kwargs['pids_limit']} mem={kwargs['mem_limit']}"
    )
