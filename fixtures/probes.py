"""Deterministic probe agents for the harness's own tests.

These exist so isolation, timeout, and retry behaviour can be tested without a
model in the loop. A probe that depended on a model would make the contamination
result depend on the model's mood, which is not a test of isolation.

Each callable takes the trial context and returns argv for the subprocess
adapter.
"""

from __future__ import annotations

from typing import Any


def write_marker(ctx: Any) -> list[str]:
    """Write a marker into the workdir. Half of the contamination probe."""
    return ["/bin/sh", "-c", f"echo meridian-was-here > {ctx.workdir}/marker.txt"]


def noop(ctx: Any) -> list[str]:
    """Do nothing successfully. The other half: it only asserts."""
    return ["/bin/true"]


def always_fails(ctx: Any) -> list[str]:
    """Fail every time. Used to prove agent failures are never retried."""
    return ["/bin/sh", "-c", "echo the agent could not do it >&2; exit 3"]


def hang(ctx: Any) -> list[str]:
    """Run past any sane deadline. Used to prove the container is killed."""
    return ["/bin/sh", "-c", "sleep 900"]


def touch_output(ctx: Any) -> list[str]:
    """Produce a trivially gradeable artifact."""
    return ["/bin/sh", "-c", f"mkdir -p {ctx.workdir}/out && echo ok > {ctx.workdir}/out/done.txt"]


def report_uid(ctx: Any) -> list[str]:
    """Leave a marker only if the process is the unprivileged trial user."""
    return [
        "/bin/sh",
        "-c",
        f'test "$(id -u)" = "10001" && touch {ctx.workdir}/out/uid-ok',
    ]


def probe_readonly_rootfs(ctx: Any) -> list[str]:
    """Leave a marker only if writing outside the workdir is refused."""
    return [
        "/bin/sh",
        "-c",
        f"echo escape > /etc/meridian-probe 2>/dev/null || touch {ctx.workdir}/out/rootfs-readonly",
    ]


def probe_no_network(ctx: Any) -> list[str]:
    """Leave a marker only if name resolution fails."""
    return [
        "/bin/sh",
        "-c",
        "python3 -c \"import socket; socket.gethostbyname('example.com')\" 2>/dev/null "
        f"|| touch {ctx.workdir}/out/no-network",
    ]


def dump_env(ctx: Any) -> list[str]:
    """Write the trial's whole environment out so a test can inspect it."""
    return ["/bin/sh", "-c", f"env > {ctx.workdir}/out/env.txt"]


def reach_proxy(ctx: Any) -> list[str]:
    """Leave a marker only if the proxy is reachable by its network alias."""
    return [
        "/bin/sh",
        "-c",
        'python3 -c "import urllib.request as u; '
        f"u.urlopen('{ctx.model_base_url}/healthz', timeout=5).read()\" "
        f"&& touch {ctx.workdir}/out/proxy-reachable",
    ]


def reach_internet(ctx: Any) -> list[str]:
    """Leave a marker only if a public address is NOT reachable.

    Connects to an IP rather than a hostname so the test proves there is no
    route, not merely that DNS is unhelpful.
    """
    return [
        "/bin/sh",
        "-c",
        "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 443), timeout=4)\" "
        f"2>/dev/null || touch {ctx.workdir}/out/internet-unreachable",
    ]


def exhaust_memory(ctx: Any) -> list[str]:
    """Allocate until the cgroup kills us.

    Used to prove an OOM is classified as the agent's failure and never retried.
    Allocates in 8MB steps and touches each block, because an untouched
    allocation is not resident and the cgroup never notices it.
    """
    return [
        "python3",
        "-c",
        "b = []\nwhile True:\n    b.append(bytearray(8 * 1024 * 1024))\n",
    ]
