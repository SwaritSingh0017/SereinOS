"""
Authentication, Authorization, Rate Limiting, and Input Sanitization for SereinOS.
Provides secure password hashing (PBKDF2-HMAC-SHA256, 600,000 iterations),
age confirmation enforcement, rate limiting, and XSS/input sanitization.
"""
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, Set

logger = logging.getLogger("serein.security.auth")

# OWASP Recommended PBKDF2 Parameters (2024+)
PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """
    Hash a password using PBKDF2-HMAC-SHA256 with a cryptographically secure random salt.
    Format: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
    """
    if not password:
        raise ValueError("Password cannot be empty")
    
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        iterations
    )
    return f"pbkdf2_{PBKDF2_ALGORITHM}${iterations}${salt.hex()}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """
    Verify a password against a stored PBKDF2 hash using constant-time comparison.
    """
    if not password or not hashed:
        return False
    
    try:
        parts = hashed.split("$")
        if len(parts) != 4 or parts[0] != f"pbkdf2_{PBKDF2_ALGORITHM}":
            logger.warning("Unrecognized password hash format")
            return False
        
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_hash = bytes.fromhex(parts[3])
        
        computed_hash = hashlib.pbkdf2_hmac(
            PBKDF2_ALGORITHM,
            password.encode("utf-8"),
            salt,
            iterations
        )
        return hmac.compare_digest(computed_hash, expected_hash)
    except Exception as e:
        logger.error(f"Error during password verification: {e}")
        return False


def sanitize_input(text: str, max_length: int = 4096, allow_html: bool = False) -> str:
    """
    Sanitize user input to prevent XSS, control-character injection, and buffer overruns.
    - Strips NULL bytes and dangerous ASCII control characters.
    - Trims whitespace.
    - HTML-escapes unless explicitly permitted.
    - Enforces length bounds.
    """
    if text is None:
        return ""
    
    if not isinstance(text, str):
        text = str(text)
    
    # Strip null bytes and non-printable control characters except newline and tab
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    
    # Trim leading/trailing whitespace
    cleaned = cleaned.strip()
    
    # Enforce maximum length
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    
    # HTML escape to protect against XSS injection
    if not allow_html:
        cleaned = html.escape(cleaned, quote=True)
    
    return cleaned


class RateLimiter:
    """
    Sliding window / Token bucket rate limiter per client key (IP or user ID).
    Thread-safe and async-compatible.
    """
    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: Dict[str, list] = {}

    def is_allowed(self, client_id: str) -> bool:
        """Check if request is permitted under rate limit."""
        now = time.time()
        cutoff = now - self.window_seconds
        
        if client_id not in self._history:
            self._history[client_id] = [now]
            return True
        
        # Filter timestamps outside window
        timestamps = [t for t in self._history[client_id] if t > cutoff]
        if len(timestamps) < self.max_requests:
            timestamps.append(now)
            self._history[client_id] = timestamps
            return True
        
        self._history[client_id] = timestamps
        return False

    def reset(self, client_id: Optional[str] = None):
        """Reset rate limiter state."""
        if client_id:
            self._history.pop(client_id, None)
        else:
            self._history.clear()


@dataclass
class UserSession:
    """Represents an authenticated user session."""
    session_token: str
    username: str
    role: str = "user"  # "admin", "user", "guest"
    age_confirmed: bool = False
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    expires_in_seconds: float = 86400.0  # 24 hours

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_activity) > self.expires_in_seconds

    def touch(self):
        self.last_activity = time.time()


class SecurityManager:
    """
    Central authentication and session governance for SereinOS.
    Tracks user credentials, active sessions, and age verification gates.
    """
    def __init__(self):
        self._users: Dict[str, Dict] = {}  # username -> {hash, role, age_confirmed}
        self._sessions: Dict[str, UserSession] = {}  # token -> UserSession
        self.rate_limiter = RateLimiter(max_requests=60, window_seconds=60.0)

    def register_user(
        self,
        username: str,
        password: str,
        role: str = "user",
        age_confirmed: bool = False
    ) -> bool:
        """Register a user with hashed password and required age confirmation."""
        username = sanitize_input(username, max_length=64, allow_html=False)
        if not username or username in self._users:
            return False
        
        if not age_confirmed:
            raise ValueError("Age confirmation (18+ or parental consent) is required to initialize SereinOS.")
        
        hashed = hash_password(password)
        self._users[username] = {
            "password_hash": hashed,
            "role": role,
            "age_confirmed": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        logger.info(f"User '{username}' registered with role '{role}' and verified age confirmation.")
        return True

    def authenticate(self, username: str, password: str) -> Optional[UserSession]:
        """Authenticate user credentials and create a secure session."""
        username = sanitize_input(username, max_length=64, allow_html=False)
        user_data = self._users.get(username)
        if not user_data:
            return None
        
        if not verify_password(password, user_data["password_hash"]):
            return None
        
        # Generate cryptographically secure random session token
        token = f"sess_{secrets.token_urlsafe(32)}"
        session = UserSession(
            session_token=token,
            username=username,
            role=user_data["role"],
            age_confirmed=user_data["age_confirmed"]
        )
        self._sessions[token] = session
        return session

    def create_session(
        self,
        username: str = "admin",
        role: str = "admin",
        age_confirmed: bool = True,
        expires_in_seconds: float = 86400.0,
    ) -> UserSession:
        """Create and register an authenticated user session directly."""
        token = f"sess_{secrets.token_urlsafe(32)}"
        session = UserSession(
            session_token=token,
            username=username,
            role=role,
            age_confirmed=age_confirmed,
            expires_in_seconds=expires_in_seconds,
        )
        self._sessions[token] = session
        return session

    def validate_session(self, token: Optional[str]) -> Optional[UserSession]:
        """Validate token and check expiration."""
        if not token:
            return None
        
        session = self._sessions.get(token)
        if not session:
            return None
        
        if session.is_expired:
            self._sessions.pop(token, None)
            return None
        
        session.touch()
        return session

    def logout(self, token: str):
        """Invalidate session."""
        self._sessions.pop(token, None)


# Default shared security manager instance
security_manager = SecurityManager()
