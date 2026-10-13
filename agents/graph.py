"""Builds Jarvis: guards around an orchestrator that uses the specialists as tools.

    START -> input_guard -> orchestrator -> output_guard -> END
                  \\-> refusal -> END          (when the input guard blocks)
"""
import asyncio

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from agents.factory import (as_tool, build_specialists, load_profiles, make_llm,
                            make_mcp_client, render_prompt)
from guardrails.clef import ClefClient
from guardrails.config import GUARDRAILS
from guardrails.input_guard import make_input_guard, refusal
from guardrails.output_guard import output_guard

# Pass this in the run config so the cost tracker attributes the orchestrator's tokens
ORCHESTRATOR_TAGS = ["agent:orchestrator"]

_background_tasks: set = set()  # keeps the Clef warm-up task alive until it finishes


def make_classifiers(specialist_llm):
    """Return (clef client or None, LLM classifier or None) from config/guardrails.yaml."""
    guard = GUARDRAILS["input_guard"]
    mode, settings = guard["classifier"], guard["clef"]
    if mode == "none":
        return None, None
    if mode == "llm":
        return None, specialist_llm

    clef = ClefClient(model=settings["model"], timeout=float(settings["timeout_seconds"]))
    if not clef.configured:
        print("[guard] Clef is not configured (CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_AUTH_TOKEN): "
              f"using {'the LLM classifier' if settings['fallback'] == 'llm' else 'regex rules only'}")
        clef = None
    else:
        task = asyncio.create_task(clef.warm_up())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    fallback = specialist_llm if settings["fallback"] == "llm" else None
    return clef, fallback


class JarvisState(MessagesState):
    blocked_reason: str | None   # set by input_guard when the request is blocked
    input_notes: list[str]       # what input_guard did this turn (e.g. masked an IBAN)
    output_notes: list[str]      # what output_guard did this turn (e.g. redacted a key)


def run_config(thread_id: str, callbacks: list | None = None) -> dict:
    """The config every run of the graph uses."""
    return {"configurable": {"thread_id": thread_id},
            "tags": ORCHESTRATOR_TAGS,
            "callbacks": callbacks or [],
            "recursion_limit": GUARDRAILS["recursion_limit"]}


async def build_jarvis(profile: str | None = None, checkpointer=None):
    """Return (graph, specialists, profile name, profile config)."""
    profiles, default = load_profiles()
    name = profile or default
    cfg = profiles[name]

    tools = await make_mcp_client().get_tools()
    specialist_llm = make_llm(cfg["specialists"], cfg)
    specialists = build_specialists(tools, specialist_llm)
    if not specialists:
        raise RuntimeError("No specialist agents could be created. Is the MCP server running?")

    agent_tools = [as_tool(n, spec) for n, spec in specialists.items()]
    agents_list = "\n".join(f"- {t.name}: {spec['description']}"
                            for t, spec in zip(agent_tools, specialists.values()))

    orchestrator = create_agent(
        make_llm(cfg["orchestrator"], cfg),
        tools=agent_tools,
        system_prompt=render_prompt("agents/prompts/orchestrator.md", agents=agents_list),
    )

    clef, llm_classifier = make_classifiers(specialist_llm)

    builder = StateGraph(JarvisState)
    builder.add_node("input_guard", make_input_guard(llm_classifier, clef, GUARDRAILS["input_guard"]["clef"]))
    builder.add_node("refusal", refusal)
    builder.add_node("orchestrator", orchestrator)
    builder.add_node("output_guard", output_guard)
    builder.add_edge(START, "input_guard")
    builder.add_conditional_edges("input_guard",
                                  lambda s: "refusal" if s.get("blocked_reason") else "orchestrator",
                                  ["refusal", "orchestrator"])
    builder.add_edge("refusal", END)
    builder.add_edge("orchestrator", "output_guard")
    builder.add_edge("output_guard", END)

    # the checkpointer remembers the conversation per thread_id
    graph = builder.compile(checkpointer=checkpointer or InMemorySaver())
    return graph, specialists, name, cfg
