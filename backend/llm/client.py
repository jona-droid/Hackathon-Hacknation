from __future__ import annotations

import json
import logging
from typing import Any

from backend.core.config import ANTHROPIC_API_KEY

logger = logging.getLogger("robot-apprentice.llm")

DEFAULT_MODEL = "claude-3-5-sonnet-20241022"


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.model = model
        self._client = None
        if self.api_key:
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=self.api_key, timeout=4.0)
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

        response = self._client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            temperature=temperature,
        )

        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        return "".join(text_parts).strip()

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
