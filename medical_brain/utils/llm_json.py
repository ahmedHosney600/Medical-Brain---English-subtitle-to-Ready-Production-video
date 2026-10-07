"""Reading JSON answers from the models."""


def strip_json_fence(text: str) -> str:
    """Removes a leading ```json ... ``` fence, if the model wrapped its JSON in one.
    (Same logic every JSON-returning step used to copy inline.)"""
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text
