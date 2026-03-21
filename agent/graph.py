"""
LangGraph-based research agent.

Graph topology:
  agent ──(has tool calls)──► tools ──► agent
        ──(RESEARCH_COMPLETE)──► store ──► END
        ──(max iterations)──► store ──► END
"""

import json
import logging
import uuid
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent.config import settings
from agent.goals import load_goals
from agent.memory.db import ResearchLog, get_session
from agent.memory.vector import add_document, search
from agent.tools.mcp_client import call_mcp_tool, tools_summary
from agent.tools.pdf_reader import read_pdf
from agent.tools.web_search import web_search

log = logging.getLogger("sisyphus.graph")

TOOLS = [web_search, read_pdf, call_mcp_tool]
MAX_ITERATIONS = 8  # guard against infinite loops


# ── State ──────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    goal: dict
    iteration: int


# ── Prompt ────────────────────────────────────────────────────────────────────

def _build_system_prompt(goal: dict) -> str:
    all_goals = load_goals()
    goals_text = "\n".join(
        f"  [{g['priority']}] {g['title']}: {g['description']}" for g in all_goals
    ) or "  (none yet)"

    memories = search(f"{goal['title']} {goal.get('description', '')}", n_results=4)
    memory_text = (
        "\n".join(f"  • {m['text'][:250]}" for m in memories)
        if memories
        else "  (no relevant past research yet)"
    )

    mcp_text = tools_summary()

    return f"""You are Sisyphus, a persistent autonomous research agent.
Your purpose is to investigate your assigned goal thoroughly and produce a useful research summary.

ALL ACTIVE GOALS (for context):
{goals_text}

RELEVANT PAST RESEARCH (from memory):
{memory_text}

{mcp_text}

CURRENT GOAL TO RESEARCH NOW:
Title: {goal['title']}
Description: {goal.get('description', '(no description)')}

INSTRUCTIONS:
- Use web_search, read_pdf, and call_mcp_tool tools as needed.
- Prefer multiple focused searches over one broad one.
- After researching, write a clear, structured summary of your findings.
- If you are unsure about something important and need the user's input,
  include exactly one line: FEEDBACK_NEEDED: <your specific question>
- When you have finished, end your final message with the exact token: RESEARCH_COMPLETE
- Do NOT include RESEARCH_COMPLETE until you are done with all tool calls."""


# ── Nodes ─────────────────────────────────────────────────────────────────────

def agent_node(state: AgentState) -> dict:
    llm = ChatOllama(
        base_url=settings.ollama_host,
        model=settings.ollama_model,
        temperature=0.3,
        num_predict=2048,
    ).bind_tools(TOOLS)

    system = _build_system_prompt(state["goal"])
    messages = [SystemMessage(content=system)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response], "iteration": state["iteration"] + 1}


def store_node(state: AgentState) -> dict:
    last = state["messages"][-1]
    summary = last.content if isinstance(last.content, str) else str(last.content)
    summary = summary.replace("RESEARCH_COMPLETE", "").strip()
    goal = state["goal"]

    doc_id = str(uuid.uuid4())
    add_document(
        doc_id,
        summary,
        metadata={
            "goal_id": str(goal.get("id", 0)),
            "goal_title": goal["title"],
        },
    )

    with get_session() as session:
        session.add(
            ResearchLog(
                goal_id=goal.get("id"),
                summary=summary,
                sources=json.dumps([]),
            )
        )
        session.commit()

    log.info(f"Research stored for goal: {goal['title']} ({len(summary)} chars)")
    return {}


# ── Routing ───────────────────────────────────────────────────────────────────

def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]

    # Hit iteration limit → force store
    if state["iteration"] >= MAX_ITERATIONS:
        log.warning(f"Max iterations reached for goal: {state['goal']['title']}")
        return "store"

    # LLM wants to call tools
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"

    # LLM says it is done
    content = last.content if isinstance(last.content, str) else ""
    if "RESEARCH_COMPLETE" in content:
        return "store"

    # Otherwise keep going
    return "agent"


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_node("store", store_node)

    g.set_entry_point("agent")
    g.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "agent": "agent", "store": "store"},
    )
    g.add_edge("tools", "agent")
    g.add_edge("store", END)

    return g.compile()


# ── Public entry point ────────────────────────────────────────────────────────

def run_research_cycle(goal: dict) -> tuple[str, bool]:
    """
    Run one full research cycle for the given goal.

    Returns:
        (summary: str, needs_feedback: bool)
    """
    import re

    graph_app = build_graph()
    initial_msg = HumanMessage(
        content=f"Research goal: {goal['title']}\n{goal.get('description', '')}"
    )
    result = graph_app.invoke(
        {"messages": [initial_msg], "goal": goal, "iteration": 0}
    )

    last = result["messages"][-1]
    summary = last.content if isinstance(last.content, str) else str(last.content)
    summary = summary.replace("RESEARCH_COMPLETE", "").strip()

    needs_feedback = bool(re.search(r"FEEDBACK_NEEDED:", summary))
    return summary, needs_feedback
