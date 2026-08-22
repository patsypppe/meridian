"""Auditing the suite itself — can a task be passed without doing the work?

The decision logic is trivial on purpose: an adversary passing *anything* is a
finding. What these tests pin is the shape of the report, because the report is
the product here. A finding a reader cannot act on is a finding they will ignore.
"""

from __future__ import annotations

import pytest

from meridian.suites.audit import ADVERSARIES, AuditFinding, audit_report, findings

pytestmark = pytest.mark.unit


def test_a_clean_suite_produces_no_findings() -> None:
    assert findings({"null": [], "empty-scaffold": []}) == []


def test_every_task_an_adversary_passed_is_a_finding() -> None:
    found = findings({"null": ["happy-path"], "empty-scaffold": ["happy-path", "missing-field"]})
    assert len(found) == 3
    assert {f.task_slug for f in found} == {"happy-path", "missing-field"}


def test_findings_are_ordered_by_the_task_a_reader_has_to_fix() -> None:
    """Not by the adversary that happened to find it."""
    found = findings({"empty-scaffold": ["zebra"], "null": ["apple", "zebra"]})
    assert [(f.task_slug, f.adversary) for f in found] == [
        ("apple", "null"),
        ("zebra", "empty-scaffold"),
        ("zebra", "null"),
    ]


def test_a_finding_says_why_the_adversary_should_have_failed() -> None:
    """ "happy-path passed" is not actionable; naming what the agent did is."""
    found = findings({"null": ["happy-path"]})
    assert "did no" in found[0].why_it_should_fail or "touched nothing" in found[0].headline
    assert "happy-path" in found[0].headline
    assert "null" in found[0].headline


def test_an_unrecognised_adversary_still_produces_a_usable_finding() -> None:
    """A caller may add its own; the report must not lose the finding."""
    found = findings({"home-grown": ["happy-path"]})
    assert len(found) == 1
    assert found[0].why_it_should_fail


# -- the report --------------------------------------------------------------


def test_a_clean_report_does_not_overclaim() -> None:
    """Surviving the floor is not proof the assertions are right."""
    report = audit_report([], ["happy-path", "missing-field"])
    assert "No task passed" in report
    assert "does not prove" in report


def test_the_report_counts_tasks_not_findings() -> None:
    """Three findings against two tasks is two tasks to go and fix."""
    found = findings({"null": ["a-task"], "empty-scaffold": ["a-task", "b-task"]})
    report = audit_report(found, ["a-task", "b-task", "c-task"])
    assert "2 task(s) can be passed without doing the work" in report


def test_the_report_names_every_task_and_adversary() -> None:
    found = findings({"null": ["a-task"], "plausible-garbage": ["b-task"]})
    report = audit_report(found, ["a-task", "b-task"])
    assert "a-task" in report
    assert "b-task" in report
    assert "null" in report
    assert "plausible-garbage" in report


def test_the_report_names_the_usual_cause() -> None:
    """The finding is only half of it; the fix is the other half."""
    report = audit_report(findings({"null": ["a-task"]}), ["a-task"])
    assert "exists" in report and "parses" in report


# -- the adversaries themselves ----------------------------------------------


def test_every_adversary_is_a_subprocess_probe() -> None:
    """They must not depend on the framework under test, or they test it too."""
    assert all(a.adapter.startswith("subprocess:probes:") for a in ADVERSARIES)


def test_every_adversary_explains_itself() -> None:
    assert all(a.why_it_should_fail for a in ADVERSARIES)


def test_adversary_names_are_unique() -> None:
    """They key the results map; a duplicate silently drops one adversary's findings."""
    names = [a.name for a in ADVERSARIES]
    assert len(names) == len(set(names))


def test_a_finding_renders_without_a_registered_adversary() -> None:
    finding = AuditFinding("t", "custom", "did not do the work")
    assert "t" in finding.headline and "custom" in finding.headline
