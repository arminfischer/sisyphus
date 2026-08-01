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
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent.config import settings
from agent.goals import load_goals
from agent.memory.db import ResearchLog, get_session
from agent.memory.vector import add_document, search
from agent.tools.pdf_reader import read_pdf
from agent.tools.web_search import web_search

log = logging.getLogger("sisyphus.graph")

TOOLS = [web_search, read_pdf]
MAX_ITERATIONS = 8  # guard against infinite loops


# ── State ──────────────────────────────────────────────────────────────────────


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    goal: dict
    iteration: int
    critique_rounds: int


# ── Prompt ────────────────────────────────────────────────────────────────────


def _build_system_prompt(goal: dict) -> str:
    all_goals = load_goals()
    goals_text = (
        "\n".join(
            f"  [{g['priority']}] {g['title']}: {g['description']}\n"
            f"    rationale: {g.get('rationale', '')}"
            for g in all_goals
        )
        or "  (none yet)"
    )

    memories = search(f"{goal['title']} {goal.get('description', '')}", n_results=4)
    memory_text = (
        "\n".join(f"  • {m['text'][:250]}" for m in memories)
        if memories
        else "  (no relevant past research yet)"
    )

    return f"""You are Sisyphus, a persistent autonomous research agent.
Your purpose is to investigate your assigned goal thoroughly and produce a useful research summary.

TODAY'S DATE: {datetime.now(timezone.utc).strftime("%Y-%m-%d")} (UTC)
Always search for the most recent information.

ALL ACTIVE GOALS (for context):
{goals_text}

RELEVANT PAST RESEARCH (from memory):
{memory_text}

CURRENT GOAL TO RESEARCH NOW:
Title: {goal['title']}
Description: {goal.get('description', '(no description)')}
Rationale: {goal.get('rationale', '(no rationale given)')}

INSTRUCTIONS:
- Use web_search and read_pdf tools as needed.
- Prefer multiple focused searches over one broad one.
- After researching, write a clear, structured summary of your findings.
- Be rigorous and self-critical: do NOT stop early. Before signalling
  completion, ask yourself whether your findings actually answer the goal
  and its rationale. If there are obvious gaps, contradictions, or
  unverified claims, do more research before finishing.
- If you are unsure about something important and need the user's input,
  include exactly one line: FEEDBACK_NEEDED: <your specific question>
- When you are genuinely done, end your final message with the exact token: RESEARCH_COMPLETE
- Do NOT include RESEARCH_COMPLETE until you are done with all tool calls."""


# ── Nodes ─────────────────────────────────────────────────────────────────────


def agent_node(state: AgentState) -> dict:
    iteration = state["iteration"] + 1
    goal = state["goal"]
    log.info(
        f"[{goal['title']}] LLM thinking... (iteration {iteration}/{MAX_ITERATIONS})"
    )

    llm = ChatOpenAI(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=0.3,
        max_tokens=2048,
    ).bind_tools(TOOLS)

    system = _build_system_prompt(state["goal"])
    messages = [SystemMessage(content=system)] + state["messages"]
    response = llm.invoke(messages)

    if isinstance(response, AIMessage) and response.tool_calls:
        for tc in response.tool_calls:
            args_preview = str(tc.get("args", {}))[:120]
            log.info(f"[{goal['title']}] Tool call → {tc['name']}({args_preview})")
    elif isinstance(response.content, str) and "RESEARCH_COMPLETE" in response.content:
        log.info(f"[{goal['title']}] Agent finished research.")

    return {"messages": [response], "iteration": iteration}


CRITIQUE_MAX_ROUNDS = 2  # how many times the agent can be sent back to improve


def _collect_summary(state: AgentState) -> str:
    """Collect all substantive AI text across the conversation."""
    parts = []
    for msg in state["messages"]:
        if not isinstance(msg, AIMessage):
            continue
        if msg.tool_calls:  # skip pure tool-call frames
            continue
        text = msg.content if isinstance(msg.content, str) else ""
        text = text.replace("RESEARCH_COMPLETE", "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def critique_node(state: AgentState) -> dict:
    """
    Self-critique step: an independent LLM call reviews the agent's research
    summary against the goal and its rationale. If the critique finds the
    research insufficient (gaps, unverified claims, not addressing the
    rationale), it injects a HumanMessage demanding more work and routes back
    to the agent. Otherwise routes to store.

    This prevents the agent from stopping early with shallow results.
    """
    goal = state["goal"]
    summary = _collect_summary(state)
    critique_rounds = state.get("critique_rounds", 0)

    # If we've already hit the critique round limit, accept and store.
    if critique_rounds >= CRITIQUE_MAX_ROUNDS:
        log.info(
            f"[{goal['title']}] Critique round limit reached ({critique_rounds}); accepting."
        )
        return {"critique_rounds": critique_rounds}

    llm = ChatOpenAI(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        temperature=0.2,
        max_tokens=512,
    )

    prompt = f"""You are a strict, critical reviewer of autonomous research.
Your job is to decide whether the research summary below is sufficient, or
whether the agent must do MORE work before it is allowed to stop.

Goal:
  Title: {goal['title']}
  Description: {goal.get('description', '')}
  Rationale: {goal.get('rationale', '')}

Research summary produced by the agent:
---
{summary}
---

Evaluate critically:
1. Does the summary actually address the goal AND its rationale?
2. Are claims backed by sources or are there unverified assertions?
3. Are there obvious gaps, missing angles, or unanswered sub-questions?
4. Is the depth appropriate, or is it superficial / hand-wavy?

Respond with ONLY a JSON object (no markdown, no prose):
{{
  "verdict": "sufficient" | "insufficient",
  "gaps": ["specific gap 1", "specific gap 2"],
  "guidance": "If insufficient, a direct instruction to the agent on what to do next."
}}
Be demanding. Only return "sufficient" if the summary genuinely answers the
goal and rationale with adequate depth and sourcing."""

    response = llm.invoke(prompt)
    raw = response.content if isinstance(response.content, str) else str(response.content)

    import re as _re
    import json as _json

    cleaned = _re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        data = _json.loads(cleaned)
    except Exception:
        log.warning(f"[{goal['title']}] Could not parse critique response; accepting.")
        return {"critique_rounds": critique_rounds + 1}

    verdict = data.get("verdict", "sufficient").lower()
    if verdict == "insufficient":
        gaps = data.get("gaps", [])
        guidance = data.get("guidance", "Do more research to address the gaps.")
        gaps_text = "\n".join(f"  - {g}" for g in gaps) if gaps else ""
        log.info(
            f"[{goal['title']}] Critique: INSUFFICIENT (round {critique_rounds + 1}). "
            f"Gaps:\n{gaps_text}"
        )
        critique_msg = HumanMessage(
            content=(
                "Your research was reviewed and found INSUFFICIENT. You must NOT "
                "stop yet. Address the following gaps and do additional research, "
                "then produce an improved summary.\n\n"
                f"Guidance: {guidance}\n"
                + (f"Specific gaps:\n{gaps_text}" if gaps_text else "")
                + "\n\nWhen genuinely done, end with RESEARCH_COMPLETE."
            )
        )
        return {
            "messages": [critique_msg],
            "critique_rounds": critique_rounds + 1,
        }

    log.info(f"[{goal['title']}] Critique: SUFFICIENT (round {critique_rounds + 1}).")
    return {"critique_rounds": critique_rounds + 1}


def store_node(state: AgentState) -> dict:
    summary = _collect_summary(state)
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

    log.info(
        f"[{goal['title']}] Research stored ({len(summary)} chars). View at http://localhost:{settings.dashboard_port}/log"
    )
    log.info(f"[{goal['title']}] Summary preview: {summary[:300].strip()}...")
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

    # LLM says it is done → send to critique first
    content = last.content if isinstance(last.content, str) else ""
    if "RESEARCH_COMPLETE" in content:
        return "critique"

    # Otherwise keep going
    return "agent"


# ── Graph assembly ─────────────────────────────────────────────────────────────


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_node("critique", critique_node)
    g.add_node("store", store_node)

    g.set_entry_point("agent")
    g.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "agent": "agent", "critique": "critique", "store": "store"},
    )
    g.add_edge("tools", "agent")

    # After critique: if the agent was sent back to do more work, the critique
    # node injects a HumanMessage and we route back to "agent". If the critique
    # accepted the research (or hit the round limit), route to "store".
    g.add_conditional_edges(
        "critique",
        lambda state: "agent"
        if any(
            isinstance(m, HumanMessage)
            and "INSUFFICIENT" in (m.content if isinstance(m.content, str) else "")
            for m in state.get("messages", [])[-1:]
        )
        else "store",
        {"agent": "agent", "store": "store"},
    )
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
        {
            "messages": [initial_msg],
            "goal": goal,
            "iteration": 0,
            "critique_rounds": 0,
        }
    )

    last = result["messages"][-1]
    summary = last.content if isinstance(last.content, str) else str(last.content)
    summary = summary.replace("RESEARCH_COMPLETE", "").strip()

    needs_feedback = bool(re.search(r"FEEDBACK_NEEDED:", summary))
    return summary, needs_feedback
