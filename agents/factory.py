"""Builds the specialist agents from config/agents.yaml.

Adding an agent = a prompt file + an entry in agents.yaml. No code changes here.
"""
import os
from datetime import date
from typing import Any

import yaml
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from config.settings import MODEL_PROFILE, ROOT
from guardrails.approval import with_approval
from guardrails.config import GUARDRAILS


def load_yaml(relative_path: str) -> dict:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    return yaml.safe_load(os.path.expandvars(text)) or {}


def render_prompt(relative_path: str, **values: str) -> str:
    """Read a prompt file and fill {today} plus any extra {placeholders}."""
    prompt = (ROOT / relative_path).read_text(encoding="utf-8")
    values.setdefault("today", date.today().strftime("%A %Y-%m-%d"))
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt


def load_profiles() -> tuple[dict, str]:
    """All model profiles from config/models.yaml, and the default profile name."""
    cfg = load_yaml("config/models.yaml")
    profiles = cfg.get("profiles") or {}
    default = MODEL_PROFILE or cfg.get("default_profile") or next(iter(profiles))
    if default not in profiles:
        raise ValueError(f"Model profile '{default}' not found in config/models.yaml")
    return profiles, default


def make_llm(model: str, profile: dict | None = None):
    """Chat model with a timeout, so quota or network problems fail fast.

    A profile may set base_url (e.g. an OpenAI-compatible proxy) and api_key_env
    (the name of the .env variable holding its key).
    """
    profile = profile or {}
    extra = {"stream_usage": True} if model.startswith("openai:") else {}  # token counts when streaming
    if profile.get("base_url"):
        extra["base_url"] = profile["base_url"]
    if key_env := profile.get("api_key_env"):
        key = os.getenv(key_env)
        if not key:
            raise ValueError(f"Missing {key_env} in .env (needed by this model profile)")
        extra["api_key"] = key
    return init_chat_model(model, max_retries=2, timeout=60, **extra)


def make_mcp_client() -> MultiServerMCPClient:
    """One client for every MCP server listed in config/mcp_servers.yaml."""
    return MultiServerMCPClient(load_yaml("config/mcp_servers.yaml"))


def message_text(message: Any) -> str:
    """Plain text of a chat message (some providers return a list of content blocks)."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def build_specialists(all_tools: list[BaseTool], llm) -> dict[str, dict]:
    """Create one agent per entry in agents.yaml, each with only its own tools."""
    needs_approval = set(GUARDRAILS["approval_required"])
    by_name = {t.name: (with_approval(t) if t.name in needs_approval else t) for t in all_tools}
    specialists: dict[str, dict] = {}

    for name, cfg in load_yaml("config/agents.yaml").items():
        wanted = cfg.get("tools", [])
        tools = [by_name[t] for t in wanted if t in by_name]
        missing = [t for t in wanted if t not in by_name]
        if missing:
            print(f"[factory] {name}: tools not available, skipped: {missing}")
        if not tools:
            print(f"[factory] {name}: no tools available, agent not created")
            continue

        agent = create_agent(llm, tools=tools, system_prompt=render_prompt(cfg["prompt"]))
        specialists[name] = {
            # the tag lets the cost tracker (Phase 3) know which agent spent the tokens
            "agent": agent.with_config({"tags": [f"agent:{name}"]}),
            "description": " ".join(cfg["description"].split()),
            "tools": [t.name for t in tools],
        }
    return specialists


def as_tool(name: str, spec: dict) -> StructuredTool:
    """Wrap a specialist so the orchestrator can call it like any other tool."""
    agent = spec["agent"]

    async def run(request: str) -> str:
        result = await agent.ainvoke({"messages": [{"role": "user", "content": request}]})
        return message_text(result["messages"][-1])

    return StructuredTool.from_function(
        coroutine=run,
        name=f"{name}_agent",
        description=spec["description"]
        + " Send one complete, self-contained request including any dates, names and details.",
    )
