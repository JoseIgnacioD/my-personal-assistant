"""Regex patterns shared by the guardrails (used by the agents and the MCP server)."""
import re

# Text that tries to give the assistant new instructions.
INJECTION = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|messages|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above|your) (instructions|rules)",
    r"forget (all |your )?(previous )?(instructions|rules)",
    r"you are now (a|an|in)\b",
    r"(developer|dan|jailbreak) mode",
    r"(reveal|show|print|repeat) (your |the )?(system prompt|instructions|hidden prompt)",
    r"new instructions?:",
    r"\bact as (an? )?(unrestricted|unfiltered)",
    # Spanish
    r"ignora (todas )?(las )?instrucciones (anteriores|previas)",
    r"olvida (tus|las) instrucciones",
]

# Instructions that only make sense when aimed at an assistant reading someone's inbox.
EMAIL_ATTACK = INJECTION + [
    r"(assistant|ai|agent|jarvis)[,:]? (please )?(forward|send|reply|delete|transfer)",
    r"forward (all|every|each|the|your|my) (of )?(emails?|messages?|mails?|inbox)",
    r"send (all|every|the|your|my) (emails?|messages?|contacts|passwords?|files?) to",
    r"(delete|remove) (all|every) (emails?|messages?)",
    r"(reply|respond) with (your|the) (password|api key|credentials)",
]

_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION]
_EMAIL_ATTACK_RE = [re.compile(p, re.IGNORECASE) for p in EMAIL_ATTACK]


def find_injection(text: str) -> str | None:
    """Return the matched snippet if the text looks like a prompt injection."""
    for rx in _INJECTION_RE:
        if m := rx.search(text or ""):
            return m.group(0)
    return None


def find_email_attack(text: str) -> str | None:
    """Return the matched snippet if an email tries to instruct the assistant."""
    for rx in _EMAIL_ATTACK_RE:
        if m := rx.search(text or ""):
            return m.group(0)
    return None


# ---------------------------------------------------------------- sensitive data

CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        checksum += d
    return len(digits) >= 13 and checksum % 10 == 0


def mask_sensitive(text: str) -> tuple[str, list[str]]:
    """Mask card numbers (Luhn-valid) and IBANs. Returns (masked text, what was masked)."""
    found: list[str] = []

    def card(m: re.Match) -> str:
        if _luhn_ok(m.group(0)):
            found.append("card number")
            return "[CARD NUMBER REMOVED]"
        return m.group(0)

    def iban(m: re.Match) -> str:
        found.append("IBAN")
        return "[IBAN REMOVED]"

    text = CARD.sub(card, text)
    text = IBAN.sub(iban, text)
    return text, found
