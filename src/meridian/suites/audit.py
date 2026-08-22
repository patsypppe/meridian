"""Can a task be passed without doing the work?

A suite is a measuring instrument, and nobody calibrates it. Tasks are written
by watching an agent fail, and a task written that way is only ever checked
against agents that are *trying* — so an assertion that a do-nothing agent also
satisfies looks exactly like a task that works.

This is not a theoretical worry. In 2026, independent researchers passed several
major agent benchmarks without solving them: a validator on one benchmark checked
only that the last message came from the assistant, so an agent replying `"{}"`
scored 1.0 on all 890 tasks. A separate study found a do-nothing agent passing
38% of one widely used agent benchmark's airline tasks.

So: run the suite against agents that are known not to have done the work, and
any task that still passes is not measuring what it claims to measure. This is
the contamination probe's idea — *a check that cannot fail proves nothing* —
generalized from Meridian's isolation to the user's own assertions.

The adversaries are deliberately not clever. A sophisticated attacker is a
different exercise; these are the floor, and a task that cannot survive the floor
does not need a sophisticated attacker to be worthless.

Pure: no I/O, no Docker, per the layout rule in `CLAUDE.md`. The runner supplies
outcomes; this module only decides what they mean.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "ADVERSARIES",
    "Adversary",
    "AuditFinding",
    "audit_report",
    "findings",
]


@dataclass(frozen=True)
class Adversary:
    """An agent that is known not to have done the work.

    `why_it_should_fail` is rendered in the report. A finding a reader cannot
    interpret is a finding that gets ignored, and the useful part is not "this
    task passed" but "this task passed *for an agent that did nothing at all*".
    """

    name: str
    adapter: str
    why_it_should_fail: str


ADVERSARIES: tuple[Adversary, ...] = (
    Adversary(
        name="null",
        adapter="subprocess:probes:noop",
        why_it_should_fail="exits successfully having touched nothing",
    ),
    Adversary(
        name="empty-scaffold",
        adapter="subprocess:probes:empty_scaffold",
        why_it_should_fail=(
            "creates the output directory and leaves it empty, which passes any "
            "assertion checking that a path exists rather than what is in it"
        ),
    ),
    Adversary(
        name="plausible-garbage",
        adapter="subprocess:probes:plausible_garbage",
        why_it_should_fail=(
            "writes well-formed JSON with invented values, which passes any "
            "assertion checking that a document parses rather than what it says"
        ),
    ),
)


@dataclass(frozen=True)
class AuditFinding:
    """One task that an adversary passed."""

    task_slug: str
    adversary: str
    why_it_should_fail: str

    @property
    def headline(self) -> str:
        return (
            f"{self.task_slug} passes for the {self.adversary} agent, which "
            f"{self.why_it_should_fail}"
        )


def findings(
    passes: Mapping[str, Iterable[str]],
    adversaries: Sequence[Adversary] = ADVERSARIES,
) -> list[AuditFinding]:
    """Turn "which tasks did each adversary pass" into findings.

    `passes` maps an adversary name to the task slugs it passed. Every entry is a
    finding: an adversary passing anything is the definition of the problem.
    """
    by_name = {adversary.name: adversary for adversary in adversaries}
    found: list[AuditFinding] = []
    for name, slugs in passes.items():
        adversary = by_name.get(name)
        why = adversary.why_it_should_fail if adversary else "did not do the work"
        found += [AuditFinding(slug, name, why) for slug in sorted(slugs)]
    # Task first: a reader is auditing their suite, so the task they have to go
    # fix is the useful sort key, not the adversary that happened to find it.
    return sorted(found, key=lambda f: (f.task_slug, f.adversary))


def audit_report(found: Sequence[AuditFinding], audited: Sequence[str]) -> str:
    """The operator-facing summary."""
    lines = [f"audited {len(audited)} task(s) against {len(ADVERSARIES)} adversaries", ""]
    if not found:
        lines += [
            "No task passed for an agent that did no work.",
            "",
            "This does not prove the assertions are right — only that they are not "
            "trivially satisfiable, which is the floor rather than the ceiling.",
        ]
        return "\n".join(lines)

    weak = sorted({finding.task_slug for finding in found})
    lines += [f"{len(weak)} task(s) can be passed without doing the work:", ""]
    lines += [f"  {finding.headline}" for finding in found]
    lines += [
        "",
        "Each of these is measuring something weaker than it claims. The usual cause "
        "is an assertion that checks a file exists, or parses, rather than checking "
        "what it says.",
    ]
    return "\n".join(lines)
