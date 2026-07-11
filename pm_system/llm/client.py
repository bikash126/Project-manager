"""LLM clients.

- AnthropicLLM: real calls via the Anthropic SDK (optional dependency).
- MockLLM: scripted responses for tests and the toy project demo.
- MeteredLLM: wraps any client with the cost ledger — every call is budget-checked
  first and recorded after, tagged by project/stage/agent/ticket (design doc §1).
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from pm_system.costs.ledger import CostLedger


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


@dataclass(frozen=True)
class CostTags:
    project_id: str
    stage: str
    agent: str
    ticket_id: str | None = None


class LLMClient(Protocol):
    def complete(self, *, system: str, prompt: str, model: str, max_tokens: int = 8192) -> LLMResponse:
        ...


# USD per million tokens (input, output), matched by substring of the model id.
DEFAULT_PRICING: list[tuple[str, tuple[float, float]]] = [
    ("opus", (15.0, 75.0)),
    ("fable", (15.0, 75.0)),
    ("sonnet", (3.0, 15.0)),
    ("haiku", (1.0, 5.0)),
]
FALLBACK_PRICE = (5.0, 25.0)


def price_usd(model: str, input_tokens: int, output_tokens: int,
              pricing: list[tuple[str, tuple[float, float]]] | None = None) -> float:
    rates = FALLBACK_PRICE
    for needle, candidate in pricing or DEFAULT_PRICING:
        if needle in model:
            rates = candidate
            break
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


class AnthropicLLM:
    """Thin wrapper over the Anthropic Messages API."""

    def __init__(self, api_key: str | None = None, client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "AnthropicLLM requires the 'anthropic' package: pip install pm-system[llm]"
                ) from exc
            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._client = client

    def complete(self, *, system: str, prompt: str, model: str, max_tokens: int = 8192) -> LLMResponse:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
        )


class MockLLM:
    """Deterministic client for tests and demos.

    Either a FIFO queue of canned response texts, or a handler called as
    handler(system, prompt) -> text.
    """

    def __init__(
        self,
        responses: Sequence[str] | None = None,
        handler: Callable[[str, str], str] | None = None,
    ):
        if (responses is None) == (handler is None):
            raise ValueError("pass exactly one of responses / handler")
        self._queue = deque(responses or [])
        self._handler = handler
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, prompt: str, model: str, max_tokens: int = 8192) -> LLMResponse:
        self.calls.append((system, prompt))
        if self._handler is not None:
            text = self._handler(system, prompt)
        else:
            if not self._queue:
                raise RuntimeError("MockLLM response queue exhausted")
            text = self._queue.popleft()
        return LLMResponse(
            text=text,
            input_tokens=max(1, (len(system) + len(prompt)) // 4),
            output_tokens=max(1, len(text) // 4),
            model=model,
        )


class MeteredLLM:
    """Ledger-wrapped client: budget check before each call, record after."""

    def __init__(self, client: LLMClient, ledger: CostLedger,
                 pricing: list[tuple[str, tuple[float, float]]] | None = None):
        self.client = client
        self.ledger = ledger
        self.pricing = pricing

    def complete(self, *, system: str, prompt: str, model: str, tags: CostTags,
                 max_tokens: int = 8192) -> LLMResponse:
        self.ledger.check_budget(tags.project_id, tags.stage)
        response = self.client.complete(
            system=system, prompt=prompt, model=model, max_tokens=max_tokens
        )
        self.ledger.record(
            project_id=tags.project_id,
            stage=tags.stage,
            agent=tags.agent,
            ticket_id=tags.ticket_id,
            model=model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=price_usd(model, response.input_tokens, response.output_tokens, self.pricing),
        )
        return response


_FENCE_OPEN = re.compile(r"```(?:json)?[ \t]*\n")


def extract_json(text: str) -> dict:
    """Pull a single JSON object out of an LLM response.

    Accepts a fenced ```json block or a bare object; raises ValueError
    otherwise. When fenced, the content is taken up to the LAST closing fence
    so that triple backticks inside the JSON (e.g. a README with a code block)
    do not truncate it.
    """
    fence = _FENCE_OPEN.search(text)
    if fence:
        start = fence.end()
        close = text.rfind("```")
        candidate = text[start:close] if close > start else text[start:]
    else:
        first, last = text.find("{"), text.rfind("}")
        if first == -1 or last <= first:
            raise ValueError("no JSON object found in response")
        candidate = text[first : last + 1]
    data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data
