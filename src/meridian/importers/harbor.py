"""Import tasks written in Harbor's task format.

Harbor (https://github.com/laude-institute/harbor, Apache-2.0) is the harness behind
Terminal-Bench 2.0 and ships adapters that convert dozens of established
benchmarks into one task shape:

    <task>/
      task.toml          timeouts, resources, metadata
      instruction.md     the prompt
      environment/       Dockerfile (or docker_image in task.toml) and its context
      tests/test.sh      hidden verifier: writes a reward to /logs/verifier/reward.txt
      solution/          reference solution (ignored here)

Reading that shape means Meridian can run those benchmarks without a hand port.

This module is deliberately honest about the gap between the two systems. Harbor
supports far more than Meridian does (multi-step tasks, GPUs, MCP servers, compose
sidecars, verifier networks). A task that needs any of that is reported as
unsupported with a specific reason and is not converted, because a task that
silently runs under different rules than its authors intended produces numbers
that mean nothing.

Pure parsing and planning: no Docker, no network.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from meridian.snapshots.registry import UNBUILT_DIGEST

TRIAL_UID = 10001
DEFAULT_WORKDIR = "/app"
DEFAULT_AGENT_TIMEOUT_SECONDS = 900
MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 3600

# Commands in a verifier script that need the network. The grading container has
# none, so a verifier that installs its own dependencies at test time cannot run.
NETWORK_COMMANDS = re.compile(
    r"\b(curl|wget|pip3?\s+install|uv\s+(pip|tool|run)|uvx|apt(-get)?\s+install|"
    r"npm\s+(install|i)\b|yarn\s+add|git\s+clone|conda\s+install|apk\s+add|yum\s+install)\b"
)
NON_DEBIAN_PACKAGE_MANAGERS = re.compile(r"\b(apk\s+add|yum\s+install|dnf\s+install|zypper)\b")

UNSUPPORTED = "unsupported"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = UNSUPPORTED


@dataclass
class HarborTask:
    root: Path
    name: str
    instruction: str
    agent_timeout_seconds: int
    cpus: str
    memory_mb: int
    workdir: str
    dockerfile: str | None
    docker_image: str | None
    environment_dir: Path | None
    test_files: dict[str, bytes]
    metadata: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not any(issue.severity == UNSUPPORTED for issue in self.issues)

    @property
    def tags(self) -> tuple[str, ...]:
        raw = self.metadata.get("tags") or []
        extra = [
            str(self.metadata[key]) for key in ("difficulty", "category") if key in self.metadata
        ]
        seen: list[str] = []
        for tag in [*map(str, raw), *extra]:
            cleaned = re.sub(r"[^a-z0-9_-]+", "-", tag.lower()).strip("-")
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return tuple(seen)


class HarborImportError(ValueError):
    """The directory is not a readable Harbor task at all."""


def find_task_dirs(path: Path) -> list[Path]:
    """A task directory, or every task directory beneath a dataset directory."""
    path = Path(path)
    if (path / "task.toml").is_file():
        return [path]
    found = sorted(p.parent for p in path.glob("*/task.toml")) or sorted(
        p.parent for p in path.glob("*/*/task.toml")
    )
    if not found:
        raise HarborImportError(f"no task.toml found in or directly under {path}")
    return found


def _without_comments(script: str) -> str:
    """Drop whole-line shell comments, so prose mentioning `curl` is not a command."""
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


# -- the one verifier rewrite we are willing to make ---------------------------------

_INSTALL_PACKAGES = {"curl", "wget", "ca-certificates"}


def _logical_lines(script: str) -> list[str]:
    """Join backslash continuations so a multi-line command is one line."""
    return re.sub(r"\\\n\s*", " ", script).splitlines()


def _rewrite_line(line: str) -> str | None:
    """`None` drops the line; otherwise the (possibly rewritten) line."""
    stripped = line.strip()
    if re.fullmatch(r"apt(-get)?\s+update(\s+-\S+)*", stripped):
        return None
    install = re.fullmatch(r"apt(-get)?\s+install\s+(?:-\S+\s+)*(.+)", stripped)
    if install and set(install.group(2).split()) <= _INSTALL_PACKAGES:
        return None
    if re.search(r"\bcurl\b.*astral\.sh/uv/.*\|\s*(ba)?sh\b", stripped):
        return None
    if re.fullmatch(r"(source|\.)\s+\$?\{?HOME\}?/\.local/bin/env", stripped):
        return None
    if stripped.startswith("uvx "):
        try:
            tokens = shlex.split(stripped)[1:]
        except ValueError:
            return line
        index = 0
        while index < len(tokens) and tokens[index] != "pytest":
            if tokens[index] in {"--with", "-w", "--python", "-p"}:
                index += 2
            elif tokens[index].startswith("--with=") or tokens[index].startswith("--python="):
                index += 1
            else:
                return line  # an option we do not understand: leave it, and refuse later
        if index >= len(tokens):
            return line
        rest = tokens[index + 1 :]
        kept: list[str] = []
        skip = False
        for token in rest:
            if skip:
                skip = False
            elif token == "--ctrf":
                skip = True  # the ctrf reporter plugin is not installed; it only writes JSON
            elif not token.startswith("--ctrf="):
                kept.append(token)
        return "python3 -m pytest " + " ".join(shlex.quote(t) for t in kept)
    return line


def rewrite_uv_boilerplate(script: str) -> str | None:
    """Rewrite the standard `install uv; uvx pytest` verifier preamble, or None.

    The preamble downloads uv and pytest at verify time, which the network-less
    grading container cannot do; the runtime overlay already ships pytest. The
    rewrite touches only that recognised pattern, and the caller re-checks the
    result for any remaining network command, so an unfamiliar script is refused
    rather than half-fixed. Returns None if nothing was rewritten.
    """
    lines = _logical_lines(script)
    rewritten = [_rewrite_line(line) for line in lines]
    out = [line for line in rewritten if line is not None]
    result = "\n".join(out) + ("\n" if script.endswith("\n") else "")
    return result if result != script else None


def _last_workdir(dockerfile: str) -> str | None:
    matches = re.findall(r"^\s*WORKDIR\s+(\S+)", dockerfile, re.MULTILINE | re.IGNORECASE)
    return matches[-1] if matches else None


def _clamp_timeout(value: Any, cap: int | None = None) -> int:
    """The task's agent timeout, within what Meridian accepts and an optional cap.

    A cap shortens the benchmark's own time limit, which changes what is being
    measured; callers that use it must say so when they report results.
    """
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        seconds = DEFAULT_AGENT_TIMEOUT_SECONDS
    ceiling = MAX_TIMEOUT_SECONDS if cap is None else min(cap, MAX_TIMEOUT_SECONDS)
    return max(MIN_TIMEOUT_SECONDS, min(seconds, ceiling))


def load_harbor_task(
    task_dir: Path, *, rewrite_boilerplate: bool = False, max_timeout_seconds: int | None = None
) -> HarborTask:
    """Read one task and record everything Meridian cannot honour.

    Raises `HarborImportError` only when the directory is not a task at all;
    unsupported features are recorded as issues so the caller can report them.
    """
    root = Path(task_dir)
    config_path = root / "task.toml"
    if not config_path.is_file():
        raise HarborImportError(f"{root} has no task.toml")
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise HarborImportError(f"{config_path} is not valid TOML: {exc}") from exc

    # Multi-step tasks keep their instructions under steps/, not at the root. That
    # is an unsupported feature to report, not a malformed task to abort on.
    multi_step = bool(config.get("steps")) or (root / "steps").is_dir()
    instruction_path = root / "instruction.md"
    if instruction_path.is_file():
        instruction = instruction_path.read_text(encoding="utf-8")
    elif multi_step:
        instruction = ""
    else:
        raise HarborImportError(f"{root} has no instruction.md")

    issues: list[Issue] = []
    environment = config.get("environment") or {}
    agent = config.get("agent") or {}
    verifier = config.get("verifier") or {}

    name = str((config.get("task") or {}).get("name") or root.name)

    # -- environment -----------------------------------------------------------------
    environment_dir = root / "environment"
    dockerfile_path = environment_dir / "Dockerfile"
    dockerfile = dockerfile_path.read_text(encoding="utf-8") if dockerfile_path.is_file() else None
    docker_image = environment.get("docker_image")
    if dockerfile is None and not docker_image:
        issues.append(
            Issue("no_environment", "no environment/Dockerfile and no docker_image in task.toml")
        )
    if any((environment_dir / f).exists() for f in ("docker-compose.yaml", "docker-compose.yml")):
        issues.append(
            Issue("docker_compose", "uses docker-compose services; Meridian runs one container")
        )
    if dockerfile and NON_DEBIAN_PACKAGE_MANAGERS.search(dockerfile):
        issues.append(
            Issue(
                "non_debian_base",
                "the Dockerfile uses a non-apt package manager; the Meridian runtime "
                "overlay needs a Debian or Ubuntu base",
            )
        )
    if str(environment.get("os", "linux")).lower() != "linux":
        issues.append(
            Issue("not_linux", f"os = {environment.get('os')!r}; only Linux is supported")
        )
    if (environment.get("gpus") or 0) > 0 or environment.get("tpu"):
        issues.append(Issue("accelerator", "needs a GPU or TPU"))
    if environment.get("mcp_servers"):
        issues.append(Issue("mcp_servers", "needs MCP servers"))
    if environment.get("healthcheck"):
        issues.append(Issue("healthcheck", "defines a healthcheck / sidecar readiness"))
    if verifier.get("environment") or verifier.get("environment_mode"):
        issues.append(Issue("verifier_environment", "verifies in a separate environment"))
    if multi_step:
        issues.append(Issue("multi_step", "multi-step task"))

    if environment.get("allow_internet"):
        issues.append(
            Issue(
                "internet",
                "allow_internet is set; Meridian trials reach only the model proxy",
                WARNING,
            )
        )
    if config.get("artifacts") or (config.get("environment") or {}).get("artifacts"):
        issues.append(Issue("artifacts", "artifact collection is ignored", WARNING))
    if agent.get("user") not in (None, "", 0, "root"):
        issues.append(
            Issue(
                "agent_user", "agent.user is ignored; trials run as an unprivileged user", WARNING
            )
        )

    # -- verifier --------------------------------------------------------------------
    tests_dir = root / "tests"
    test_files: dict[str, bytes] = {}
    if tests_dir.is_dir():
        for path in sorted(tests_dir.rglob("*")):
            if path.is_file():
                test_files[path.relative_to(tests_dir).as_posix()] = path.read_bytes()
    if "test.sh" not in test_files:
        issues.append(
            Issue(
                "no_test_script",
                "no tests/test.sh" + (" (found test.bat only)" if "test.bat" in test_files else ""),
            )
        )
    else:
        original = test_files["test.sh"].decode("utf-8", errors="replace")
        if rewrite_boilerplate and NETWORK_COMMANDS.search(_without_comments(original)):
            rewritten = rewrite_uv_boilerplate(original)
            if rewritten is not None and not NETWORK_COMMANDS.search(_without_comments(rewritten)):
                test_files["test.sh"] = rewritten.encode("utf-8")
                issues.append(
                    Issue(
                        "rewrote_verifier_boilerplate",
                        "replaced the verify-time `uv`/`uvx pytest` install with the "
                        "pytest already in the image",
                        WARNING,
                    )
                )
        script = _without_comments(test_files["test.sh"].decode("utf-8", errors="replace"))
        offenders = sorted({m.group(0).split()[0] for m in NETWORK_COMMANDS.finditer(script)})
        if offenders:
            issues.append(
                Issue(
                    "verifier_needs_network",
                    "tests/test.sh runs " + ", ".join(offenders) + "; the grading container "
                    "has no network, so it cannot install anything at verify time",
                )
            )

    workdir = (
        environment.get("workdir")
        or (_last_workdir(dockerfile) if dockerfile else None)
        or DEFAULT_WORKDIR
    )
    if not str(workdir).startswith("/"):
        issues.append(Issue("relative_workdir", f"workdir {workdir!r} is not absolute"))
        workdir = DEFAULT_WORKDIR

    cpus = environment.get("cpus") or 1
    return HarborTask(
        root=root,
        name=name,
        instruction=instruction,
        agent_timeout_seconds=_clamp_timeout(agent.get("timeout_sec"), max_timeout_seconds),
        cpus=str(cpus),
        memory_mb=int(environment.get("memory_mb") or 2048),
        workdir=str(workdir).rstrip("/") or "/",
        dockerfile=dockerfile,
        docker_image=docker_image,
        environment_dir=environment_dir if environment_dir.is_dir() else None,
        test_files=test_files,
        metadata=dict(config.get("metadata") or {}),
        issues=issues,
    )


def load_all(
    task_dirs: list[Path],
    *,
    rewrite_boilerplate: bool = False,
    max_timeout_seconds: int | None = None,
) -> tuple[list[HarborTask], list[Skipped]]:
    """Load every directory; one unreadable task is reported, never fatal to the batch."""
    tasks: list[HarborTask] = []
    unreadable: list[Skipped] = []
    for directory in task_dirs:
        try:
            tasks.append(
                load_harbor_task(
                    directory,
                    rewrite_boilerplate=rewrite_boilerplate,
                    max_timeout_seconds=max_timeout_seconds,
                )
            )
        except HarborImportError as exc:
            unreadable.append(Skipped(directory, directory.name, (Issue("unreadable", str(exc)),)))
    return tasks, unreadable


def slugify(name: str, taken: set[str] | None = None) -> str:
    """A valid, unique task slug (`^[a-z0-9-]{3,48}$`)."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().split("/")[-1]).strip("-") or "task"
    base = base[:48].strip("-")
    if len(base) < 3:
        base = f"task-{base}".strip("-")
    slug, n = base, 2
    while taken is not None and slug in taken:
        suffix = f"-{n}"
        slug = base[: 48 - len(suffix)].rstrip("-") + suffix
        n += 1
    if taken is not None:
        taken.add(slug)
    return slug


# -- the runtime overlay appended to every imported image ----------------------------

BOOTSTRAP_SH = """#!/bin/sh
# Trial bootstrap for imported tasks. PID 1 inside the trial container.
#
# The workdir is a per-trial volume that Docker seeds from the image, so the task's
# starting files are already in place. The harness streams the agent payload in on
# stdin as a tar; EOF means it is complete.
set -eu
WORKDIR="${MERIDIAN_WORKDIR:?MERIDIAN_WORKDIR is not set}"
tar -xf - -C "$WORKDIR"
PAYLOAD="$WORKDIR/.meridian"
if [ ! -f "$PAYLOAD/entrypoint.py" ]; then
    echo "meridian: payload did not contain an entrypoint" >&2
    exit 70
fi
export PYTHONPATH="$PAYLOAD:$PAYLOAD/sut${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$PAYLOAD/entrypoint.py" "$PAYLOAD/context.json"
"""

OVERLAY_TEMPLATE = """
# --- Meridian runtime overlay (generated by `meridian import-harbor`) ----------------
# Adds the agent runtime and an unprivileged trial user to the task's own image.
USER root
RUN (command -v python3 >/dev/null 2>&1 && command -v pip3 >/dev/null 2>&1) \\
    || (apt-get update \\
        && apt-get install -y --no-install-recommends python3 python3-pip \\
        && rm -rf /var/lib/apt/lists/*)
RUN python3 -m pip install --no-cache-dir --break-system-packages \\
        "langgraph==0.2.76" "langchain-core==0.3.34" "httpx==0.28.1" "pydantic==2.10.6" \\
        "pytest==8.4.1" \\
    || python3 -m pip install --no-cache-dir \\
        "langgraph==0.2.76" "langchain-core==0.3.34" "httpx==0.28.1" "pydantic==2.10.6" \\
        "pytest==8.4.1"
COPY meridian-bootstrap.sh /opt/meridian/bootstrap.sh
RUN chmod 0555 /opt/meridian/bootstrap.sh \\
    && (getent group {uid} >/dev/null || groupadd --gid {uid} trial) \\
    && (getent passwd {uid} >/dev/null || useradd --uid {uid} --gid {uid} --no-create-home trial) \\
    && mkdir -p "{workdir}" \\
    && chown -R {uid}:{uid} "{workdir}" \\
    && chmod -R u+rwX "{workdir}"
ENV MERIDIAN_WORKDIR="{workdir}"
USER {uid}:{uid}
WORKDIR {workdir}
ENTRYPOINT ["/opt/meridian/bootstrap.sh"]
"""


def overlay_dockerfile(task: HarborTask) -> str:
    """The task's own Dockerfile (or `FROM docker_image`) plus the runtime overlay."""
    base = task.dockerfile if task.dockerfile is not None else f"FROM {task.docker_image}\n"
    if not base.endswith("\n"):
        base += "\n"
    return base + OVERLAY_TEMPLATE.format(uid=TRIAL_UID, workdir=task.workdir)


# -- writing a Meridian suite --------------------------------------------------------


@dataclass(frozen=True)
class Converted:
    slug: str
    source: Path
    warnings: tuple[Issue, ...]


@dataclass(frozen=True)
class Skipped:
    source: Path
    name: str
    issues: tuple[Issue, ...]


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def write_task(task: HarborTask, suite_dir: Path, slug: str) -> None:
    """Write one converted task into `suite_dir` (see `write_suite`)."""
    prompt_rel = f"prompts/{slug}.txt"
    _write(suite_dir / prompt_rel, task.instruction)

    grader_files = []
    for relative, data in task.test_files.items():
        source_rel = f"grader/{slug}/{relative}"
        _write(suite_dir / source_rel, data)
        grader_files.append({"src": source_rel, "dest": f"/tests/{relative}"})

    image_dir = suite_dir / "images" / slug
    if task.environment_dir is not None:
        for path in sorted(task.environment_dir.rglob("*")):
            if path.is_file() and path.name != "Dockerfile":
                _write(image_dir / path.relative_to(task.environment_dir), path.read_bytes())
    _write(image_dir / "Dockerfile", overlay_dockerfile(task))
    _write(image_dir / "meridian-bootstrap.sh", BOOTSTRAP_SH)

    origin = f"harbor:{task.name}"
    document = {
        "slug": slug,
        "description": f"Imported from Harbor task {task.name!r}.",
        "provenance": {"synthetic_reason": f"imported from the {origin} benchmark task"},
        "environment": {
            "snapshot": UNBUILT_DIGEST,
            "workdir": task.workdir,
            "network": "proxy-only",
            "resources": {"cpus": task.cpus, "memory_mb": task.memory_mb, "pids": 512},
        },
        "input": {"prompt_file": prompt_rel},
        "grader_files": grader_files,
        "limits": {
            "timeout_seconds": task.agent_timeout_seconds,
            "max_tokens": 200_000,
            "budget_cents": 50,
        },
        "outcome_assertions": [{"kind": "verifier_script", "path": "/tests/test.sh"}],
        "tags": ["harbor", *task.tags],
    }
    _write(
        suite_dir / "tasks" / f"{slug}.yaml",
        yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
    )


def write_suite(
    tasks: list[HarborTask],
    suite_dir: Path,
    *,
    suite_slug: str,
    adapter: str = "langgraph:terminal_agent.agent:graph",
) -> tuple[list[Converted], list[Skipped]]:
    """Convert every supported task; report the rest. Existing slugs are not reused."""
    suite_dir = Path(suite_dir)
    taken: set[str] = {p.stem for p in (suite_dir / "tasks").glob("*.yaml")}
    converted: list[Converted] = []
    skipped: list[Skipped] = []
    for task in tasks:
        if not task.supported:
            skipped.append(
                Skipped(
                    task.root, task.name, tuple(i for i in task.issues if i.severity == UNSUPPORTED)
                )
            )
            continue
        slug = slugify(task.name, taken)
        write_task(task, suite_dir, slug)
        converted.append(
            Converted(slug, task.root, tuple(i for i in task.issues if i.severity == WARNING))
        )
    if converted:
        header = suite_dir / "suite.yaml"
        if not header.exists():
            _write(
                header,
                yaml.safe_dump(
                    {
                        "slug": suite_slug,
                        "version": 1,
                        "description": "Tasks imported from Harbor's task format.",
                        "adapter": adapter,
                        "tags": ["harbor", "imported"],
                    },
                    sort_keys=False,
                ),
            )
    return converted, skipped
