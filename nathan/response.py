"""Decode provider JSON envelopes without exposing them in the chat UI."""

import json
import re

from pydantic_core import from_json


def json_body(raw: str) -> str:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\s*```", text, flags=re.DOTALL)
    return fenced.group(1) if fenced else text


def decode_response(raw: str):
    return json.loads(json_body(raw))


def partial_answer(raw: str) -> str:
    text = re.sub(r"^\s*```(?:json)?\s*", "", raw)
    try:
        value = from_json(text, allow_partial="trailing-strings")
        return (
            value.get("answer", "")
            if isinstance(value, dict) and isinstance(value.get("answer"), str)
            else ""
        )
    except ValueError:
        return ""
