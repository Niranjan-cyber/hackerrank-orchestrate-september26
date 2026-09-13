"""A zero-dependency Groq chat-completions client for message extraction.

Groq exposes an OpenAI-compatible REST endpoint, so this is stdlib ``urllib`` only
(CONTEXT D10). It requests strict JSON-schema output, parses the JSON object the model
returns, and retries rate limits and transient failures with exponential backoff.

The transport is injectable so tests never touch the network. Errors carry an
``error_class`` and never contain the API key or response content.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Protocol

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b"

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Nominal Groq list price, USD per million tokens (input, output), used only for the
# usage report's cost column. Groq's free tier covers this workload; the rates are
# documented so the estimate is reproducible rather than hand-waved. Every model the
# recording CLI can select is priced, so no real call logs a null cost.
PRICE_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "qwen/qwen3.8-27b": (Decimal("0.29"), Decimal("0.59")),
    "openai/gpt-oss-20b": (Decimal("0.10"), Decimal("0.50")),
    "openai/gpt-oss-120b": (Decimal("0.15"), Decimal("0.75")),
}


def estimate_cost(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    price = PRICE_PER_MTOK.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    price_in, price_out = price
    million = Decimal("1000000")
    return (
        Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out
    ) / million


class Transport(Protocol):
    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, bytes]: ...


def urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class GroqError(Exception):
    """A model call that could not be completed. Never carries the key or content."""

    def __init__(
        self,
        error_class: str,
        *,
        status: int | None = None,
        retries: int = 0,
        detail: str = "",
    ):
        super().__init__(f"{error_class}: {detail}" if detail else error_class)
        self.error_class = error_class
        self.status = status
        self.retries = retries
        self.detail = detail


@dataclass(frozen=True)
class Completion:
    """The parsed JSON document the model returned, plus its token usage."""

    payload: dict
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    retries: int


class GroqClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        max_tokens: int = 2048,
        extra_body: dict | None = None,
        sleep: Callable[[float], None] = time.sleep,
        transport: Transport | None = None,
    ):
        if not api_key:
            raise ValueError("GROQ_API_KEY is required for live extraction")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        self._sleep = sleep
        self._transport = transport or urllib_transport

    def complete(self, messages: list[dict[str, str]], schema: dict) -> Completion:
        request_body = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "message_facts",
                    "strict": True,
                    "schema": schema,
                },
            },
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        request_body.update(self.extra_body)
        body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "buy-or-wait/1.0 (+python-urllib)",
        }
        url = f"{self.base_url}/chat/completions"

        attempt = 0
        while True:
            status: int | None
            raw: bytes = b""
            try:
                status, raw = self._transport(url, headers, body, self.timeout)
            except OSError:
                status, raw = None, b""

            if status == 200:
                return self._parse(raw, attempt)

            # A daily token cap does not clear on a backoff, so surface it immediately;
            # the recording CLI switches to another model instead of waiting it out.
            if status == 429 and b"per day (TPD)" in raw:
                raise GroqError(
                    "tokens_per_day",
                    status=status,
                    retries=attempt,
                    detail="tokens_per_day",
                )

            error_class = self._classify(status)
            retryable = status is None or status in RETRYABLE_STATUSES
            if retryable and attempt < self.max_retries:
                self._sleep(min(self.base_delay * (2**attempt), self.max_delay))
                attempt += 1
                continue
            raise GroqError(
                error_class, status=status, retries=attempt, detail=error_class
            )

    @staticmethod
    def _classify(status: int | None) -> str:
        if status is None:
            return "network_error"
        if status == 429:
            return "rate_limit_exceeded"
        if status in RETRYABLE_STATUSES:
            return "server_error"
        return f"http_{status}"

    def _parse(self, raw: bytes, retries: int) -> Completion:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GroqError("invalid_response_json", retries=retries, detail=str(exc))
        choices = payload.get("choices") or []
        content = choices[0].get("message", {}).get("content") if choices else None
        if not content:
            raise GroqError("empty_generation", retries=retries)
        try:
            facts = json.loads(content)
        except json.JSONDecodeError as exc:
            raise GroqError("invalid_facts_json", retries=retries, detail=str(exc))
        if not isinstance(facts, dict) or not isinstance(facts.get("facts"), list):
            raise GroqError("invalid_facts_shape", retries=retries)
        usage = payload.get("usage") or {}
        return Completion(
            payload=facts,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            retries=retries,
        )
