"""Phase 1 check: Calendar agent (built from agents.yaml) -> MCP -> n8n -> Open-Meteo.

Run from the repo root (n8n and the MCP server must be running):
    python -m scripts.test_pipeline "Will it rain in Berlin on Saturday?"

Only weather_get exists in Phase 1, so the factory warns that the calendar,
email and task tools are missing. That's expected.
"""
import asyncio
import sys

from agents.factory import build_specialists, load_profiles, make_llm, make_mcp_client


async def main(question: str) -> None:
    tools = await make_mcp_client().get_tools()
    print("Tools loaded from MCP:", [t.name for t in tools])

    profiles, default = load_profiles()
    profile = profiles[default]
    model = profile["specialists"]
    specialists = build_specialists(tools, make_llm(model, profile))
    for name, spec in specialists.items():
        print(f"Agent ready: {name} -> {spec['tools']}")

    calendar = specialists["calendar"]["agent"]
    result = await calendar.ainvoke({"messages": [{"role": "user", "content": question}]})

    for message in result["messages"]:
        message.pretty_print()

    # Preview of Phase 3: tokens used by this run
    inp = sum((getattr(m, "usage_metadata", None) or {}).get("input_tokens", 0) for m in result["messages"])
    out = sum((getattr(m, "usage_metadata", None) or {}).get("output_tokens", 0) for m in result["messages"])
    print(f"\nTokens used: {inp} input, {out} output ({model})")


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Will it rain in Berlin on Saturday?"
    asyncio.run(main(q))
