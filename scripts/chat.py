"""Chat with Jarvis in the terminal.

Run from the repo root (n8n and the MCP server must be running):
    python -m scripts.chat              # default profile
    python -m scripts.chat free-flash   # another profile from config/models.yaml
"""
import asyncio
import sys

from agents.factory import message_text
from agents.graph import build_jarvis, run_config
from guardrails.approval import Decision, approver_var, describe_action
from guardrails.budget import over_budget
from tracking.cost import CostTracker


async def terminal_approver(tool_name: str, args: dict) -> Decision:
    print("\n  APPROVAL NEEDED:\n  " + describe_action(tool_name, args).replace("\n", "\n  "))
    answer = (await asyncio.to_thread(
        input, "  Approve? [y] yes / [n] no / or type what to change: ")).strip()
    if answer.lower() in {"y", "yes", "s", "si", "sí"}:
        return Decision(approved=True)
    if answer.lower() in {"", "n", "no"}:
        return Decision(approved=False)
    return Decision(approved=False, feedback=answer)


async def main(profile: str | None) -> None:
    jarvis, specialists, name, cfg = await build_jarvis(profile)
    print(f"Profile: {name} (orchestrator {cfg['orchestrator']}, specialists {cfg['specialists']})")
    print("Specialists:", {n: spec["tools"] for n, spec in specialists.items()})
    print("Jarvis is ready. Type 'exit' to quit.")

    tracker = CostTracker(session_id="terminal", profile=name)
    config = run_config("terminal", callbacks=[tracker])
    approver_var.set(terminal_approver)

    while True:
        try:
            text = (await asyncio.to_thread(input, "\nYou: ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            break
        if limit := over_budget(tracker):
            print(limit)
            break

        tracker.start_message()
        result = await jarvis.ainvoke({"messages": [{"role": "user", "content": text}]}, config)

        for note in (result.get("input_notes") or []) + (result.get("output_notes") or []):
            print(f"  [guard] {note}")
        if result.get("blocked_reason"):
            print(f"  [guard] blocked: {result['blocked_reason']}")
        messages = result["messages"]
        last_user = max(i for i, m in enumerate(messages) if m.type == "human")
        for m in messages[last_user + 1:]:
            for call in getattr(m, "tool_calls", None) or []:
                print(f"  -> {call['name']}: {call['args'].get('request', call['args'])}")

        print(f"\nJarvis: {message_text(messages[-1])}\n")
        print(CostTracker.table(tracker.message_by_agent()))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
