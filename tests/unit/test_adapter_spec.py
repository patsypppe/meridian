"""Adapter spec parsing, and the import boundary that keeps Meridian agnostic."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from meridian.adapters.base import KNOWN_KINDS, AdapterSpec, AdapterSpecError

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[2] / "src" / "meridian"
ADAPTER_MODULE = SRC / "adapters" / "langgraph_adapter.py"


def test_a_spec_parses_into_its_three_parts() -> None:
    spec = AdapterSpec.parse("langgraph:checkout_agent.agent:graph")
    assert (spec.kind, spec.module, spec.attribute) == (
        "langgraph",
        "checkout_agent.agent",
        "graph",
    )
    assert str(spec) == "langgraph:checkout_agent.agent:graph"


@pytest.mark.parametrize(
    "raw",
    ["langgraph:only_two", "a:b:c:d", "", ":module:attr", "langgraph::attr", "langgraph:mod:"],
)
def test_malformed_specs_are_refused(raw: str) -> None:
    with pytest.raises(AdapterSpecError, match=r"kind:module:attribute|unknown adapter kind"):
        AdapterSpec.parse(raw)


def test_an_unknown_kind_names_what_is_available() -> None:
    with pytest.raises(AdapterSpecError, match="|".join(KNOWN_KINDS)):
        AdapterSpec.parse("autogen:mod:attr")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_module_in_the_harness_imports_langgraph() -> None:
    """Framework-agnostic as a structural property, not a claim.

    Stronger than "only the adapter imports it": nothing does. The LangGraph
    adapter reaches a compiled graph through `ainvoke`/`invoke` alone, so the
    framework is never a dependency of the harness — not even of the module named
    after it. A future adapter that needs the import must live in the container
    payload, and this test is what makes that a decision rather than a drift.
    """
    importers = [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if "langgraph" in _imported_modules(path)
    ]
    assert importers == [], importers


def _code_without_docstrings(path: Path) -> str:
    """Source with docstrings stripped, so prose about the rule is not the rule."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).lower()


def test_the_adapter_knows_nothing_about_the_checkout_domain() -> None:
    """A domain term in the adapter's *code* means the abstraction has leaked."""
    source = _code_without_docstrings(ADAPTER_MODULE)
    for term in ("coupon", "invoice", "order", "checkout_agent"):
        assert term not in source, f"the adapter mentions {term!r}"
