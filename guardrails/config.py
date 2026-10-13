"""Loads config/guardrails.yaml."""
import yaml

from config.settings import ROOT


def load_guardrails() -> dict:
    cfg = yaml.safe_load((ROOT / "config/guardrails.yaml").read_text(encoding="utf-8")) or {}
    cfg.setdefault("approval_required", [])
    guard = cfg.setdefault("input_guard", {})
    if "classifier" not in guard:  # older files used use_classifier: true/false
        guard["classifier"] = "llm" if guard.get("use_classifier", True) else "none"
    clef = guard.setdefault("clef", {})
    clef.setdefault("model", "clef-flash")
    clef.setdefault("block_threshold", 0.6)
    clef.setdefault("timeout_seconds", 20)
    clef.setdefault("fallback", "llm")
    cfg.setdefault("budget", {})
    cfg.setdefault("recursion_limit", 40)
    return cfg


GUARDRAILS = load_guardrails()
