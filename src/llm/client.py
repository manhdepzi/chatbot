"""Thin OpenAI client wrapper with retries for chat + embeddings."""
from __future__ import annotations

import time
from typing import Any

from openai import OpenAI

from .. import config

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def _with_retries(fn, attempts: int = 4, base_delay: float = 1.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            # back off only on rate-limit / server errors; fail fast otherwise
            code = getattr(e, "status_code", None) if hasattr(e, "status_code") else None
            if code not in (429, 500, 502, 503, 504):
                raise
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** i))
    raise last  # pragma: no cover


def chat_json(messages: list[dict], model: str | None = None,
              temperature: float = 0.0) -> dict[str, Any]:
    """Send a chat request and parse a JSON-object response."""
    client = _get_client()
    model = model or config.OPENAI_MODEL

    def call():
        return client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

    resp = _with_retries(call)
    raw = resp.choices[0].message.content
    import json
    return json.loads(raw)


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed a list of texts, returning one vector per text."""
    client = _get_client()
    model = model or config.EMBEDDING_MODEL

    def call():
        return client.embeddings.create(model=model, input=texts)

    resp = _with_retries(call)
    return [d.embedding for d in resp.data]
