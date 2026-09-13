"""Extraction layer - the engine's single external evidence boundary."""

from .fixture_adapter import FixtureExtractor, fact_from_json, fixture_key
from .groq_client import Completion, GroqClient, GroqError
from .groq_extractor import CACHED, FAILED, RECORDED, GroqExtractor, RecordSummary
from .port import ExtractionPort
from .prompts import PROMPT_VERSION, build_facts_schema, build_messages
from .usage_log import NullUsageLogger, UsageLogger
from .validate import Violation, validate_fact, validate_facts

__all__ = [
    "CACHED",
    "FAILED",
    "PROMPT_VERSION",
    "RECORDED",
    "Completion",
    "ExtractionPort",
    "FixtureExtractor",
    "GroqClient",
    "GroqError",
    "GroqExtractor",
    "NullUsageLogger",
    "RecordSummary",
    "UsageLogger",
    "Violation",
    "build_facts_schema",
    "build_messages",
    "fact_from_json",
    "fixture_key",
    "validate_fact",
    "validate_facts",
]
