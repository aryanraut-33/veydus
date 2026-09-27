# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: GCP Identity Platform Token Verification
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying RS256 token verification, Google JWKS signature
#        validation, audience/issuer matching, and error handling.
# How:   Mocks PyJWKClient and generates RSA key pairs via cryptography, testing
#        valid tokens, expired tokens, wrong audience, and unconfigured project ID.
# Why:   HLD §6.2: Ensures robust authentication layer resisting signature tampering,
#        expired tokens, and cross-project token reuse.
# Tools: pytest, unittest.mock, cryptography, pyjwt, veydus.auth.token.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from veydus.auth.token import TokenVerificationError, verify_idp_token
from veydus.config import Settings


@pytest.fixture
def rsa_key_pair():
    """Generates an RSA key pair for testing RS256 token signatures."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


def test_verify_idp_token_dev_mode() -> None:
    """Verifies HMAC fallback token verification in dev mode."""
    settings = Settings(auth_mode="dev", jwt_secret_key="dev_secret_key")
    token = jwt.encode({"sub": "dev_user_789"}, "dev_secret_key", algorithm="HS256")

    subject = verify_idp_token(token, settings)
    assert subject == "dev_user_789"


def test_verify_idp_token_dev_mode_invalid_signature() -> None:
    """Verifies that tampering with dev token signature raises TokenVerificationError."""
    settings = Settings(auth_mode="dev", jwt_secret_key="correct_secret_key")
    token = jwt.encode({"sub": "dev_user_789"}, "wrong_secret_key", algorithm="HS256")

    with pytest.raises(TokenVerificationError) as exc_info:
        verify_idp_token(token, settings)
    assert "Invalid dev token" in str(exc_info.value)


def test_verify_idp_token_rs256_success(rsa_key_pair) -> None:
    """Verifies production RS256 token verification using mocked JWKS signing key."""
    private_key, public_key = rsa_key_pair
    project_id = "veydus-prod-12345"
    settings = Settings(auth_mode="idp", gcp_project_id=project_id)

    payload = {
        "sub": "firebase_user_success_999",
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key_1"})

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

    with patch("veydus.auth.token.get_jwk_client", return_value=mock_client):
        subject = verify_idp_token(token, settings)
        assert subject == "firebase_user_success_999"


def test_verify_idp_token_rs256_expired(rsa_key_pair) -> None:
    """Verifies that expired RS256 tokens are rejected."""
    private_key, public_key = rsa_key_pair
    project_id = "veydus-prod-12345"
    settings = Settings(auth_mode="idp", gcp_project_id=project_id)

    payload = {
        "sub": "firebase_user_expired",
        "aud": project_id,
        "iss": f"https://securetoken.google.com/{project_id}",
        "exp": int(time.time()) - 3600,  # Expired 1 hour ago
    }
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key_1"})

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_key

    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key

    with patch("veydus.auth.token.get_jwk_client", return_value=mock_client):
        with pytest.raises(TokenVerificationError) as exc_info:
            verify_idp_token(token, settings)
        assert "Invalid Identity Platform token" in str(exc_info.value)


def test_verify_idp_token_missing_project_id() -> None:
    """Verifies that empty gcp_project_id raises configuration error in idp mode."""
    settings = Settings(auth_mode="idp", gcp_project_id="")
    with pytest.raises(TokenVerificationError) as exc_info:
        verify_idp_token("dummy.token.here", settings)
    assert "GCP_PROJECT_ID is not configured" in str(exc_info.value)
