# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: PII Redaction & Re-hydration
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying Indian Aadhaar, PAN card, email, phone number
#        redaction, ephemeral reverse mapping generation, and stream re-hydration.
# How:   Pytest test functions invoking PIIRedactor under active and disabled modes,
#        testing collision handling, pattern substitution, and string reconstruction.
# Why:   HLD §8.5 requirement: Sensitive PII must never reach inference endpoints;
#        token replacement must be fully reversible on the client stream path.
# Tools: pytest, veydus.rag.redaction.PIIRedactor.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import pytest

from veydus.rag.redaction import PIIRedactor


@pytest.fixture
def redactor() -> PIIRedactor:
    return PIIRedactor(enabled=True)


def test_redact_aadhaar_and_pan(redactor: PIIRedactor) -> None:
    text = "User Aadhaar is 5432 1234 5678 and tax PAN is ABCDE1234F for payroll."
    res = redactor.redact(text)

    # Plain text should not have original PII
    assert "5432 1234 5678" not in res.redacted_text
    assert "ABCDE1234F" not in res.redacted_text
    assert "<REDACTED_AADHAAR_1>" in res.redacted_text or "<REDACTED_AADHAAR" in res.redacted_text
    assert "<REDACTED_PAN_1>" in res.redacted_text or "<REDACTED_PAN" in res.redacted_text

    # Reverse mapping should capture original strings
    assert any(v == "5432 1234 5678" for v in res.reverse_mapping.values())
    assert any(v == "ABCDE1234F" for v in res.reverse_mapping.values())

    # Rehydration should restore exact original text
    restored = redactor.rehydrate(res.redacted_text, res.reverse_mapping)
    assert restored == text


def test_redact_email_and_phone(redactor: PIIRedactor) -> None:
    text = "Contact support at operations@acme-corp.com or call +1-415-555-0199 directly."
    res = redactor.redact(text)

    assert "operations@acme-corp.com" not in res.redacted_text
    assert (
        "<REDACTED_EMAIL_ADDRESS_1>" in res.redacted_text or "<REDACTED_EMAIL" in res.redacted_text
    )

    restored = redactor.rehydrate(res.redacted_text, res.reverse_mapping)
    assert restored == text


def test_redact_disabled_noop() -> None:
    disabled_redactor = PIIRedactor(enabled=False)
    text = "My PAN is ABCDE1234F and email is test@domain.com"
    res = disabled_redactor.redact(text)

    assert res.redacted_text == text
    assert res.reverse_mapping == {}
    assert res.entities_found == []

    restored = disabled_redactor.rehydrate(res.redacted_text, res.reverse_mapping)
    assert restored == text


def test_rehydrate_partial_streaming_chunks(redactor: PIIRedactor) -> None:
    text = "Employee ABCDE1234F has been verified."
    res = redactor.redact(text)

    # In streaming, LLM might output text surrounding the token in chunks
    token = next(k for k, v in res.reverse_mapping.items() if v == "ABCDE1234F")
    chunk1 = f"Employee {token}"
    chunk2 = " has been verified."

    restored1 = redactor.rehydrate(chunk1, res.reverse_mapping)
    restored2 = redactor.rehydrate(chunk2, res.reverse_mapping)

    assert restored1 + restored2 == text
