"""Jarvis web UI (Chainlit).

Run from the repo root (n8n and the MCP server must be running):
    chainlit run app.py
"""
import json
from typing import Any

import chainlit as cl
from chainlit.input_widget import Select
from chainlit.utils import utc_now

from agents.factory import load_profiles, message_text
from agents.graph import build_jarvis, run_config
from guardrails.approval import Decision, approver_var, describe_action
from guardrails.budget import over_budget
from tracking.cost import CostTracker

STARTERS = [
    ("This week", "What do I have on my calendar this week?"),
    ("Inbox", "Summarize my unread emails"),
    ("Run on Saturday", "Find a free slot on Saturday morning for a run in Berlin and check if it will rain"),
    ("Prepare Monday", "Check my calendar for Monday and add a task to prepare for each event"),
]
GUARD_NODES = {"input_guard", "output_guard"}


@cl.set_starters
async def set_starters():
    return [cl.Starter(label=label, message=message) for label, message in STARTERS]


# ---------------------------------------------------------------- session setup

async def load_jarvis(profile: str) -> bool:
    """Build Jarvis for a model profile and store it in the session. Returns success."""
    try:
        jarvis, specialists, name, cfg = await build_jarvis(profile)
    except Exception as e:  # MCP server down, missing package or API key...
        await cl.Message(content=f"Could not start Jarvis with profile **{profile}**: `{e}`").send()
        return False

    runs = (cl.user_session.get("runs") or 0) + 1  # a new conversation per profile switch
    cl.user_session.set("runs", runs)
    cl.user_session.set("jarvis", jarvis)
    cl.user_session.set("profile", name)
    cl.user_session.set("thread_id", f"{cl.context.session.id}-{runs}")
    cl.user_session.get("tracker").profile = name
    cl.user_session.set("models", f"orchestrator `{cfg['orchestrator']}`, specialists `{cfg['specialists']}`")
    return True


@cl.on_chat_start
async def on_chat_start():
    profiles, default = load_profiles()
    await cl.ChatSettings([
        Select(id="profile", label="Model profile",
               items={f"{cfg.get('label', name)} ({name})": name for name, cfg in profiles.items()},
               initial_value=default),
    ]).send()
    cl.user_session.set("tracker", CostTracker(session_id=cl.context.session.id))
    await load_jarvis(default)
    await update_cost_panel()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    profile = settings["profile"]
    if profile != cl.user_session.get("profile") and await load_jarvis(profile):
        await cl.Message(
            content=f"Switched to profile **{profile}**: {cl.user_session.get('models')}. "
                    "A new conversation starts here.").send()
        await update_cost_panel()


# ---------------------------------------------------------------- approval (human in the loop)

async def chainlit_approver(tool_name: str, args: dict) -> Decision:
    """Shown when an agent wants to run a tool listed in approval_required."""
    res = await cl.AskActionMessage(
        content=f"🛑 **Approval needed.** Jarvis wants to do this:\n\n{describe_action(tool_name, args)}",
        actions=[
            cl.Action(name="approve", payload={"choice": "approve"}, label="✅ Approve"),
            cl.Action(name="change", payload={"choice": "change"}, label="✏️ Request changes"),
            cl.Action(name="reject", payload={"choice": "reject"}, label="❌ Reject"),
        ],
        timeout=300,
    ).send()
    choice = ((res or {}).get("payload") or {}).get("choice", "reject")
    if choice == "approve":
        return Decision(approved=True)
    if choice == "change":
        answer = await cl.AskUserMessage(content="What should I change?", timeout=300).send()
        return Decision(approved=False, feedback=(answer or {}).get("output", ""))
    return Decision(approved=False)


# ---------------------------------------------------------------- display helpers

def as_text(value: Any) -> str:
    """Readable text for a tool's input or output in a step."""
    if hasattr(value, "content"):  # ToolMessage
        value = value.content
    if isinstance(value, list):  # MCP content blocks
        value = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def guard_summary(node: str, output: Any) -> str:
    output = output if isinstance(output, dict) else {}
    notes = output.get("input_notes" if node == "input_guard" else "output_notes") or []
    if node == "input_guard":
        status = f"⛔ Blocked: {output['blocked_reason']}" if output.get("blocked_reason") else "✅ Allowed"
    else:
        status = "⚠️ Answer redacted" if notes else "✅ No secrets found"
    return status + "".join(f"\n- {n}" for n in notes)


async def update_cost_panel() -> None:
    tracker: CostTracker = cl.user_session.get("tracker")
    session = tracker.session_by_agent()
    content = (f"**Profile:** {cl.user_session.get('profile')}\n\n"
               f"{cl.user_session.get('models')}\n\n"
               f"### This session\n\n{CostTracker.table(session) if session else '_No calls yet._'}")
    await cl.ElementSidebar.set_title("Cost tracker")
    await cl.ElementSidebar.set_elements([cl.Text(name="Session cost", content=content)])


# ---------------------------------------------------------------- chat

@cl.on_message
async def on_message(message: cl.Message):
    jarvis = cl.user_session.get("jarvis")
    if jarvis is None:
        await cl.Message(content="Jarvis is not running. Start the MCP server and reload the page.").send()
        return

    tracker: CostTracker = cl.user_session.get("tracker")
    if limit := over_budget(tracker):  # budget guardrail
        await cl.Message(content=f"💸 {limit} Start a new chat to continue.").send()
        return

    tracker.start_message()
    config = run_config(cl.user_session.get("thread_id"), callbacks=[tracker])
    steps: dict[str, cl.Step] = {}
    token = approver_var.set(chainlit_approver)

    try:
        async for event in jarvis.astream_events(
                {"messages": [{"role": "user", "content": message.content}]}, config, version="v2"):
            kind, name = event["event"], event["name"]

            if kind == "on_chain_start" and name in GUARD_NODES:
                step = cl.Step(name=name.replace("_", " "), type="run")
                await step.send()
                steps[event["run_id"]] = step

            elif kind == "on_tool_start":
                # nest MCP tool calls inside the specialist step that triggered them
                parent = next((steps[p] for p in reversed(event.get("parent_ids", [])) if p in steps), None)
                step = cl.Step(name=name, type="tool", parent_id=parent.id if parent else None)
                data = event["data"].get("input") or {}
                step.input = as_text(data.get("request", data) if isinstance(data, dict) else data)
                await step.send()
                steps[event["run_id"]] = step

            elif kind in ("on_tool_end", "on_chain_end") and event["run_id"] in steps:
                step = steps.pop(event["run_id"])
                output = event["data"].get("output")
                step.output = guard_summary(name, output) if name in GUARD_NODES else as_text(output)
                step.end = utc_now()
                await step.update()

    except Exception as e:
        await cl.Message(content=f"Something went wrong: `{type(e).__name__}: {e}`").send()
        return
    finally:
        approver_var.reset(token)

    state = await jarvis.aget_state({"configurable": config["configurable"]})
    await cl.Message(content=message_text(state.values["messages"][-1])).send()

    rows = tracker.message_by_agent()
    note = ""
    if tracker.unpriced_models():
        note = f"\n\n_No price in config/pricing.yaml for: {', '.join(tracker.unpriced_models())}_"
    await cl.Message(author="Cost tracker",
                     content=f"**This message**\n\n{CostTracker.table(rows)}{note}").send()
    await update_cost_panel()
