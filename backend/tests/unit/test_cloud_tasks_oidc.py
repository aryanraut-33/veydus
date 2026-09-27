# ==============================================================================
# FILE: backend/tests/unit/test_cloud_tasks_oidc.py
# WHAT: Unit tests for Google Cloud Tasks OIDC token validation.
# HOW: Uses pytest, unittest.mock, and cryptography to generate RSA key pairs,
#      mock Google's JWKS public certs, and test service account verification.
# WHY: Ensures worker ingestion endpoints are protected against unauthorized calls,
#      verifies service account caller authorization, and confirms dev mode fallback.
# TOOLS/LIBRARIES: pytest, unittest.mock, cryptography (RSA), PyJWT, FastAPI.
# ==============================================================================

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from veydus.auth.oidc import GOOGLE_OIDC_ISSUER, verify_cloud_tasks_oidc
from veydus.config import Settings


@pytest.fixture(scope="module")
def rsa_key_pair():
    """Generate ephemeral RSA key pair for testing OIDC signatures."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _generate_test_oidc_token(
    private_pem: bytes,
    email: str = "veydus-tasks-sa@project.iam.gserviceaccount.com",
    issuer: str = GOOGLE_OIDC_ISSUER,
    expires_in_seconds: int = 3600,
) -> str:
    """Generate a mock Google OIDC ID token signed with private RSA key."""
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "iss": issuer,
        "sub": "109876543210987654321",
        "email": email,
        "email_verified": True,
        "aud": "https://veydus-worker-abcdef-uc.a.run.app",
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": "test-key-id"})


def test_oidc_dev_mode_no_credentials():
    """In dev mode, omitting Authorization header succeeds with dev actor."""
    settings = Settings(auth_mode="dev", cloud_tasks_service_account="dev-tasks@internal")
    with patch("veydus.auth.oidc.get_settings", return_value=settings):
        payload = verify_cloud_tasks_oidc(None)
        assert payload["email"] == "dev-tasks@internal"
        assert payload["email_verified"] is True


def test_oidc_dev_mode_dev_token():
    """In dev mode, passing dev-tasks-token succeeds."""
    settings = Settings(auth_mode="dev", cloud_tasks_service_account="dev-tasks@internal")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="dev-tasks-token")
    with patch("veydus.auth.oidc.get_settings", return_value=settings):
        payload = verify_cloud_tasks_oidc(creds)
        assert payload["email"] == "dev-tasks@internal"


def test_oidc_idp_mode_missing_credentials_raises_401():
    """In idp mode, missing credentials raises HTTP 401."""
    settings = Settings(
        auth_mode="idp", cloud_tasks_service_account="sa@project.iam.gserviceaccount.com"
    )
    with patch("veydus.auth.oidc.get_settings", return_value=settings):
        with pytest.raises(HTTPException) as exc_info:
            verify_cloud_tasks_oidc(None)
        assert exc_info.value.status_code == 401


def test_oidc_idp_mode_valid_token_matches_sa(rsa_key_pair):
    """In idp mode, valid token from authorized SA succeeds."""
    private_pem, public_pem = rsa_key_pair
    target_sa = "veydus-tasks-sa@project.iam.gserviceaccount.com"
    token = _generate_test_oidc_token(private_pem, email=target_sa)

    settings = Settings(auth_mode="idp", cloud_tasks_service_account=target_sa)

    # Mock PyJWKClient to return the public key
    mock_signing_key = MagicMock()
    mock_signing_key.key = public_pem
    mock_jwks = MagicMock()
    mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with (
        patch("veydus.auth.oidc.get_settings", return_value=settings),
        patch("veydus.auth.oidc.get_google_jwks_client", return_value=mock_jwks),
    ):
        payload = verify_cloud_tasks_oidc(creds)
        assert payload["email"] == target_sa


def test_oidc_idp_mode_sa_mismatch_raises_403(rsa_key_pair):
    """In idp mode, valid token from unexpected SA raises HTTP 403."""
    private_pem, public_pem = rsa_key_pair
    attacker_sa = "attacker-sa@project.iam.gserviceaccount.com"
    expected_sa = "veydus-tasks-sa@project.iam.gserviceaccount.com"
    token = _generate_test_oidc_token(private_pem, email=attacker_sa)

    settings = Settings(auth_mode="idp", cloud_tasks_service_account=expected_sa)

    mock_signing_key = MagicMock()
    mock_signing_key.key = public_pem
    mock_jwks = MagicMock()
    mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with (
        patch("veydus.auth.oidc.get_settings", return_value=settings),
        patch("veydus.auth.oidc.get_google_jwks_client", return_value=mock_jwks),
    ):
        with pytest.raises(HTTPException) as exc_info:
            verify_cloud_tasks_oidc(creds)
        assert exc_info.value.status_code == 403
        assert "not authorized" in exc_info.value.detail
