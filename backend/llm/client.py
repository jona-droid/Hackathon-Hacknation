from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.core.config import ANTHROPIC_API_KEY

logger = logging.getLogger("robot-apprentice.llm")
T = TypeVar("T", bound=BaseModel)

# Knowledge distillation, flight summary and comparison: rare calls where quality matters.
DEFAULT_MODEL = "claude-opus-5-5"


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, timeout: float = 60.0) -> None:
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.model = model
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

    def call_multimodal(
        self,
        prompt: str,
        system: str = "",
        image_b64: str | None = None,
        max_tokens: int = 1500,
        temperature: float = 0.2,
    ) -> str:
        if not self.is_available:
            raise RuntimeError("Anthropic client is not configured (missing ANTHROPIC_API_KEY).")

        content: list[dict[str, Any]] = []

        if image_b64:
            # Clean image string if necessary
            if "," in image_b64:
                image_b64 = image_b64.split(",", 1)[1]
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_b64,
                },
            })

        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]

        if self.model.startswith("claude-haiku"):
            response = self._client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        else:
            # Opus 5.5: thinking is always on (thinking tokens count toward max_tokens), sampling
            # parameters are rejected, and "fallbacks" re-runs a declined request on another model.
            response = self._client.beta.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens + 8000,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )

        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined the request")
        text_parts = [block.text for block in response.content if block.type == "text"]
        return "".join(text_parts).strip()

    def parse(self, prompt: str, system: str, output_format: type[T], max_tokens: int = 1000) -> T:
        """Structured output validated against a Pydantic model."""
        if not self.is_available:
            raise RuntimeError("Anthropic client is not configured (missing ANTHROPIC_API_KEY).")
        messages = [{"role": "user", "content": prompt}]
        if self.model.startswith("claude-haiku"):
            response = self._client.messages.parse(
                model=self.model, system=system, messages=messages, max_tokens=max_tokens, output_format=output_format,
            )
        else:
            response = self._client.beta.messages.parse(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens + 8000,
                output_config={"effort": "low"},
                output_format=output_format,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        if response.stop_reason in ("refusal", "max_tokens") or response.parsed_output is None:
            raise RuntimeError(f"No structured answer from Claude (stop_reason={response.stop_reason})")
        return response.parsed_output

    def call_json(
        self,
        prompt: str,
        system: str = "",
        image_b64: str | None = None,
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        raw = self.call_multimodal(
            prompt=prompt + "\n\nCRITICAL: Respond with strictly valid JSON only, without markdown backticks.",
            system=system,
            image_b64=image_b64,
            max_tokens=max_tokens,
            temperature=0.1,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()

        return json.loads(cleaned)
