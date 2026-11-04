"""
Comprehensive Security and Authentication Test Suite for SereinOS.
Verifies password hashing, age confirmation gates, session authentication,
input sanitization, rate limiting, and secret masking.
"""
import pytest
import time
from serein_core.auth import (
    hash_password, verify_password, sanitize_input,
    RateLimiter, SecurityManager, UserSession
)
from serein_core.config import AIProviderConfig, SecurityConfig, mask_secret


def test_password_hashing_and_verification():
    """Passwords must be hashed with PBKDF2-HMAC-SHA256, unique salts, and verified accurately."""
    pwd = "CorrectHorseBatteryStaple123!"
    h1 = hash_password(pwd)
    h2 = hash_password(pwd)

    # Format check: pbkdf2_sha256$<iterations>$<salt>$<hash>
    assert h1.startswith("pbkdf2_sha256$600000$")
    assert h2.startswith("pbkdf2_sha256$600000$")

    # Unique salts generate unique hashes
    assert h1 != h2

    # Verification
    assert verify_password(pwd, h1) is True
    assert verify_password(pwd, h2) is True
    assert verify_password("WrongPassword!", h1) is False
    assert verify_password("", h1) is False
    assert verify_password(pwd, "") is False


def test_age_confirmation_gate():
    """User registration must require explicit age confirmation."""
    sec = SecurityManager()

    # Attempting to register without age confirmation must raise ValueError
    with pytest.raises(ValueError, match="Age confirmation"):
        sec.register_user("teen_user", "SecurePass123!", role="user", age_confirmed=False)

    # With age confirmation, registration succeeds
    success = sec.register_user("adult_user", "SecurePass123!", role="user", age_confirmed=True)
    assert success is True

    # Authenticate user and verify session retains age confirmation
    session = sec.authenticate("adult_user", "SecurePass123!")
    assert session is not None
    assert session.username == "adult_user"
    assert session.age_confirmed is True


def test_session_lifecycle_and_validation():
    """Sessions must expire after timeout and allow logout."""
    sec = SecurityManager()
    sec.register_user("test_user", "Password987!", age_confirmed=True)

    session = sec.authenticate("test_user", "Password987!")
    assert session is not None

    token = session.session_token
    # Valid token
    validated = sec.validate_session(token)
    assert validated is not None
    assert validated.username == "test_user"

    # Invalid token
    assert sec.validate_session("fake_token_12345") is None

    # Expiration simulation
    session.expires_in_seconds = 0.01
    time.sleep(0.02)
    assert sec.validate_session(token) is None


def test_input_sanitization_and_xss_protection():
    """Form inputs must be stripped of control chars, length-bounded, and HTML-escaped."""
    dirty_xss = "<script>alert('xss');</script> Hello World! \x00\x07"
    cleaned = sanitize_input(dirty_xss, max_length=100, allow_html=False)

    assert "<script>" not in cleaned
    assert "&lt;script&gt;" in cleaned
    assert "\x00" not in cleaned
    assert "\x07" not in cleaned
    assert "Hello World!" in cleaned

    # Length boundary
    long_text = "A" * 5000
    bounded = sanitize_input(long_text, max_length=50)
    assert len(bounded) == 50


def test_rate_limiter():
    """Rate limiter must enforce sliding window request ceilings."""
    limiter = RateLimiter(max_requests=3, window_seconds=1.0)
    client = "192.168.1.100"

    assert limiter.is_allowed(client) is True
    assert limiter.is_allowed(client) is True
    assert limiter.is_allowed(client) is True
    # 4th request exceeds max_requests=3
    assert limiter.is_allowed(client) is False

    # Other client is not affected
    assert limiter.is_allowed("192.168.1.101") is True


def test_api_key_masking():
    """Sensitive API keys and secrets must be masked for logs and telemetry."""
    cfg = AIProviderConfig(
        nvidia_api_key="nvapi-1234567890abcdef1234567890abcdef",
        openrouter_api_key="sk-or-v1-abcdef1234567890abcdef1234567890"
    )
    masked = cfg.get_masked_keys()

    assert "1234567890abcdef" not in masked["nvidia_api_key"]
    assert "****" in masked["nvidia_api_key"]
    assert "****" in masked["openrouter_api_key"]
    assert mask_secret(None) == "<not_set>"
    assert mask_secret("") == "<not_set>"


def test_production_security_defaults():
    """Security defaults must enforce debug_mode=False and safe baselines."""
    sec_cfg = SecurityConfig()
    assert sec_cfg.debug_mode is False
    assert sec_cfg.sqlcipher_enabled is True
    assert sec_cfg.age_verification_enforced is True
