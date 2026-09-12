"""Extraction port - the single boundary between untrusted evidence and the engine.

The engine core is pure: it receives a tuple of already-validated `Fact` values and
never touches files, networks, or clocks. This module defines the protocol that the
imperative shell uses to obtain those facts.

See docs/contracts/extraction-fact-schema.md for the fact schema, validation rules,
and fixture keying scheme.
"""

from __future__ import annotations

from typing import Protocol

from engine.types import Fact


class ExtractionPort(Protocol):
    """Shell-side boundary for turning messages and images into validated facts."""

    def facts_for_user(self, user_id: str) -> tuple[Fact, ...]:
        """All validated facts for one user, deterministically ordered.

        Returns an empty tuple when the user has no evidence. Never returns None.
        """

    def image_amount(self, event_id: str) -> Fact | None:
        """The resolved amount for a blank-amount event, or None if unresolvable.

        None triggers deterministic imputation. This method never returns zero.
        """
