"""Token and cost tracking: every LLM call is recorded per agent, priced, and stored in SQLite.

The agent is identified from run tags: specialists are tagged "agent:<name>" in
agents/factory.py, the orchestrator run gets "agent:orchestrator" in its config.
"""
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import yaml
from langchain_core.callbacks import BaseCallbackHandler

from config.settings import DATA_DIR, ROOT

DB_PATH = DATA_DIR / "jarvis.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    profile       TEXT,
    agent         TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost          REAL NOT NULL
)
"""


def _load_pricing() -> tuple[str, dict]:
    cfg = yaml.safe_load((ROOT / "config/pricing.yaml").read_text(encoding="utf-8")) or {}
    return cfg.get("currency", "EUR"), cfg.get("models") or {}


CURRENCY, PRICES = _load_pricing()


def normalize_model(name: str) -> str:
    """'models/gemini-x' or 'google_genai:gemini-x' -> 'gemini-x'."""
    name = (name or "unknown").split(":")[-1]
    return name.removeprefix("models/")


def find_price(model: str) -> dict | None:
    """Longest configured name that the model name starts with."""
    matches = [k for k in PRICES if model.startswith(k)]
    return PRICES[max(matches, key=len)] if matches else None


def agent_from_tags(tags: list[str] | None) -> str:
    """Specialist tag wins over the inherited orchestrator tag."""
    agents = [t.split(":", 1)[1] for t in tags or [] if t.startswith("agent:")]
    specific = [a for a in agents if a != "orchestrator"]
    return specific[-1] if specific else (agents[0] if agents else "unknown")


@dataclass
class Call:
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    priced: bool


class CostTracker(BaseCallbackHandler):
    """LangChain callback: records each LLM call. One tracker per chat session."""

    run_inline = True  # keep calls in order, in the event loop thread

    def __init__(self, session_id: str, profile: str = ""):
        self.session_id = session_id
        self.profile = profile
        self.calls: list[Call] = []          # calls of the current message
        self._runs: dict[UUID, dict] = {}    # run_id -> agent, model (filled at start)

    # ------------------------------------------------------------ callback hooks

    def on_chat_model_start(self, serialized: dict, messages: Any, *, run_id: UUID,
                            tags: list[str] | None = None, **kwargs: Any) -> None:
        params = kwargs.get("invocation_params") or {}
        model = params.get("model") or params.get("model_name") or "unknown"
        self._runs[run_id] = {"agent": agent_from_tags(tags), "model": model}

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        info = self._runs.pop(run_id, {"agent": "unknown", "model": "unknown"})
        usage, model = {}, info["model"]
        try:
            message = response.generations[0][0].message
            usage = message.usage_metadata or {}
            model = message.response_metadata.get("model_name") or model
        except (IndexError, AttributeError):
            pass
        self.record(info["agent"], model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    # ------------------------------------------------------------ recording

    def record(self, agent: str, model: str, input_tokens: int, output_tokens: int) -> Call:
        model = normalize_model(model)
        price = find_price(model)
        cost = 0.0
        if price:
            cost = (input_tokens * price.get("input", 0) + output_tokens * price.get("output", 0)) / 1_000_000
        call = Call(agent, model, input_tokens, output_tokens, cost, priced=price is not None)
        self.calls.append(call)
        DATA_DIR.mkdir(exist_ok=True)
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute(_SCHEMA)
            conn.execute(
                "INSERT INTO usage (ts, session_id, profile, agent, model, input_tokens, output_tokens, cost)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), self.session_id, self.profile,
                 agent, model, input_tokens, output_tokens, cost))
        return call

    def start_message(self) -> None:
        self.calls = []

    # ------------------------------------------------------------ summaries

    def message_by_agent(self) -> dict[str, dict]:
        rows: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cost": 0.0})
        for c in self.calls:
            r = rows[c.agent]
            r["calls"] += 1
            r["input"] += c.input_tokens
            r["output"] += c.output_tokens
            r["cost"] += c.cost
        return dict(rows)

    def session_by_agent(self) -> dict[str, dict]:
        if not DB_PATH.exists():
            return {}
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(_SCHEMA)
            rows = conn.execute(
                "SELECT agent, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost)"
                " FROM usage WHERE session_id = ? GROUP BY agent ORDER BY SUM(cost) DESC, agent",
                (self.session_id,)).fetchall()
        return {a: {"calls": n, "input": i, "output": o, "cost": c} for a, n, i, o, c in rows}

    @staticmethod
    def table(rows: dict[str, dict]) -> str:
        """Markdown table of per-agent usage, with a total row."""
        lines = [f"| Agent | LLM calls | Input tokens | Output tokens | Cost ({CURRENCY}) |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        total = {"calls": 0, "input": 0, "output": 0, "cost": 0.0}
        for agent, r in rows.items():
            lines.append(f"| {agent} | {r['calls']} | {r['input']:,} | {r['output']:,} | {r['cost']:.4f} |")
            for k in total:
                total[k] += r[k]
        lines.append(f"| **Total** | **{total['calls']}** | **{total['input']:,}** | "
                     f"**{total['output']:,}** | **{total['cost']:.4f}** |")
        return "\n".join(lines)

    def unpriced_models(self) -> list[str]:
        return sorted({c.model for c in self.calls if not c.priced})
