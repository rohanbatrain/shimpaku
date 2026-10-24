"""Role routing: triage / diagnose / escalate -> gateway model names (settings.models())."""

from __future__ import annotations

import re

from shimpaku.config import settings

ROLES = ("triage", "diagnose", "escalate")
NEXT = {"triage": "diagnose", "diagnose": "escalate", "escalate": "escalate"}

_TRIAGE = re.compile(r"\b(health|status|looking|overview|anything wrong|how is|how's|summary|summar)\b", re.I)
_MULTI = re.compile(r"\b(again|recurr|keeps|every time|both|and also|campus and fabric|fabric and campus)\b", re.I)


def resolve(model_or_role: str | None) -> tuple[str | None, str]:
    """Return (role, gateway model). A plain model name returns (None, name)."""
    m = settings.models()
    if model_or_role in (None, "", "auto"):
        return "diagnose", m.get("diagnose", "")
    if model_or_role in m:
        return model_or_role, m[model_or_role]
    return None, model_or_role


def initial_role(question: str) -> str:
    """First-pass role for 'auto' routing (R7 §1A routing rule)."""
    if _MULTI.search(question):
        return "escalate"
    if _TRIAGE.search(question):
        return "triage"
    return "diagnose"


def model_for(role: str) -> str:
    return settings.models().get(role) or settings.models().get("diagnose", "")
