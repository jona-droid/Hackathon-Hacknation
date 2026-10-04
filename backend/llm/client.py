from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.core.config import ANTHROPIC_API_KEY
from backend.llm import usage

logger = logging.getLogger("robot-apprentice.llm")
T = TypeVar("T", bound=BaseModel)

# Knowledge distillation: rare calls where quality matters most.
DEFAULT_MODEL = "claude-opus-5-5"


def _image_block(image_b64: str) -> dict[str, Any]:
    if "," in image_b64:  # data URL header
        image_b64 = image_b64.split(",", 1)[1]
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}}


class LLMClient:
    """Synchronous Claude client (run it with asyncio.to_thread). Every call is metered in usage.py."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        purpose: str = "other",
    ) -> None:
        self.api_key = api_key if api_key is not None else ANTHROPIC_API_KEY
        self.model = model
        self.purpose = purpose
        self._client = None
        if self.api_key:
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.api_key, timeout=timeout, max_retries=1)
            except Exception as e:
                logger.warning(f"Could not initialize Anthropic client: {e}")

    @property
    def is_available(self) -> bool:
        return self._client is not None and bool(str(self.api_key).strip())

    def _messages(self, prompt: str, image_b64: str | None) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [_image_block(image_b64)] if image_b64 else []
        content.append({"type": "text", "text": prompt})
        return [{"role": "user", "content": content}]

    def _request(self, method: str, system: str, messages: list[dict[str, Any]], max_tokens: int, **extra: Any) -> Any:
        if not self.is_available:
            raise RuntimeError("Anthropic client is not configured (missing ANTHROPIC_API_KEY).")
        if self.model.startswith("claude-haiku"):
            response = getattr(self._client.messages, method)(
                model=self.model, system=system, messages=messages, max_tokens=max_tokens, **extra
            )
        else:
            # Opus 5.5: thinking is always on (thinking tokens count toward max_tokens), sampling
            # parameters are rejected, and "fallbacks" re-runs a declined request on another model.
            response = getattr(self._client.beta.messages, method)(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens + 8000,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **extra,
            )
        usage.record(self.model, getattr(response, "usage", None), self.purpose)
        return response

    def call_multimodal(self, prompt: str, system: str = "", image_b64: str | None = None, max_tokens: int = 1500) -> str:
        response = self._request("create", system, self._messages(prompt, image_b64), max_tokens)
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request")
        return "".join(block.text for block in response.content if block.type == "text").strip()

    def parse(
        self,
        prompt: str,
        system: str,
        output_format: type[T],
        max_tokens: int = 1000,
        image_b64: str | None = None,
    ) -> T:
        """Structured output validated against a Pydantic model."""
        response = self._request("parse", system, self._messages(prompt, image_b64), max_tokens, output_format=output_format)
        if response.stop_reason in ("refusal", "max_tokens") or response.parsed_output is None:
            raise RuntimeError(f"No structured answer from Claude (stop_reason={response.stop_reason})")
        return response.parsed_output

    def call_json(self, prompt: str, system: str = "", image_b64: str | None = None, max_tokens: int = 2000) -> dict[str, Any]:
        """Free-form JSON (kept for callers without a schema; prefer parse())."""
        raw = self.call_multimodal(
            prompt=prompt + "\n\nCRITICAL: Respond with strictly valid JSON only, without markdown backticks.",
            system=system,
            image_b64=image_b64,
            max_tokens=max_tokens,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()
        return json.loads(cleaned)
