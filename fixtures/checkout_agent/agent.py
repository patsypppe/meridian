"""The checkout agent: a small LangGraph graph over a tool-using model loop.

    plan → act ⇄ tools → finish

`plan` builds the system prompt from `prompts/planner.md`, which is the file the
seeded regression edits. `act` calls the model through the Meridian proxy — never
a provider directly, and with no credential of its own. `tools` executes whatever
the model asked for and feeds the results back.

The graph is deliberately ordinary. It is the system under test, not a showcase:
what matters is that a one-sentence prompt change breaks exactly one task.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from checkout_agent.tools import TOOL_SCHEMAS, Toolbox

MODEL = os.environ.get("MERIDIAN_FIXTURE_MODEL", "claude-sonnet-5")
MAX_TURNS = 8
REQUEST_TIMEOUT_SECONDS = 120.0

PROMPTS = Path(__file__).resolve().parent / "prompts"


def _append(existing: list[Any], incoming: list[Any]) -> list[Any]:
    return [*existing, *incoming]


class AgentState(TypedDict, total=False):
    context: dict[str, Any]
    messages: Annotated[list[dict[str, Any]], _append]
    system: str
    turns: int
    tool_calls: Annotated[list[dict[str, Any]], _append]
    input_tokens: int
    output_tokens: int
    error: str | None
    completed: bool


def plan(state: AgentState) -> AgentState:
    """Load the planner prompt. This is the file the seeded regression edits."""
    context = state["context"]
    planner = (PROMPTS / "planner.md").read_text(encoding="utf-8")
    return {
        "system": planner,
        "messages": [{"role": "user", "content": context["prompt"]}],
        "turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "tool_calls": [],
        "completed": False,
        "error": None,
    }


def act(state: AgentState) -> AgentState:
    """One model turn, through the proxy."""
    context = state["context"]
    body = {
        "model": MODEL,
        "max_tokens": int(context.get("max_tokens", 4096)),
        "system": state.get("system", ""),
        "messages": state.get("messages", []),
        "tools": TOOL_SCHEMAS,
    }
    headers = {
        "x-meridian-task": str(context.get("task_slug", "")),
        "x-meridian-trial": str(context.get("trial_index", 0)),
        "content-type": "application/json",
    }
    url = f"{context['model_base_url']}/v1/messages"

    try:
        response = httpx.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        return {"error": f"model call failed: {exc}", "completed": False}

    if response.status_code != 200:
        # A budget refusal or a cassette miss arrives here. Both are agent-visible
        # failures: the agent could not finish, and the trial is a `fail`.
        detail = response.text[:400]
        return {"error": f"model returned {response.status_code}: {detail}", "completed": False}

    payload = response.json()
    usage = payload.get("usage") or {}
    content = payload.get("content") or []

    return {
        "messages": [{"role": "assistant", "content": content}],
        "turns": state.get("turns", 0) + 1,
        "input_tokens": state.get("input_tokens", 0) + int(usage.get("input_tokens", 0)),
        "output_tokens": state.get("output_tokens", 0) + int(usage.get("output_tokens", 0)),
        "completed": payload.get("stop_reason") != "tool_use",
    }


def tools(state: AgentState) -> AgentState:
    """Execute whatever the model asked for, in order."""
    context = state["context"]
    toolbox = Toolbox(context["workdir"])
    last = state["messages"][-1]
    results: list[dict[str, Any]] = []
    recorded: list[dict[str, Any]] = []

    for block in last.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name"))
        arguments = dict(block.get("input") or {})
        try:
            output = toolbox.call(name, arguments)
            ok = "error" not in output
        except Exception as exc:
            output = {"error": f"{type(exc).__name__}: {exc}"}
            ok = False
        recorded.append({"name": name, "ok": ok})
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": json.dumps(output),
                "is_error": not ok,
            }
        )

    return {"messages": [{"role": "user", "content": results}], "tool_calls": recorded}


def route(state: AgentState) -> str:
    if state.get("error"):
        return "finish"
    if state.get("completed"):
        return "finish"
    if state.get("turns", 0) >= MAX_TURNS:
        # Out of turns is an agent failure, not a harness one: it had its budget
        # and did not finish.
        return "out_of_turns"
    last = state["messages"][-1]
    has_tool_use = any(
        isinstance(block, dict) and block.get("type") == "tool_use"
        for block in last.get("content", [])
    )
    return "tools" if has_tool_use else "finish"


def out_of_turns(state: AgentState) -> AgentState:
    return {"error": f"gave up after {MAX_TURNS} turns", "completed": False}


def finish(state: AgentState) -> AgentState:
    return {"completed": state.get("error") is None}


def build_graph() -> Any:
    builder: StateGraph[AgentState] = StateGraph(AgentState)
    builder.add_node("plan", plan)
    builder.add_node("act", act)
    builder.add_node("tools", tools)
    builder.add_node("out_of_turns", out_of_turns)
    builder.add_node("finish", finish)

    builder.set_entry_point("plan")
    builder.add_edge("plan", "act")
    builder.add_conditional_edges(
        "act",
        route,
        {"tools": "tools", "finish": "finish", "out_of_turns": "out_of_turns"},
    )
    builder.add_edge("tools", "act")
    builder.add_edge("out_of_turns", "finish")
    builder.add_edge("finish", END)
    return builder.compile()


graph = build_graph()
