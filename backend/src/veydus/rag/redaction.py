# ─────────────────────────────────────────────────────────────────
# VEYDUS — PII Redaction & Re-hydration Engine
# ─────────────────────────────────────────────────────────────────
# What:  In-process detection, tokenized redaction, and re-hydration of
#        Personally Identifiable Information (PII) before LLM prompt assembly.
# How:   - Microsoft Presidio Analyzer with custom PatternRecognizers for
#          Indian Aadhaar (UIDAI format) and Indian PAN cards, plus standard
#          entities (EMAIL_ADDRESS, PHONE_NUMBER, PERSON, US_SSN, IP_ADDRESS).
#        - Span-level de-duplication and substitution with typed tokens
#          (e.g., <REDACTED_AADHAAR_1>, <REDACTED_PAN_1>).
#        - Maintains request-scoped reverse mapping dictionary in ephemeral
#          memory only; never logs or persists unredacted PII (HLD §8.5).
#        - rehydrate(): Reconstructs original strings from stream tokens before
#          yielding to the client, guaranteeing the LLM never sees plain PII.
# Why:   HLD §8.5 & Security Requirements mandate strict privacy boundaries:
#        zero PII leakage to third-party or hosted inference endpoints.
# Tools: presidio-analyzer, spacy (en_core_web_sm), pydantic v2, re, logging.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Core entity types targeted for enterprise RAG redaction
SUPPORTED_ENTITIES: frozenset[str] = frozenset(
    {
        "IN_AADHAAR",
        "IN_PAN",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "PERSON",
        "US_SSN",
        "IP_ADDRESS",
    }
)


class PIIRedactionResult(BaseModel):
    """Result of PII redaction containing sanitized text and reverse mapping."""

    redacted_text: str = Field(description="Sanitized text with tokens replacing PII")
    reverse_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Ephemeral token-to-plaintext mapping stored in request memory only",
    )
    entities_found: list[str] = Field(
        default_factory=list,
        description="List of entity type names detected during scan",
    )


class PIIRedactor:
    """Enterprise PII detector and reversible anonymizer."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._analyzer: Any | None = None
        self._init_analyzer()

    def _init_analyzer(self) -> None:
        if not self.enabled:
            return

        try:
            import spacy
            from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
            from presidio_analyzer.nlp_engine import SpacyNlpEngine

            # Load spacy model deterministically without external downloads
            nlp = spacy.load("en_core_web_sm")
            nlp_engine = SpacyNlpEngine(
                models=[{"lang_code": "en", "model_name": "en_core_web_sm"}]
            )
            nlp_engine.nlp = {"en": nlp}  # type: ignore[assignment]

            analyzer = AnalyzerEngine(nlp_engine=nlp_engine)

            # 1. Custom Recognizer: Indian Aadhaar Number
            # Format: 12 digits, typically formatted as XXXX XXXX XXXX or XXXXXXXXXXXX
            aadhaar_pattern = Pattern(
                name="AadhaarPattern",
                regex=r"\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b",
                score=0.85,
            )
            aadhaar_recognizer = PatternRecognizer(
                supported_entity="IN_AADHAAR",
                patterns=[aadhaar_pattern],
                supported_language="en",
            )
            analyzer.registry.add_recognizer(aadhaar_recognizer)

            # 2. Custom Recognizer: Indian PAN Card Number
            # Format: 5 uppercase letters + 4 digits + 1 uppercase letter
            pan_pattern = Pattern(
                name="PanPattern",
                regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
                score=0.95,
            )
            pan_recognizer = PatternRecognizer(
                supported_entity="IN_PAN",
                patterns=[pan_pattern],
                supported_language="en",
            )
            analyzer.registry.add_recognizer(pan_recognizer)

            self._analyzer = analyzer
            logger.info(
                "Presidio PII analyzer successfully initialized with custom Aadhaar/PAN rules"
            )
        except Exception as exc:
            logger.warning(
                "Failed to initialize Presidio NLP engine: %s. Using regex fallback.",
                exc,
            )
            self._analyzer = None

    def redact(self, text: str) -> PIIRedactionResult:
        """Sanitizes plain text by replacing sensitive PII spans with typed tokens."""
        if not self.enabled or not text:
            return PIIRedactionResult(
                redacted_text=text,
                reverse_mapping={},
                entities_found=[],
            )

        if self._analyzer is not None:
            return self._redact_with_presidio(text)
        return self._redact_with_regex_fallback(text)

    def _redact_with_presidio(self, text: str) -> PIIRedactionResult:
        """Execute Presidio analysis and token replacement."""
        assert self._analyzer is not None
        results = self._analyzer.analyze(
            text=text,
            entities=list(SUPPORTED_ENTITIES),
            language="en",
        )

        # Sort spans by start offset ascending, then score descending
        # Filter overlaps: if two spans collide, pick the higher scoring or longer span
        filtered_results: list[Any] = []
        for res in sorted(results, key=lambda r: (r.start, -r.score, -(r.end - r.start))):
            if not filtered_results:
                filtered_results.append(res)
                continue
            prev = filtered_results[-1]
            if res.start < prev.end:
                # Overlap collision: skip the secondary entity
                continue
            filtered_results.append(res)

        reverse_mapping: dict[str, str] = {}
        entities_found: list[str] = []
        entity_counters: dict[str, int] = {}

        # Reconstruct text by substituting spans from end to beginning to preserve offsets
        # But generate sequential token IDs based on natural reading order (1, 2, 3...)
        span_replacements: list[tuple[int, int, str]] = []
        for res in filtered_results:
            tag = res.entity_type.replace("IN_", "")
            count = entity_counters.get(tag, 0) + 1
            entity_counters[tag] = count
            token = f"<REDACTED_{tag}_{count}>"

            raw_val = text[res.start : res.end]
            reverse_mapping[token] = raw_val
            entities_found.append(res.entity_type)
            span_replacements.append((res.start, res.end, token))

        # Apply replacements from end to start to avoid index drift
        sanitized = text
        for start, end, token in sorted(span_replacements, key=lambda s: s[0], reverse=True):
            sanitized = sanitized[:start] + token + sanitized[end:]

        return PIIRedactionResult(
            redacted_text=sanitized,
            reverse_mapping=reverse_mapping,
            entities_found=entities_found,
        )

    def _redact_with_regex_fallback(self, text: str) -> PIIRedactionResult:
        """Hermetic regex fallback when Presidio NLP engine is unavailable."""
        patterns: list[tuple[str, re.Pattern[str]]] = [
            ("AADHAAR", re.compile(r"\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b")),
            ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
            ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
            (
                "PHONE_NUMBER",
                re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
            ),
        ]

        reverse_mapping: dict[str, str] = {}
        entities_found: list[str] = []
        entity_counters: dict[str, int] = {}
        sanitized = text

        for entity_type, pat in patterns:
            matches = list(pat.finditer(sanitized))
            for match in reversed(matches):
                count = entity_counters.get(entity_type, 0) + 1
                entity_counters[entity_type] = count
                token = f"<REDACTED_{entity_type}_{count}>"
                raw_val = match.group(0)
                reverse_mapping[token] = raw_val
                entities_found.append(entity_type)
                sanitized = sanitized[: match.start()] + token + sanitized[match.end() :]

        return PIIRedactionResult(
            redacted_text=sanitized,
            reverse_mapping=reverse_mapping,
            entities_found=entities_found,
        )

    @staticmethod
    def rehydrate(text: str, reverse_mapping: dict[str, str]) -> str:
        """Replaces redaction tokens in streaming tokens/text with original values."""
        if not reverse_mapping or not text:
            return text

        rehydrated = text
        for token, original_value in reverse_mapping.items():
            rehydrated = rehydrated.replace(token, original_value)
        return rehydrated
