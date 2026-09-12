"""Extraction layer - the engine's single external evidence boundary."""

from .fixture_adapter import FixtureExtractor, fixture_key
from .port import ExtractionPort
from .validate import Violation, validate_fact, validate_facts

__all__ = [
    "ExtractionPort",
    "FixtureExtractor",
    "Violation",
    "fixture_key",
    "validate_fact",
    "validate_facts",
]
