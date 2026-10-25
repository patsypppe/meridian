"""The terminal agent's one tool: run a shell command.

Standard library only, so it can be tested on the host without LangGraph.

The command runs inside the trial container, which is already the sandbox: a
read-only root filesystem, an unprivileged user, no capabilities and no network.
This module adds only what any shell tool needs -- a time limit and bounded output.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

MAX_OUTPUT_CHARS = 8000
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "bash",
        "description": (
            "Run a shell command in the working directory. Returns the combined "
            "stdout/stderr and the exit code. Commands that run longer than the "
            "timeout are killed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run."},
                "timeout_seconds": {
                    "type": "integer",
                    "description": f"Time limit, at most {MAX_TIMEOUT_SECONDS} (default "
                    f"{DEFAULT_TIMEOUT_SECONDS}).",
                },
            },
            "required": ["command"],
        },
    }
]


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep the start and the end of long output; the middle is rarely the point."""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - limit
    return f"{text[:head]}\n... [{omitted} characters omitted] ...\n{text[-tail:]}"


def run_bash(command: str, *, workdir: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run `command` with bash in `workdir`. Never raises; failures are results."""
    timeout = DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    timeout = max(1, min(int(timeout), MAX_TIMEOUT_SECONDS))
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "TERM": "dumb",
    }
    try:
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=workdir,
            env=env,
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"") + (exc.stderr or b"")
        output = partial.decode("utf-8", errors="replace")
        return {
            "exit_code": None,
            "output": truncate(output),
            "error": f"timed out after {timeout}s",
        }
    except OSError as exc:
        return {"exit_code": None, "output": "", "error": f"{type(exc).__name__}: {exc}"}
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    return {"exit_code": completed.returncode, "output": truncate(output), "error": None}


def format_result(result: dict[str, Any]) -> str:
    """The text the model sees for one command."""
    lines = []
    if result["error"]:
        lines.append(f"[{result['error']}]")
    elif result["exit_code"] != 0:
        lines.append(f"[exit code {result['exit_code']}]")
    if result["output"]:
        lines.append(result["output"])
    return "\n".join(lines) if lines else "[no output]"
