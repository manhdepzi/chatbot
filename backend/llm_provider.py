from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()


def provider_name() -> str:
    return (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()


def enabled() -> bool:
    provider = provider_name()
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


def configured_model() -> str:
    provider = provider_name()
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def _json_from_text(text: str) -> Any:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _generate_gemini_json(prompt: str, temperature: float) -> Any:
    import google.generativeai as genai

    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise ValueError("Chưa cấu hình GEMINI_API_KEY/GOOGLE_API_KEY.")
    genai.configure(api_key=key)
    response = genai.GenerativeModel(configured_model()).generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json", "temperature": temperature},
    )
    return _json_from_text(response.text)


def _generate_openai_json(prompt: str, temperature: float) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Chưa cài thư viện openai. Hãy chạy: pip install openai") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("Chưa cấu hình OPENAI_API_KEY.")

    response = OpenAI().responses.create(
        model=configured_model(),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    }
                ],
            }
        ],
        text={"format": {"type": "json_object"}},
        temperature=temperature,
    )
    output_text: Optional[str] = getattr(response, "output_text", None)
    if not output_text:
        output_text = str(response)
    return _json_from_text(output_text)


def generate_json(prompt: str, temperature: float = 0.1) -> Any:
    provider = provider_name()
    if provider == "openai":
        return _generate_openai_json(prompt, temperature)
    if provider in {"gemini", "google"}:
        return _generate_gemini_json(prompt, temperature)
    raise ValueError(f"LLM_PROVIDER không hỗ trợ: {provider}. Chọn 'gemini' hoặc 'openai'.")


def status() -> Dict[str, Any]:
    return {
        "provider": provider_name(),
        "model": configured_model(),
        "enabled": enabled(),
    }
