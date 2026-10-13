"""Approval: risky tools ask the user before they run.

The UI (Chainlit or terminal) registers an "approver" function for the current request.
Wrapped tools call it and wait. With no approver (e.g. a scheduled run), they refuse.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable

from langchain_core.tools import BaseTool, StructuredTool


@dataclass
class Decision:
    approved: bool
    feedback: str = ""


Approver = Callable[[str, dict], Awaitable[Decision]]
approver_var: ContextVar[Approver | None] = ContextVar("approver", default=None)


def describe_action(tool_name: str, args: dict) -> str:
    """Human-readable summary of what the tool is about to do (markdown)."""
    if tool_name == "email_send":
        return (f"**Send email**\n\n**To:** {args.get('to')}\n\n**Subject:** {args.get('subject')}\n\n"
                f"{args.get('body', '')}")
    if tool_name == "calendar_create_event":
        extra = f"\n\n**Attendees:** {args['attendees']}" if args.get("attendees") else ""
        return (f"**Create event:** {args.get('title')}\n\n"
                f"**From** {args.get('start')} **to** {args.get('end')}{extra}")
    if tool_name == "calendar_update_event":
        return (f"**Move event** `{args.get('event_id')}`\n\n"
                f"**New time:** {args.get('start')} to {args.get('end')}")
    lines = "\n".join(f"- **{k}:** {v}" for k, v in args.items())
    return f"**{tool_name}**\n\n{lines}"


def with_approval(tool: BaseTool) -> BaseTool:
    """Return a copy of the tool that asks the user before running."""

    async def run(**kwargs):
        approver = approver_var.get()
        if approver is None:
            return ("Not done: this action needs the user's approval and nobody is available "
                    "to approve it. Tell the user what you would have done.")
        decision = await approver(tool.name, kwargs)
        if not decision.approved:
            feedback = decision.feedback.strip()
            return ("Not done: the user REJECTED this action. "
                    + (f"Their feedback: {feedback}. Adjust and try again only if the feedback asks for it."
                       if feedback else "Do not retry it; tell the user it was cancelled."))
        return await tool.ainvoke(kwargs)

    return StructuredTool.from_function(
        coroutine=run, name=tool.name, args_schema=tool.args_schema,
        description=tool.description + " (The user is asked to approve this before it runs.)")
