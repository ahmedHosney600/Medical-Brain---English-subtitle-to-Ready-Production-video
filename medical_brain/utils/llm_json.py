"""Reading JSON answers from the models — the same for every model.

Models differ in how they hand back JSON: some wrap it in a ```json fence, some add
a sentence before or after it, some leave raw newlines or unescaped quotes inside
strings, some nest the fields or rename them. parse_llm_json() and pick() absorb
those differences, so a gate never fails (or reads a 0) just because of formatting."""
import json
import re


def strip_json_fence(text: str) -> str:
    """Removes a leading ```json ... ``` fence, if the model wrapped its JSON in one."""
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text


class UnreadableAnswer(ValueError):
    """A JSON step's answer that couldn't be read even after the repair request.
    .raw keeps the model's text so the step can still save it."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def _outer_object(text: str) -> str:
    """The first balanced {...} in text (quotes respected), or from the first '{' on."""
    start = text.find("{")
    if start < 0:
        return text
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _escape_stray_quotes(text: str) -> str:
    """Inside a JSON string, a '"' only closes the string when the next non-space
    character is , } ] or :. Any other '"' was meant as text, so escape it."""
    out, in_str, esc = [], False, False
    n = len(text)
    for i, c in enumerate(text):
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            continue
        if esc:
            out.append(c)
            esc = False
        elif c == "\\":
            out.append(c)
            esc = True
        elif c == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j >= n or text[j] in ",}]:":
                out.append(c)
                in_str = False
            else:
                out.append('\\"')
        else:
            out.append(c)
    return "".join(out)


def _python_literals(text: str) -> str:
    """True/False/None outside strings → true/false/null; drops trailing commas."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        m = re.match(r"(True|False|None)\b", text[i:])
        if m:
            out.append({"True": "true", "False": "false", "None": "null"}[m.group(1)])
            i += len(m.group(1))
            continue
        if c == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_llm_json(text: str) -> dict:
    """The JSON object in a model's answer, however it was wrapped. Raises ValueError
    if no object can be read."""
    raw = (text or "").strip()
    fence = re.search(r"```(?:json|JSON)?\s*\n?(.*?)```", raw, re.DOTALL)
    candidates = [fence.group(1)] if fence and "{" in fence.group(1) else []
    candidates.append(raw)
    last_error = None
    for cand in candidates:
        body = _outer_object(cand.strip())
        for fix in (lambda s: s, _escape_stray_quotes, _python_literals,
                    lambda s: _python_literals(_escape_stray_quotes(s))):
            try:
                data = json.loads(fix(body), strict=False)
            except ValueError as e:
                last_error = e
                continue
            if isinstance(data, dict):
                return data
    raise ValueError(f"no JSON object in the answer ({last_error})")


_MISSING = object()


def _lookup(data: dict, name: str):
    if name in data:
        return data[name]
    for value in data.values():          # one level down: {"scores": {...}}
        if isinstance(value, dict) and name in value:
            return value[name]
    return _MISSING


def has(data: dict, *names) -> bool:
    return any(_lookup(data, n) is not _MISSING and _lookup(data, n) not in (None, "") for n in names)


def pick(data: dict, *names, default=None, kind=None):
    """First of `names` found at the top level or one level down. kind=int turns
    "9/10", "9", 9.0 into 9; kind=bool turns "true"/"yes"/"pass" into True."""
    for name in names:
        value = _lookup(data, name)
        if value is _MISSING or value in (None, ""):
            continue
        if kind is int:
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(round(value))
            m = re.search(r"-?\d+(?:\.\d+)?", str(value))
            if m:
                return int(round(float(m.group())))
            continue
        if kind is bool:
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("true", "yes", "pass", "passed", "1", "y"):
                return True
            if s in ("false", "no", "fail", "failed", "0", "n"):
                return False
            continue
        if kind is str:
            return str(value)
        return value
    return default
