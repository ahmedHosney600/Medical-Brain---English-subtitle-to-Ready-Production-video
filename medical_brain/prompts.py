"""Loads the step prompts from prompts/<name>.md.

A prompt file can contain {{NAME}} markers (e.g. {{EGYPTIAN_CTR_RULES}}) that the
step fills in: load_prompt("packaging_generator", EGYPTIAN_CTR_RULES=...).
JSON examples in prompts use single braces, so they are left alone."""
import os
import re

from .paths import PROMPTS_DIR

_cache = {}


def load_prompt(name: str, **parts) -> str:
    if name not in _cache:
        path = os.path.join(PROMPTS_DIR, name + ".md")
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        # Files end with one newline for editors; the prompt itself doesn't.
        _cache[name] = text[:-1] if text.endswith("\n") else text
    text = _cache[name]
    for key, value in parts.items():
        text = text.replace("{{" + key + "}}", value)
    left = re.findall(r"\{\{([A-Z_]+)\}\}", text)
    if left:
        raise KeyError(f"prompts/{name}.md needs values for: {', '.join(left)}")
    return text
