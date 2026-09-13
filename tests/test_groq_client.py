"""Tests for the stdlib Groq client: strict schema request, retry/backoff, parsing."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))

from extraction.groq_client import Completion, GroqClient, GroqError  # noqa: E402

SCHEMA = {"type": "object"}


def _ok_body(facts: dict, prompt=10, completion=5) -> bytes:
    return json.dumps(
        {
            "choices": [
                {"message": {"role": "assistant", "content": json.dumps(facts)}}
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }
    ).encode()


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.bodies: list[bytes] = []
        self.headers: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls += 1
        self.bodies.append(body)
        self.headers.append(headers)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SleepSpy:
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, delay):
        self.delays.append(delay)


def _client(transport, sleep=None, max_retries=5):
    return GroqClient(
        api_key="test-key",
        transport=transport,
        sleep=sleep or SleepSpy(),
        max_retries=max_retries,
        base_delay=1.0,
    )


class TestGroqClientSuccess(unittest.TestCase):
    def test_parses_facts_and_usage(self):
        transport = FakeTransport([(200, _ok_body({"facts": []}, 11, 7))])
        result = _client(transport).complete(
            [{"role": "user", "content": "hi"}], SCHEMA
        )
        self.assertIsInstance(result, Completion)
        self.assertEqual(result.payload, {"facts": []})
        self.assertEqual(result.input_tokens, 11)
        self.assertEqual(result.output_tokens, 7)
        self.assertEqual(result.total_tokens, 18)
        self.assertEqual(result.retries, 0)

    def test_request_is_strict_schema_one_message(self):
        transport = FakeTransport([(200, _ok_body({"facts": []}))])
        _client(transport).complete([{"role": "user", "content": "hi"}], SCHEMA)
        sent = json.loads(transport.bodies[0].decode())
        response_format = sent["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(response_format["json_schema"]["schema"], SCHEMA)
        self.assertEqual(len(sent["messages"]), 1)

    def test_authorization_header_is_bearer(self):
        transport = FakeTransport([(200, _ok_body({"facts": []}))])
        _client(transport).complete([{"role": "user", "content": "hi"}], SCHEMA)
        self.assertEqual(transport.headers[0]["Authorization"], "Bearer test-key")


class TestGroqClientRetry(unittest.TestCase):
    def test_retries_rate_limit_with_exponential_backoff(self):
        sleep = SleepSpy()
        transport = FakeTransport(
            [
                (429, b'{"error": "rate limit"}'),
                (429, b'{"error": "rate limit"}'),
                (200, _ok_body({"facts": []})),
            ]
        )
        result = _client(transport, sleep=sleep).complete([], SCHEMA)
        self.assertEqual(result.retries, 2)
        self.assertEqual(sleep.delays, [1.0, 2.0])

    def test_exhausted_retries_raises_with_error_class(self):
        transport = FakeTransport([(429, b'{"error": "rate limit"}')] * 3)
        with self.assertRaises(GroqError) as ctx:
            _client(transport, max_retries=2).complete([], SCHEMA)
        self.assertEqual(ctx.exception.error_class, "rate_limit_exceeded")
        self.assertEqual(ctx.exception.retries, 2)
        self.assertEqual(transport.calls, 3)

    def test_daily_token_cap_is_not_retried(self):
        body = (
            b'{"error":{"message":"Rate limit reached ... on tokens per day (TPD): '
            b'Limit 200000, Used 198591","type":"tokens"}}'
        )
        transport = FakeTransport([(429, body)] * 4)
        sleep = SleepSpy()
        with self.assertRaises(GroqError) as ctx:
            _client(transport, sleep=sleep).complete([], SCHEMA)
        self.assertEqual(ctx.exception.error_class, "tokens_per_day")
        self.assertEqual(sleep.delays, [])
        self.assertEqual(transport.calls, 1)

    def test_server_error_is_retried(self):
        transport = FakeTransport([(503, b"nope"), (200, _ok_body({"facts": []}))])
        sleep = SleepSpy()
        result = _client(transport, sleep=sleep).complete([], SCHEMA)
        self.assertEqual(result.retries, 1)
        self.assertEqual(sleep.delays, [1.0])

    def test_network_error_is_retried(self):
        transport = FakeTransport([OSError("boom"), (200, _ok_body({"facts": []}))])
        result = _client(transport, sleep=SleepSpy()).complete([], SCHEMA)
        self.assertEqual(result.retries, 1)

    def test_client_error_is_not_retried(self):
        transport = FakeTransport([(400, b'{"error": "bad request"}')])
        sleep = SleepSpy()
        with self.assertRaises(GroqError) as ctx:
            _client(transport, sleep=sleep).complete([], SCHEMA)
        self.assertEqual(ctx.exception.error_class, "http_400")
        self.assertEqual(ctx.exception.retries, 0)
        self.assertEqual(sleep.delays, [])
        self.assertEqual(transport.calls, 1)

    def test_error_message_never_contains_the_api_key(self):
        transport = FakeTransport([(400, b'{"error": "bad request"}')])
        with self.assertRaises(GroqError) as ctx:
            _client(transport).complete([], SCHEMA)
        self.assertNotIn("test-key", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
