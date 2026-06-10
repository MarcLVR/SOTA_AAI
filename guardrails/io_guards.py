"""
Input / output guardrails — no heavy external deps required.

Input guards (run before the graph):
  - Prompt injection detection (keyword patterns + decoding of base64,
    escape-sequence, and zero-width/bidi obfuscation before matching)
  - PII flagging (regex: email, phone, credit card, SSN)
  - Max length check
  - Topic scope enforcement (optional allowlist)

Output guards (run before returning to user):
  - Refusal / error passthrough detection
  - Hallucination risk flag (response makes claims without tool grounding)
  - PII leakage check

Each guard returns a GuardResult(passed, reason, sanitised_text).

Upgrade path: swap the heuristic detectors for
  `guardrails-ai` validators (pip install guardrails-ai) —
  the interface is identical, just replace the check functions.
"""
from __future__ import annotations

import base64
import codecs
import re
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class GuardResult:
    passed: bool
    reason: str = ""
    sanitised_text: str = ""   # original if passed, redacted if not
    warnings: list[str] = field(default_factory=list)


# ── PII patterns ──────────────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email",       re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.I)),
    ("phone_eu",    re.compile(r"\+?\d[\d\s\-\(\)]{8,15}\d")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ssn_us",      re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("iban",        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b")),
]

_PII_PLACEHOLDER = "[REDACTED]"


def _detect_pii(text: str) -> tuple[bool, list[str], str]:
    """Returns (found, types_found, sanitised_text)."""
    found_types = []
    sanitised = text
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            found_types.append(label)
            sanitised = pattern.sub(_PII_PLACEHOLDER, sanitised)
    return bool(found_types), found_types, sanitised


# ── Prompt injection patterns ─────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"ignore (all )?(previous|prior|above) instructions",
        r"disregard (your )?(system |previous )?prompt",
        r"you are now (a |an )?(different|new|evil|jailbroken)",
        r"do anything now",
        r"DAN mode",
        r"act as (if you (are|have) no restrictions|an? unrestricted)",
        r"forget (everything|all) you (know|were told)",
        r"new personality",
        r"override (your )?(safety|ethical|content) (filter|guideline|restriction)",
        r"<\|.*?\|>",       # special token injection attempt
        r"\[INST\].*?\[/INST\]",  # instruction template injection
    ]
]


def _match_injection(text: str) -> str | None:
    """Return the first matching injection snippet, or None."""
    for pattern in _INJECTION_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


# Obfuscation used to smuggle injections past keyword filters.
# Zero-width + bidi control chars: U+200B–200F, U+202A–202E, U+2060, U+FEFF.
_ZERO_WIDTH_RE = re.compile("[​-‏‪-‮⁠﻿]")
_B64_RE        = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _detect_obfuscated_injection(text: str) -> tuple[bool, str]:
    """Catch injections hidden via zero-width/bidi chars, escape sequences, or base64."""
    # 1. Zero-width / bidi smuggling — strip the invisible chars and re-check.
    stripped = _ZERO_WIDTH_RE.sub("", text)
    if stripped != text and (hit := _match_injection(stripped)):
        return True, f"Injection hidden with zero-width/bidi characters: '{hit[:60]}'"

    # 2. Escape-encoded text (\\xNN / \\uNNNN).
    if "\\x" in text or "\\u" in text:
        try:
            decoded = codecs.decode(text, "unicode_escape")
            if hit := _match_injection(decoded):
                return True, f"Injection in escape-encoded text: '{hit[:60]}'"
        except Exception:
            pass

    # 3. Base64-encoded payloads.
    for m in _B64_RE.finditer(text):
        chunk = m.group(0)
        chunk = chunk[: len(chunk) - (len(chunk) % 4)]  # trim to a multiple of 4
        if len(chunk) < 16:
            continue
        try:
            decoded = base64.b64decode(chunk, validate=True).decode("utf-8", "ignore")
        except Exception:
            continue
        if decoded and (hit := _match_injection(decoded)):
            return True, f"Injection in base64-encoded text: '{decoded[:60]}'"

    return False, ""


def _detect_injection(text: str) -> tuple[bool, str]:
    hit = _match_injection(text)
    if hit:
        return True, f"Possible prompt injection detected: '{hit[:60]}'"
    return _detect_obfuscated_injection(text)


# ── Input guard ───────────────────────────────────────────────────────────────

def input_guard(
    text: str,
    max_length: int = 8000,
    allowed_topics: Optional[list[str]] = None,
    redact_pii: bool = True,
) -> GuardResult:
    """
    Validate and sanitise user input before it reaches the agent.

    Args:
        text: The raw user message.
        max_length: Maximum allowed character length.
        allowed_topics: If set, input must mention at least one of these topics
                        (rough keyword check — not NLI). None = no restriction.
        redact_pii: If True, replace detected PII in the sanitised output.
    """
    warnings: list[str] = []

    # Length check
    if len(text) > max_length:
        return GuardResult(
            passed=False,
            reason=f"Input exceeds maximum length ({len(text)} > {max_length} chars).",
            sanitised_text=text[:max_length],
        )

    # Prompt injection
    injected, injection_reason = _detect_injection(text)
    if injected:
        logger.warning(f"[guardrail:input] injection attempt blocked: {injection_reason}")
        return GuardResult(passed=False, reason=injection_reason, sanitised_text=text)

    # PII detection
    has_pii, pii_types, sanitised = _detect_pii(text)
    if has_pii:
        msg = f"PII detected in input ({', '.join(pii_types)}) — redacted."
        warnings.append(msg)
        logger.warning(f"[guardrail:input] {msg}")
        if not redact_pii:
            sanitised = text   # keep original if caller opts out of redaction

    # Topic scope (optional)
    if allowed_topics:
        lower = text.lower()
        if not any(t.lower() in lower for t in allowed_topics):
            return GuardResult(
                passed=False,
                reason=f"Input out of scope. Allowed topics: {allowed_topics}.",
                sanitised_text=sanitised,
            )

    return GuardResult(passed=True, sanitised_text=sanitised, warnings=warnings)


# ── Output guard ──────────────────────────────────────────────────────────────

_REFUSAL_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"i('m| am) (sorry|unable|not able)",
        r"i cannot (help|assist|provide)",
        r"as an ai (language model|assistant)",
        r"i don'?t have (access|the ability)",
    ]
]

_UNCERTAINTY_PHRASES = [
    "i think", "i believe", "i'm not sure", "i'm not certain",
    "might be", "could be", "possibly", "probably", "i guess",
]


def output_guard(
    text: str,
    tool_calls_made: bool = False,
    redact_pii: bool = True,
) -> GuardResult:
    """
    Validate agent output before returning it to the user.

    Args:
        text: The agent's response text.
        tool_calls_made: Whether the agent used any tools to ground this response.
        redact_pii: Redact PII found in the output.
    """
    warnings: list[str] = []

    # PII leakage in output
    has_pii, pii_types, sanitised = _detect_pii(text)
    if has_pii:
        msg = f"PII detected in output ({', '.join(pii_types)}) — redacted."
        warnings.append(msg)
        logger.warning(f"[guardrail:output] {msg}")
        if not redact_pii:
            sanitised = text

    # Hallucination risk: factual-sounding claims without tool grounding
    lower = text.lower()
    uncertainty_count = sum(1 for p in _UNCERTAINTY_PHRASES if p in lower)
    if not tool_calls_made and uncertainty_count == 0 and len(text) > 200:
        warnings.append(
            "Response appears confident but no tools were used to verify facts. "
            "Consider asking the agent to search or retrieve before asserting."
        )

    # Refusal passthrough (not a block, just a flag)
    for pattern in _REFUSAL_PATTERNS:
        if pattern.search(text):
            warnings.append("Response contains a refusal pattern — may be unhelpful.")
            break

    return GuardResult(passed=True, sanitised_text=sanitised, warnings=warnings)
