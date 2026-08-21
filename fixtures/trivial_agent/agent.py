"""A LangGraph graph that produces one file and stops."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


class State(TypedDict, total=False):
    context: dict[str, Any]
    turns: int
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    completed: bool
    error: str | None


def work(state: State) -> State:
    workdir = Path(state["context"]["workdir"])
    (workdir / "out").mkdir(parents=True, exist_ok=True)
    (workdir / "out" / "done.txt").write_text("ok\n", encoding="utf-8")
    return {
        "turns": 1,
        "tool_calls": [{"name": "write_file", "ok": True}],
        "input_tokens": 0,
        "output_tokens": 0,
        "completed": True,
        "error": None,
    }


def build_graph() -> Any:
    builder: StateGraph[State] = StateGraph(State)
    builder.add_node("work", work)
    builder.set_entry_point("work")
    builder.add_edge("work", END)
    return builder.compile()


graph = build_graph()
