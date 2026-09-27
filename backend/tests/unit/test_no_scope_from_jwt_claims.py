# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Anti-Defect Check — No Scope from JWT Claims
# ─────────────────────────────────────────────────────────────────
# What:  Enforces the HLD §6.2 security requirement that JWT tokens must carry
#        idp_subject ONLY. Reading tenant scope (org_id, department_id, hierarchy_level)
#        from token claims is an architectural defect.
# How:   - Asserts that verify_idp_token() returns strictly a string (idp_subject)
#          and never an object containing authorization attributes.
#        - Asserts that extract_scope_claims_prohibited() raises ForbiddenScopeInTokenError
#          when forged claims (org_id, department_id, hierarchy_level, role) are present.
# Why:   HLD §6.2 & Sprint A5 Success Check 6: Prevents token-forgery, client claim
#        spoofing, and privilege escalation vulnerabilities.
# Tools: pytest, veydus.auth.token.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import jwt
import pytest

from veydus.auth.token import (
    ForbiddenScopeInTokenError,
    extract_scope_claims_prohibited,
    verify_idp_token,
)
from veydus.config import Settings


def test_token_returns_idp_subject_only() -> None:
    """Verifies that token verification returns strictly the subject string without scope."""
    settings = Settings(auth_mode="dev", jwt_secret_key="test_secret_key_12345")
    payload = {"sub": "firebase_uid_abc123"}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")

    subject = verify_idp_token(token, settings)

    # Invariant: Must return string idp_subject only
    assert isinstance(subject, str)
    assert subject == "firebase_uid_abc123"
    assert not hasattr(subject, "org_id")
    assert not hasattr(subject, "department_id")
    assert not hasattr(subject, "hierarchy_level")


def test_forged_scope_claims_in_token_are_rejected() -> None:
    """Proves that reading tenant scope from JWT claims is treated as a defect."""
    forged_claims = {
        "sub": "attacker_uid",
        "org_id": "11111111-1111-1111-1111-111111111111",
        "department_id": "22222222-2222-2222-2222-222222222222",
        "hierarchy_level": 5,
        "role": "operator",
    }

    with pytest.raises(ForbiddenScopeInTokenError) as exc_info:
        extract_scope_claims_prohibited(forged_claims)

    assert "DEFECT [HLD §6.2]" in str(exc_info.value)
    assert "org_id" in str(exc_info.value) or "hierarchy_level" in str(exc_info.value)


@pytest.mark.parametrize("forbidden_key", ["org_id", "department_id", "hierarchy_level", "role"])
def test_individual_forbidden_scope_claim_rejection(forbidden_key: str) -> None:
    """Verifies each forbidden scope key triggers ForbiddenScopeInTokenError."""
    claims = {"sub": "user_123", forbidden_key: "forged_value"}
    with pytest.raises(ForbiddenScopeInTokenError):
        extract_scope_claims_prohibited(claims)
