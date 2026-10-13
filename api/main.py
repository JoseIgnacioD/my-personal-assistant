"""Jarvis HTTP API: lets n8n (the evaluation workflow) send prompts to Jarvis.

Run from the repo root (the MCP server must be running):
    python -m api.main

POST /eval runs one prompt through the full graph (guards, orchestrator, specialists)
and reports what happened. Actions that need approval are recorded and automatically
REJECTED, so an evaluation never sends emails or changes the calendar.
"""
import asyncio
import time
import uuid
from datetime import date
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from agents.factory import load_profiles, message_text
from agents.graph import build_jarvis, run_config
from config.settings import API_HOST, API_PORT, N8N_SECRET
from guardrails.approval import Decision, approver_var
from tracking.cost import CostTracker

app = FastAPI(title="Jarvis API")
_graphs: dict[str, Any] = {}
_lock = asyncio.Lock()


class EvalRequest(BaseModel):
    prompt: str
    profile: str = ""        # empty = default profile
    case: dict | None = None  # echoed back, so n8n can score the answer


async def get_graph(profile: str):
    """Build Jarvis once per profile and reuse it."""
    async with _lock:
        if profile not in _graphs:
            graph, _specialists, _name, _cfg = await build_jarvis(profile)
            _graphs[profile] = graph
        return _graphs[profile]


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/eval")
async def evaluate(req: EvalRequest, x_jarvis_secret: str | None = Header(default=None)):
    if N8N_SECRET and x_jarvis_secret != N8N_SECRET:
        raise HTTPException(status_code=401, detail="Wrong or missing X-Jarvis-Secret header")

    profiles, default = load_profiles()
    profile = req.profile or default
    if profile not in profiles:
        raise HTTPException(status_code=400, detail=f"Unknown profile '{profile}'")

    graph = await get_graph(profile)
    approvals: list[str] = []

    async def record_and_reject(tool_name: str, args: dict) -> Decision:
        approvals.append(tool_name)
        return Decision(approved=False, feedback="")  # evaluation: never perform actions

    tracker = CostTracker(session_id=f"eval-{date.today().isoformat()}", profile=profile)
    token = approver_var.set(record_and_reject)
    started = time.perf_counter()
    try:
        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": req.prompt}]},
            run_config(f"eval-{uuid.uuid4()}", callbacks=[tracker]))  # fresh conversation per prompt
    except Exception as e:
        print(f"[eval ERROR] {req.prompt[:60]!r}: {type(e).__name__}: {e}")
        return {"case": req.case, "profile": profile, "error": f"{type(e).__name__}: {e}"}
    finally:
        approver_var.reset(token)
    latency = time.perf_counter() - started

    # which specialists did the orchestrator call? (its tool calls are named "<agent>_agent")
    agents = []
    for m in result["messages"]:
        for call in getattr(m, "tool_calls", None) or []:
            name = call["name"].removesuffix("_agent")
            if name not in agents:
                agents.append(name)

    usage = tracker.message_by_agent()
    return {
        "case": req.case,
        "profile": profile,
        "answer": message_text(result["messages"][-1]),
        "blocked": bool(result.get("blocked_reason")),
        "blocked_reason": result.get("blocked_reason"),
        "guard_notes": result.get("input_notes") or [],
        "agents_called": agents,
        "approvals_requested": approvals,
        "latency_s": round(latency, 2),
        "tokens": sum(r["input"] + r["output"] for r in usage.values()),
        "cost": round(sum(r["cost"] for r in usage.values()), 6),
        "by_agent": usage,
    }


if __name__ == "__main__":
    print(f"Jarvis API on http://{API_HOST}:{API_PORT} (POST /eval)")
    uvicorn.run(app, host=API_HOST, port=API_PORT)
