"""Text normalisation for the message channel.

The corpus prints typographic punctuation (curly quotes, en/em dashes, ellipses,
non-breaking spaces) in 60 of 215 messages. The extraction model normalises those code
points inconsistently - a curly apostrophe can come back as a control character - so a
byte-exact ``verbatim_quote`` check (V4) would reject otherwise valid evidence.

Normalising the small, purely typographic set to ASCII before delivery and before the
V4 comparison removes a transport artefact without changing any word, number, or date.
It is applied on both sides of the comparison, so it can never make a fabricated quote
match text that does not contain it.
"""

from __future__ import annotations

# Purely typographic substitutions. No letter, digit, or semantic mark is altered.
_TYPOGRAPHIC = {
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote / apostrophe
    "\u201a": "'",  # single low-9 quote
    "\u201b": "'",  # single high-reversed-9 quote
    "\u201c": '"',  # left double quote
    "\u201d": '"',  # right double quote
    "\u201e": '"',  # double low-9 quote
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",  # non-breaking space
    "\u202f": " ",  # narrow non-breaking space
    "\u2009": " ",  # thin space
    "\u2011": "-",  # non-breaking hyphen
}


def normalize_text(text: str) -> str:
    """Replace typographic punctuation with its ASCII equivalent."""
    if not text:
        return text
    for source, replacement in _TYPOGRAPHIC.items():
        if source in text:
            text = text.replace(source, replacement)
    return text
