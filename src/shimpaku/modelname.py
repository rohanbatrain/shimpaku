"""The model as shown to people: its own short name, never the provider behind it.

Gateway aliases carry the provider (``nvidia-nemotron-lightning``, ``openai/gpt-x``); every
surface that names a model (Shim's answer header, the LLM gateway page) shows ``nemotron-lightning``.
"""

from __future__ import annotations

import re

PROVIDERS = ("nvidia", "openai", "anthropic", "google", "gemini-api", "meta", "mistral", "deepseek", "groq",
             "together", "fireworks", "openrouter", "tokenharbor", "opencode", "azure", "bedrock", "vertex",
             "ollama", "huggingface", "hf", "qwen-api", "zai", "moonshot", "xai", "cohere")
_PREFIX = re.compile(r"^(?:(?:%s)[-_/:.])+" % "|".join(re.escape(p) for p in PROVIDERS), re.IGNORECASE)


def short_model(name: object) -> str:
    s = str(name or "")
    s = s.rsplit("/", 1)[-1]          # "provider/org/model" -> "model"
    return _PREFIX.sub("", s) or s
