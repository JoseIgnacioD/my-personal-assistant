"""Output guard: runs after the orchestrator and redacts secrets from the final answer."""
import os
import re

from langchain_core.messages import AIMessage

from guardrails.patterns import mask_sensitive

SECRET_PATTERNS = [
    re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),    # OpenAI / Anthropic keys
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),                   # Google API keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),         # bearer tokens
    re.compile(r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*\S{8,}"),
]
SECRET_ENV_VARS = ["GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "N8N_SECRET",
                   "AITOKENTRACKER_API_KEY", "CLOUDFLARE_AUTH_TOKEN"]


def redact(text: str) -> tuple[str, int]:
    count = 0
    for name in SECRET_ENV_VARS:  # exact values of our own secrets
        value = os.getenv(name, "")
        if len(value) >= 8 and value in text:
            count += text.count(value)
            text = text.replace(value, "[REDACTED]")
    for rx in SECRET_PATTERNS:
        text, n = rx.subn("[REDACTED]", text)
        count += n
    text, found = mask_sensitive(text)
    return text, count + len(found)


def output_guard(state: dict) -> dict:
    last = state["messages"][-1]
    if not isinstance(last, AIMessage):
        return {"output_notes": []}
    content = last.content if isinstance(last.content, str) else "".join(
        b.get("text", "") for b in last.content if isinstance(b, dict))
    clean, count = redact(content)
    if not count:
        return {"output_notes": []}
    return {"messages": [AIMessage(content=clean, id=last.id)],
            "output_notes": [f"redacted {count} secret(s)"]}
