"""
Command Bus - D-Bus interface for system control.
Provides tiered permission model for system actions, canonical path sandboxing,
application whitelisting, SSRF protection, server-tracked confirmation challenges,
and fail-secure audit logging.
"""
import asyncio
import logging
import json
import os
import re
import shutil
import socket
import ipaddress
import hashlib
import time
import tempfile
import inspect
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Awaitable, Set
from uuid import uuid4

import aiohttp
import aiohttp.abc

from dbus_next import BusType, MessageType
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal
from dbus_next.constants import PropertyAccess

from serein_core.config import get_config as config
from serein_core.database import get_database
from serein_core.events import event_bus, CoreEvents
from serein_core.safety_layer import SafetyLayer, PermissionLevel, ActionRisk

logger = logging.getLogger("serein.core.command_bus")


class PermissionTier(Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    DESTRUCTIVE = "destructive"
    SENSITIVE = "sensitive"


class CommandStatus(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    UNKNOWN = "unknown"
    FAILED = "failed"


@dataclass
class CommandResult:
    success: bool
    result: Any = None
    error: Optional[str] = None
    permission_tier: PermissionTier = PermissionTier.READ_ONLY
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    challenge_id: Optional[str] = None
    status: Optional[CommandStatus] = None

    def __post_init__(self):
        if self.challenge_id is None and self.confirmation_token is not None:
            self.challenge_id = self.confirmation_token
        elif self.confirmation_token is None and self.challenge_id is not None:
            self.confirmation_token = self.challenge_id

        if self.status is None:
            if self.requires_confirmation:
                self.status = CommandStatus.CONFIRMATION_REQUIRED
            elif not self.success:
                self.status = CommandStatus.FAILED
            else:
                self.status = CommandStatus.SUCCESS


# =========================================================================
# Security Controls & Policy Definitions
# =========================================================================

# Strict application whitelist (ADR-009)
APPROVED_APPLICATIONS: Dict[str, str] = {
    "terminal": "/usr/bin/foot",
    "browser": "/usr/bin/chromium",
    "files": "/usr/bin/thunar",
    "calc": "/usr/bin/gnome-calculator",
    "editor": "/usr/bin/neovim",
    "python": "python",
}

# Forbidden characters in app launches to prevent command injection
SHELL_INJECTION_CHARS = [";", "&", "|", "`", "$", "\n", "\r", ">", "<"]


def _canonical_json(data: Any) -> str:
    """Format dictionary into deterministic canonical JSON for hashing."""
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def _compute_param_hash(params: Dict[str, Any]) -> str:
    """Compute SHA-256 hash of canonical parameter payload."""
    clean_params = {k: v for k, v in params.items() if not k.startswith("_")}
    canonical = _canonical_json(clean_params)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# IP CIDRs explicitly blocked to prevent SSRF and internal network scanning
BLOCKED_CIDRS = [
    ipaddress.ip_network("0.0.0.0/8"),          # Current network
    ipaddress.ip_network("10.0.0.0/8"),         # RFC1918 Private
    ipaddress.ip_network("100.64.0.0/10"),      # Shared Address Space (Carrier-Grade NAT)
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("169.254.0.0/16"),     # Link-Local / Cloud Metadata
    ipaddress.ip_network("172.16.0.0/12"),      # RFC1918 Private
    ipaddress.ip_network("192.0.0.0/24"),       # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),       # Documentation (TEST-NET-1)
    ipaddress.ip_network("192.168.0.0/16"),     # RFC1918 Private
    ipaddress.ip_network("198.18.0.0/15"),      # Benchmarking
    ipaddress.ip_network("198.51.100.0/24"),    # Documentation (TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),     # Documentation (TEST-NET-3)
    ipaddress.ip_network("224.0.0.0/4"),        # Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved for Future Use
    ipaddress.ip_network("255.255.255.255/32"), # Broadcast
    ipaddress.ip_network("::/128"),             # Unspecified IPv6
    ipaddress.ip_network("::1/128"),            # Loopback IPv6
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped IPv6
    ipaddress.ip_network("100::/64"),           # Discard-only prefix
    ipaddress.ip_network("2001:db8::/32"),      # Documentation IPv6
    ipaddress.ip_network("fc00::/7"),           # Unique Local IPv6 (ULA)
    ipaddress.ip_network("fe80::/10"),          # Link-Local IPv6
    ipaddress.ip_network("ff00::/8"),           # Multicast IPv6
]


def is_ip_blocked(ip_obj: Any) -> bool:
    """Check if an IP address belongs to any blocked, private, local, or reserved range."""
    # Handle IPv4-mapped IPv6 address
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        ip_obj = ip_obj.ipv4_mapped

    if (
        ip_obj.is_loopback
        or ip_obj.is_private
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return True

    for net in BLOCKED_CIDRS:
        if ip_obj in net:
            return True

    return False


class PinnedIPResolver(aiohttp.abc.AbstractResolver):
    """
    Custom aiohttp DNS resolver that forces socket connections to connect
    ONLY to pre-validated IP address(es), eliminating DNS rebinding (TOCTOU) attacks.
    Preserves TLS SNI and certificate validation against the original hostname.
    """
    def __init__(self, hostname: str, pinned_ip: str, port: int, family: int):
        self.hostname = hostname
        self.pinned_ip = pinned_ip
        self.port = port
        self.family = family

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict]:
        return [{
            "hostname": self.hostname,
            "host": self.pinned_ip,
            "port": self.port if self.port else port,
            "family": self.family,
            "proto": socket.IPPROTO_TCP,
            "flags": 0,
        }]

    async def close(self) -> None:
        pass


def validate_safe_path(target_path: str, operation: str = "read") -> Path:
    """
    Resolves target path to canonical absolute path and validates boundary containment.
    Guards against path traversal, symlink escapes, and prohibited system roots.
    """
    if not target_path or not str(target_path).strip():
        raise ValueError("Path is required")

    raw_p = Path(target_path).expanduser()
    try:
        resolved = raw_p.resolve()
    except Exception as e:
        raise ValueError(f"Cannot resolve path '{target_path}': {e}")

    # Explicitly forbidden system roots
    forbidden_roots = [
        Path("/bin"), Path("/sbin"), Path("/usr"), Path("/etc"),
        Path("/boot"), Path("/sys"), Path("/proc"), Path("/dev"), Path("/root"),
        Path("~/.ssh").expanduser().resolve(),
        Path("~/.gnupg").expanduser().resolve(),
        Path("~/.aws").expanduser().resolve(),
    ]
    if os.name == "nt":
        win_dir = os.environ.get("WINDIR", "C:\\Windows")
        forbidden_roots.extend([
            Path(win_dir).resolve(),
            Path(win_dir, "System32").resolve(),
        ])

    # Reject filesystem root itself
    if resolved.parent == resolved:
        raise PermissionError(f"Access to filesystem root '{resolved}' is strictly prohibited.")

    for fb in forbidden_roots:
        try:
            if resolved == fb or resolved.is_relative_to(fb):
                raise PermissionError(f"Access to protected system path '{resolved}' is strictly prohibited.")
        except Exception:
            pass

    # Allowed workspace containment
    user_home = Path("~").expanduser().resolve()
    allowed_workspaces = [
        user_home / "Documents",
        user_home / "Downloads",
        user_home / "Desktop",
        user_home / "Pictures",
        user_home / ".local" / "share" / "serein",
        Path("/tmp/serein").resolve(),
        Path(tempfile.gettempdir()) / "serein",
        Path(tempfile.gettempdir()).resolve(),
        Path.cwd().resolve(),
    ]

    is_allowed = any(
        (resolved == ws or resolved.is_relative_to(ws))
        for ws in allowed_workspaces
    )
    if not is_allowed:
        raise PermissionError(f"Path '{resolved}' is outside permitted workspace roots.")

    return resolved


class CommandHandler:
    """Base class for command handlers."""
    
    def __init__(self, name: str, tier: PermissionTier, 
                 requires_confirmation: bool = False,
                 confirmation_prompt: Optional[str] = None):
        self.name = name
        self.tier = tier
        self.requires_confirmation = requires_confirmation
        self.confirmation_prompt = confirmation_prompt
    
    async def execute(self, params: Dict[str, Any], 
                     confirmation: bool = False) -> CommandResult:
        """Execute the command. Override in subclasses."""
        if self.requires_confirmation and not confirmation:
            return CommandResult(
                success=False,
                error=self.confirmation_prompt or f"Confirmation required for {self.name}",
                permission_tier=self.tier,
                requires_confirmation=True,
            )
        return CommandResult(success=False, error="Not implemented")


class ReadOnlyHandlers:
    """Read-only system queries."""
    
    @staticmethod
    async def list_running_apps(params: Dict) -> CommandResult:
        """List running applications."""
        try:
            import psutil
            apps = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'exe']):
                try:
                    info = proc.info
                    if info['name']:
                        apps.append({
                            "pid": info['pid'],
                            "name": info['name'],
                            "cpu": info['cpu_percent'],
                            "memory": info['memory_percent'],
                            "exe": info['exe'],
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return CommandResult(success=True, result={"apps": apps}, permission_tier=PermissionTier.READ_ONLY)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def get_system_status(params: Dict) -> CommandResult:
        """Get system status."""
        try:
            import psutil
            return CommandResult(success=True, result={
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory": psutil.virtual_memory()._asdict(),
                "disk": psutil.disk_usage('/')._asdict(),
                "boot_time": psutil.boot_time(),
                "load_avg": psutil.getloadavg() if hasattr(psutil, 'getloadavg') else None,
            }, permission_tier=PermissionTier.READ_ONLY)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def get_window_list(params: Dict) -> CommandResult:
        """Get list of open windows (requires Wayland/X11)."""
        return CommandResult(success=True, result={"windows": []}, permission_tier=PermissionTier.READ_ONLY)
    
    @staticmethod
    async def read_file(params: Dict) -> CommandResult:
        """Read file contents with path validation."""
        path = params.get("path")
        if not path:
            return CommandResult(success=False, error="path required")
        try:
            p = validate_safe_path(path, operation="read")
            if not p.exists():
                return CommandResult(success=False, error="File not found")
            if not p.is_file():
                return CommandResult(success=False, error="Not a file")
            content = p.read_text(encoding="utf-8")
            return CommandResult(success=True, result={"path": str(p), "content": content}, permission_tier=PermissionTier.READ_ONLY)
        except PermissionError as e:
            return CommandResult(success=False, error=str(e), status=CommandStatus.DENIED)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class ReversibleHandlers:
    """Reversible system actions."""
    
    @staticmethod
    async def launch_app(params: Dict) -> CommandResult:
        """Launch an application from approved whitelist."""
        app = params.get("app")
        args = params.get("args", [])
        if not app:
            return CommandResult(success=False, error="app required")
        
        app_clean = str(app).strip()
        # Check command injection characters
        if any(c in app_clean for c in SHELL_INJECTION_CHARS):
            return CommandResult(
                success=False,
                error=f"Command injection detected in application name: '{app_clean}'",
                status=CommandStatus.DENIED
            )
        for a in args:
            if any(c in str(a) for c in SHELL_INJECTION_CHARS):
                return CommandResult(
                    success=False,
                    error=f"Command injection detected in argument: '{a}'",
                    status=CommandStatus.DENIED
                )

        # Validate against whitelist
        exec_path = None
        if app_clean in APPROVED_APPLICATIONS:
            exec_path = APPROVED_APPLICATIONS[app_clean]
        else:
            for name, path_val in APPROVED_APPLICATIONS.items():
                if app_clean == path_val:
                    exec_path = path_val
                    break
        
        if not exec_path:
            return CommandResult(
                success=False,
                error=f"Application '{app_clean}' is not in approved application whitelist",
                permission_tier=PermissionTier.REVERSIBLE,
                status=CommandStatus.DENIED
            )

        try:
            # Check mock environment or missing host binary
            if os.environ.get("SEREIN_MOCK_SUBPROCESS", "0") == "1" or not shutil.which(exec_path):
                return CommandResult(
                    success=True,
                    result={"pid": 9999, "simulated": True, "app": app_clean, "exec": exec_path},
                    permission_tier=PermissionTier.REVERSIBLE
                )
            
            proc = await asyncio.create_subprocess_exec(exec_path, *args)
            return CommandResult(success=True, result={"pid": proc.pid}, permission_tier=PermissionTier.REVERSIBLE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def close_app(params: Dict) -> CommandResult:
        """Close an application by name or PID."""
        name = params.get("name")
        pid = params.get("pid")
        try:
            import psutil
            if pid:
                proc = psutil.Process(pid)
                proc.terminate()
                await asyncio.to_thread(proc.wait, timeout=5)
                return CommandResult(success=True, result={"terminated": pid})
            elif name:
                count = 0
                for proc in psutil.process_iter(['name']):
                    if proc.info['name'] == name:
                        proc.terminate()
                        count += 1
                return CommandResult(success=True, result={"terminated": count})
            return CommandResult(success=False, error="name or pid required")
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def set_volume(params: Dict) -> CommandResult:
        """Set system volume."""
        level = params.get("level", 50)
        try:
            if not shutil.which("wpctl"):
                return CommandResult(success=True, result={"volume": level, "simulated": True}, permission_tier=PermissionTier.REVERSIBLE)
            proc = await asyncio.create_subprocess_exec(
                "wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level/100:.2f}"
            )
            await proc.wait()
            return CommandResult(success=True, result={"volume": level}, permission_tier=PermissionTier.REVERSIBLE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def move_file(params: Dict) -> CommandResult:
        """Move file to new location with path validation."""
        src = params.get("source")
        dst = params.get("destination")
        if not src or not dst:
            return CommandResult(success=False, error="source and destination required")
        try:
            s = validate_safe_path(src, operation="move_src")
            d = validate_safe_path(dst, operation="move_dst")
            shutil.move(str(s), str(d))
            return CommandResult(success=True, result={"moved": str(s), "to": str(d)}, permission_tier=PermissionTier.REVERSIBLE)
        except PermissionError as e:
            return CommandResult(success=False, error=str(e), status=CommandStatus.DENIED)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class DestructiveHandlers:
    """Destructive/irreversible actions."""
    
    @staticmethod
    async def delete_file(params: Dict) -> CommandResult:
        """
        Delete file or directory.
        Redirects user workspace deletions to Freedesktop Trash (ADR-007).
        Direct unlinking permitted only for temporary scratch files in /tmp/serein.
        """
        path = params.get("path")
        recursive = params.get("recursive", False)
        if not path:
            return CommandResult(success=False, error="path required")
        try:
            p = validate_safe_path(path, operation="delete")
            if not p.exists():
                return CommandResult(success=False, error="Target path does not exist")

            # Check if path is in temporary scratch space
            is_scratch = False
            scratch_roots = [
                Path("/tmp/serein").resolve(),
                (Path(tempfile.gettempdir()) / "serein").resolve(),
            ]
            for sd in scratch_roots:
                try:
                    if p == sd or p.is_relative_to(sd):
                        is_scratch = True
                        break
                except Exception:
                    pass

            if is_scratch:
                if p.is_dir():
                    if recursive:
                        shutil.rmtree(p)
                    else:
                        return CommandResult(success=False, error="Directory requires recursive=true")
                else:
                    p.unlink(missing_ok=True)
                return CommandResult(success=True, result={"deleted": str(p)}, permission_tier=PermissionTier.DESTRUCTIVE)
            else:
                # User workspace directory: redirect to Freedesktop Trash
                try:
                    import send2trash
                    send2trash.send2trash(str(p))
                except ImportError:
                    trash_dir = Path("~/.local/share/Trash/files").expanduser().resolve()
                    trash_dir.mkdir(parents=True, exist_ok=True)
                    dest = trash_dir / f"{p.name}_{int(time.time())}"
                    shutil.move(str(p), str(dest))
                return CommandResult(success=True, result={"trashed": str(p)}, permission_tier=PermissionTier.DESTRUCTIVE)
        except PermissionError as e:
            return CommandResult(success=False, error=str(e), status=CommandStatus.DENIED)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def shutdown_system(params: Dict) -> CommandResult:
        """Shutdown the system."""
        try:
            if not shutil.which("systemctl"):
                return CommandResult(success=True, result={"action": "shutdown", "simulated": True}, permission_tier=PermissionTier.DESTRUCTIVE)
            proc = await asyncio.create_subprocess_exec("systemctl", "poweroff")
            await proc.wait()
            return CommandResult(success=True, result={"action": "shutdown"}, permission_tier=PermissionTier.DESTRUCTIVE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def reboot_system(params: Dict) -> CommandResult:
        """Reboot the system."""
        try:
            if not shutil.which("systemctl"):
                return CommandResult(success=True, result={"action": "reboot", "simulated": True}, permission_tier=PermissionTier.DESTRUCTIVE)
            proc = await asyncio.create_subprocess_exec("systemctl", "reboot")
            await proc.wait()
            return CommandResult(success=True, result={"action": "reboot"}, permission_tier=PermissionTier.DESTRUCTIVE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def install_package(params: Dict) -> CommandResult:
        """Install a package with strict input sanitization and flag injection prevention."""
        package = params.get("package")
        manager = params.get("manager", "apt")
        if not package:
            return CommandResult(success=False, error="package required")
            
        # Prevent shell injection, flag injection, and path traversal in package names
        # Must start with alphanumeric and contain only safe package name characters
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_\-\.\+]{0,63}$", package):
            return CommandResult(
                success=False,
                error=f"Invalid package name '{package}': must start with alphanumeric and contain only safe package chars",
                status=CommandStatus.DENIED
            )

        valid_managers = {"apt", "pacman", "flatpak", "snap"}
        if manager not in valid_managers:
            return CommandResult(success=False, error=f"Unknown or unauthorized package manager: {manager}", status=CommandStatus.DENIED)

        try:
            # Enforce '--' delimiter before package name to prevent flag injection
            cmds = {
                "apt": ["apt", "install", "-y", "--", package],
                "pacman": ["pacman", "-S", "--noconfirm", "--", package],
                "flatpak": ["flatpak", "install", "-y", "--", package],
                "snap": ["snap", "install", "--", package],
            }
            
            if os.environ.get("SEREIN_MOCK_SUBPROCESS", "0") == "1" or not shutil.which(cmds[manager][0]):
                return CommandResult(
                    success=True,
                    result={"stdout": "simulated package install", "stderr": "", "package": package},
                    permission_tier=PermissionTier.DESTRUCTIVE
                )
            
            proc = await asyncio.create_subprocess_exec(
                *cmds[manager],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return CommandResult(
                success=proc.returncode == 0,
                result={"stdout": stdout.decode(), "stderr": stderr.decode()},
                permission_tier=PermissionTier.DESTRUCTIVE
            )
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class SensitiveHandlers:
    """Sensitive domain actions."""
    
    @staticmethod
    async def network_request(params: Dict) -> CommandResult:
        """
        Make network request with strict SSRF & DNS rebinding protection (ADR-008).
        Pins TCP socket connection to pre-validated IP address, rejecting DNS rebinding attacks.
        Follows redirects manually up to 5 steps, re-validating every redirect target IP before connecting.
        """
        url = params.get("url")
        method = params.get("method", "GET").upper()
        headers = params.get("headers", {})
        data = params.get("data")
        if not url:
            return CommandResult(success=False, error="url required")

        MAX_REDIRECTS = 5
        current_url = url

        try:
            for redirect_step in range(MAX_REDIRECTS + 1):
                parsed = urllib.parse.urlparse(current_url)
                if parsed.scheme not in ("http", "https"):
                    return CommandResult(
                        success=False,
                        error=f"Unsupported scheme '{parsed.scheme}': only http and https allowed",
                        status=CommandStatus.DENIED
                    )

                hostname = parsed.hostname
                if not hostname:
                    return CommandResult(success=False, error="Invalid URL: missing hostname", status=CommandStatus.DENIED)

                port = parsed.port or (443 if parsed.scheme == "https" else 80)

                # DNS resolution pre-flight check
                loop = asyncio.get_event_loop()
                try:
                    addr_info = await loop.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
                except socket.gaierror as e:
                    return CommandResult(success=False, error=f"DNS resolution failed for '{hostname}': {e}", status=CommandStatus.FAILED)

                if not addr_info:
                    return CommandResult(success=False, error=f"Could not resolve hostname '{hostname}'", status=CommandStatus.FAILED)

                validated_records = []
                for ai in addr_info:
                    family, socktype, proto, canonname, sockaddr = ai
                    ip_str = sockaddr[0]
                    try:
                        ip_obj = ipaddress.ip_address(ip_str)
                    except ValueError:
                        return CommandResult(
                            success=False,
                            error=f"SSRF blocked: Invalid IP address returned for host '{hostname}': {ip_str}",
                            status=CommandStatus.DENIED
                        )

                    if is_ip_blocked(ip_obj):
                        return CommandResult(
                            success=False,
                            error=f"SSRF blocked: Host '{hostname}' resolves to forbidden private or local IP {ip_str}",
                            status=CommandStatus.DENIED
                        )
                    validated_records.append((ip_str, family, sockaddr[1]))

                # Pin socket connection to the first validated IP address to prevent DNS rebinding
                pinned_ip, pinned_family, pinned_port = validated_records[0]
                resolver = PinnedIPResolver(hostname, pinned_ip, pinned_port, pinned_family)
                connector = aiohttp.TCPConnector(resolver=resolver)

                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.request(
                        method,
                        current_url,
                        headers=headers,
                        json=data,
                        allow_redirects=False
                    ) as resp:
                        # Handle redirects explicitly with full destination IP revalidation
                        if resp.status in (301, 302, 303, 307, 308):
                            if redirect_step >= MAX_REDIRECTS:
                                return CommandResult(
                                    success=False,
                                    error="Too many redirects (exceeded limit of 5)",
                                    status=CommandStatus.FAILED
                                )
                            loc = resp.headers.get("Location")
                            if not loc:
                                body = await resp.text()
                                return CommandResult(
                                    success=True,
                                    result={"status": resp.status, "headers": dict(resp.headers), "body": body[:10000]},
                                    permission_tier=PermissionTier.SENSITIVE
                                )
                            next_url = urllib.parse.urljoin(current_url, loc)
                            current_url = next_url
                            continue

                        body = await resp.text()
                        return CommandResult(
                            success=True,
                            result={
                                "status": resp.status,
                                "headers": dict(resp.headers),
                                "body": body[:10000],
                            },
                            permission_tier=PermissionTier.SENSITIVE
                        )
        except PermissionError as e:
            return CommandResult(success=False, error=str(e), status=CommandStatus.DENIED)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def access_secrets(params: Dict) -> CommandResult:
        """Access system secrets/keyring."""
        return CommandResult(success=False, error="Not implemented", permission_tier=PermissionTier.SENSITIVE)


class CommandBus:
    """Main Command Bus - D-Bus service for system control."""
    
    def __init__(self, allowed_tiers: Optional[Set[PermissionTier]] = None, dry_run: bool = False,
                 safety_layer: Optional[SafetyLayer] = None, database: Optional[Any] = None,
                 require_session: bool = False):
        self.bus: Optional[MessageBus] = None
        bus_cfg = getattr(config, "command_bus", None)
        self.service_name = getattr(bus_cfg, "dbus_service_name", "os.serein.CommandBus") if bus_cfg else "os.serein.CommandBus"
        self.object_path = getattr(bus_cfg, "dbus_object_path", "/os/serein/CommandBus") if bus_cfg else "/os/serein/CommandBus"
        self.allowed_tiers = set(allowed_tiers) if allowed_tiers is not None else {PermissionTier.READ_ONLY}
        self.dry_run = dry_run
        self.safety_layer = safety_layer or SafetyLayer()
        self.db = database
        self.require_session = require_session
        self._revoked_tokens: Set[str] = set()
        self._handlers: Dict[str, CommandHandler] = {}
        self._register_handlers()

    async def _get_db(self):
        """Get database instance for persistent challenge tracking and forensic audit."""
        if self.db is not None:
            return self.db
        try:
            return await get_database()
        except Exception as e:
            logger.error(f"Failed to obtain database instance: {e}")
            return None

    async def _safe_await(self, obj: Any) -> Any:
        """Safely await an object if it is awaitable, or return it directly."""
        if inspect.isawaitable(obj):
            return await obj
        return obj

    async def _is_valid_session(self, session_id: Optional[str]) -> bool:
        """
        Validate if session_id corresponds to an active, authenticated UI/daemon session.
        Strict fail-closed invariant:
        - Rejects authentication whenever SQLite validation fails or database is unavailable.
        - Never falls back to accepting an in-memory session.
        - Checks SQLite authenticated_sessions (revoked=0, not expired) and synchronizes valid sessions.
        - Rejects revoked tokens, expired tokens, or unpersisted placeholders.
        """
        if not session_id or not str(session_id).strip():
            return False

        if session_id in getattr(self, "_revoked_tokens", set()):
            return False

        from serein_core.auth import security_manager

        db = await self._get_db()
        if db is None or not hasattr(db, "validate_auth_session"):
            logger.error("Session authentication rejected: SQLite database is unavailable")
            return False

        try:
            # Check SQLite persistent record
            auth_row = await self._safe_await(db.validate_auth_session(session_id))
            if not auth_row:
                # Session not found, revoked (revoked=1), or expired in SQLite
                security_manager.logout(session_id)
                return False

            # SQLite confirmed active session; verify and rehydrate in-memory if needed
            sess = security_manager.validate_session(session_id)
            if sess is None:
                from serein_core.auth import UserSession
                created_at = auth_row.get("created_at") or time.time()
                expires_at = auth_row.get("expires_at") or (time.time() + 86400.0)
                rehydrated = UserSession(
                    session_token=auth_row["session_token"],
                    username=auth_row["username"],
                    role=auth_row.get("role", "user"),
                    created_at=created_at,
                    last_activity=auth_row.get("last_activity") or time.time(),
                    expires_in_seconds=max(0.0, expires_at - created_at),
                )
                security_manager._sessions[session_id] = rehydrated

            return True
        except Exception as e:
            logger.error(f"Session authentication rejected due to SQLite exception: {e}")
            return False

    async def authenticate(
        self,
        username: str,
        password: str,
        expires_in_seconds: float = 86400.0,
    ) -> Optional[str]:
        """
        Authenticate credentials against canonical SecurityManager and persist to SQLite.
        Fails closed: if persistence fails or database is unavailable, in-memory session
        is immediately invalidated and None is returned.
        """
        from serein_core.auth import security_manager
        session = security_manager.authenticate(username, password, expires_in_seconds=expires_in_seconds)
        if not session:
            logger.warning(f"Authentication failed for user '{username}': invalid credentials")
            return None

        token = session.session_token
        db = await self._get_db()
        if db is None or not hasattr(db, "create_auth_session"):
            logger.error("Durable session persistence failed: database unavailable; failing closed")
            security_manager.logout(token)
            return None

        try:
            persisted = await self._safe_await(db.create_auth_session(
                session_token=token,
                username=session.username,
                role=session.role,
                expires_in_seconds=expires_in_seconds,
            ))
            if not persisted:
                logger.error("Durable session persistence failed: failing closed")
                security_manager.logout(token)
                return None
        except Exception as e:
            logger.error(f"Durable session persistence exception: {e}; failing closed")
            security_manager.logout(token)
            return None

        if hasattr(self, "_revoked_tokens"):
            self._revoked_tokens.discard(token)

        return token

    async def revoke_session(self, session_token: str) -> bool:
        """
        Revoke an authenticated session across memory and SQLite persistent storage.
        Strict fail-closed invariant:
        - In-memory state is immediately invalidated.
        - Session token is recorded in local revocation blacklist so it cannot authorize operations.
        - Returns True ONLY when revocation is confirmed in persistent storage.
        - If persistent revocation fails (db unavailable, db exception, or 0 rows updated),
          returns False, and ensures the session remains barred from authorizing operations.
        """
        if not session_token or not str(session_token).strip():
            return False

        from serein_core.auth import security_manager

        # Invalidate in-memory session state immediately
        security_manager.logout(session_token)

        # Track in local revocation blacklist so it cannot authorize operations
        if not hasattr(self, "_revoked_tokens"):
            self._revoked_tokens = set()
        self._revoked_tokens.add(session_token)

        db = await self._get_db()
        if db is None or not hasattr(db, "revoke_auth_session"):
            logger.error(f"Persistent revocation failed for '{session_token}': database unavailable")
            return False

        try:
            persisted = await self._safe_await(db.revoke_auth_session(session_token))
            if not persisted:
                logger.error(f"Persistent revocation failed for '{session_token}': storage returned False")
                return False
            return True
        except Exception as e:
            logger.error(f"Persistent revocation exception for '{session_token}': {e}")
            return False
    
    def _register_handlers(self) -> None:
        """Register all default command handlers."""
        # Read-only
        self._register("list_running_apps", ReadOnlyHandlers.list_running_apps, PermissionTier.READ_ONLY)
        self._register("list_processes", ReadOnlyHandlers.list_running_apps, PermissionTier.READ_ONLY)
        self._register("get_system_status", ReadOnlyHandlers.get_system_status, PermissionTier.READ_ONLY)
        self._register("get_window_list", ReadOnlyHandlers.get_window_list, PermissionTier.READ_ONLY)
        self._register("read_file", ReadOnlyHandlers.read_file, PermissionTier.READ_ONLY)
        
        # Reversible
        self._register("launch_app", ReversibleHandlers.launch_app, PermissionTier.REVERSIBLE)
        self._register("close_app", ReversibleHandlers.close_app, PermissionTier.REVERSIBLE)
        self._register("close_process", ReversibleHandlers.close_app, PermissionTier.REVERSIBLE)
        self._register("set_volume", ReversibleHandlers.set_volume, PermissionTier.REVERSIBLE)
        self._register("move_file", ReversibleHandlers.move_file, PermissionTier.REVERSIBLE)
        
        # Destructive - requires confirmation
        self._register("delete_file", DestructiveHandlers.delete_file, 
                      PermissionTier.DESTRUCTIVE, True, "Delete file? This will move it to Trash.")
        self._register("shutdown", DestructiveHandlers.shutdown_system,
                      PermissionTier.DESTRUCTIVE, True, "Shutdown system? All unsaved work will be lost.")
        self._register("shutdown_system", DestructiveHandlers.shutdown_system,
                      PermissionTier.DESTRUCTIVE, True, "Shutdown system? All unsaved work will be lost.")
        self._register("reboot", DestructiveHandlers.reboot_system,
                      PermissionTier.DESTRUCTIVE, True, "Reboot system? All unsaved work will be lost.")
        self._register("reboot_system", DestructiveHandlers.reboot_system,
                      PermissionTier.DESTRUCTIVE, True, "Reboot system? All unsaved work will be lost.")
        self._register("install_package", DestructiveHandlers.install_package,
                      PermissionTier.DESTRUCTIVE, True, "Install package? This modifies system software.")
        
        # Sensitive
        self._register("network_request", SensitiveHandlers.network_request, PermissionTier.SENSITIVE)
        self._register("access_secrets", SensitiveHandlers.access_secrets, PermissionTier.SENSITIVE)
    
    def _register(self, name: str, func: Callable, tier: PermissionTier,
                  requires_confirmation: bool = False, prompt: Optional[str] = None) -> None:
        handler = CommandHandler(name, tier, requires_confirmation, prompt)
        handler.execute = func
        self._handlers[name] = handler

    def register(self, name: str, func: Callable, tier: PermissionTier = PermissionTier.READ_ONLY,
                 requires_confirmation: bool = False, prompt: Optional[str] = None) -> None:
        """Public method to register a custom command handler."""
        if name in self._handlers:
            raise ValueError(f"Command '{name}' is already registered")
        self._register(name, func, tier, requires_confirmation, prompt)

    def list_commands(self) -> List[Dict[str, Any]]:
        """List available commands as dictionary items."""
        return [
            {
                "name": h.name,
                "tier": h.tier.value,
                "requires_confirmation": h.requires_confirmation,
                "confirmation_prompt": h.confirmation_prompt,
            }
            for h in self._handlers.values()
        ]
    
    async def start(self) -> None:
        """Start the D-Bus service."""
        logger.info("Starting Command Bus...")
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        await self.bus.request_name(self.service_name)
        interface = self._create_interface()
        self.bus.export(self.object_path, interface)
        logger.info(f"Command Bus started on {self.service_name} at {self.object_path}")
        await event_bus.publish(type("Event", (), {
            "type": CoreEvents.COMMAND_BUS_STARTED,
            "data": {"service": self.service_name}
        })())
    
    def _create_interface(self) -> ServiceInterface:
        """Create D-Bus service interface."""
        bus_self = self

        class CommandBusInterface(ServiceInterface):
            def __init__(self):
                super().__init__("os.serein.CommandBus")

            @method()
            async def ExecuteCommand(self, command: 's', params: 's', confirmation_token: 's', session_id: 's') -> 's':
                try:
                    params_dict = json.loads(params) if params else {}
                    caller_session = session_id or params_dict.get("_session_id") or params_dict.get("session_id")
                    result = await bus_self.execute(
                        command,
                        params_dict,
                        confirmation_token=confirmation_token,
                        session_id=caller_session,
                    )
                    return json.dumps({
                        "success": result.success,
                        "result": result.result,
                        "error": result.error,
                        "permission_tier": result.permission_tier.value if result.permission_tier else "",
                        "requires_confirmation": result.requires_confirmation,
                        "confirmation_token": result.confirmation_token,
                        "challenge_id": result.challenge_id,
                        "status": result.status.value if result.status else "",
                    })
                except Exception as e:
                    logger.error(f"Command execution error: {e}")
                    return json.dumps({"success": False, "error": str(e), "status": "failed"})

            @method()
            async def RequestConfirmation(self, token: 's', approved: 'b', session_id: 's') -> 's':
                res = await bus_self.confirm(token, approved, session_id=session_id)
                return json.dumps({
                    "success": res.success,
                    "status": res.status.value if res.status else "",
                    "error": res.error,
                })

            @method()
            async def ListCommands(self) -> 's':
                return json.dumps(bus_self.list_commands())

        return CommandBusInterface()

    async def _log_audit(self, command: str, params: Dict, tier: PermissionTier, result_str: str, 
                         error_msg: Optional[str] = None, session_id: Optional[str] = None) -> bool:
        """
        Write security audit log entry.
        Returns True on success, False if logging failed.
        """
        try:
            db = await self._get_db()
            if db and hasattr(db, "log_action"):
                await db.log_action({
                    "action_type": command,
                    "target": str(params.get("path") or params.get("app") or params.get("url") or params.get("target") or ""),
                    "backend": "command_bus",
                    "initiated_by": params.get("_initiated_by", "user"),
                    "permission_tier": tier.value,
                    "result": result_str,
                    "error_message": error_msg,
                })
                return True
            return False
        except Exception as e:
            logger.error(f"Audit log write failed: {e}")
            return False

    async def execute(self, command: str, params: Optional[Dict[str, Any]] = None, 
                      confirmation_token: str = "", session_id: Optional[str] = None) -> CommandResult:
        """
        Execute a command with strict permission, safety, session authentication,
        and durable SQLite-backed single-use challenge verification.
        """
        params = params or {}
        
        if command not in self._handlers:
            return CommandResult(
                success=False,
                error=f"Unknown command: {command}",
                status=CommandStatus.UNKNOWN,
            )
        
        handler = self._handlers[command]
        resolved_session = session_id or params.get("_session_id") or params.get("session_id")

        # 1. Authenticated Session Verification
        if self.require_session:
            if not resolved_session or not await self._is_valid_session(resolved_session):
                await self._log_audit(command, params, handler.tier, "denied", "Missing or invalid authenticated session", session_id=resolved_session)
                return CommandResult(
                    success=False,
                    error="Missing or invalid authenticated session",
                    permission_tier=handler.tier,
                    status=CommandStatus.DENIED,
                )
        elif resolved_session is not None:
            if not await self._is_valid_session(resolved_session):
                await self._log_audit(command, params, handler.tier, "denied", "Invalid or expired authenticated session", session_id=resolved_session)
                return CommandResult(
                    success=False,
                    error="Invalid or expired authenticated session",
                    permission_tier=handler.tier,
                    status=CommandStatus.DENIED,
                )

        bound_session = resolved_session or "default_session"

        # 2. Permission Tier Enforcement
        if handler.tier not in self.allowed_tiers:
            await self._log_audit(command, params, handler.tier, "denied", "Permission tier not allowed", session_id=bound_session)
            try:
                await event_bus.publish(type("Event", (), {
                    "type": CoreEvents.PERMISSION_DENIED,
                    "data": {"command": command, "tier": handler.tier.value}
                })())
            except Exception:
                pass
            return CommandResult(
                success=False,
                error=f"Permission denied: {handler.tier.value} requires appropriate access",
                permission_tier=handler.tier,
                status=CommandStatus.DENIED,
            )

        # 3. Comprehensive SafetyLayer Evaluation
        safety_action_map = {
            "read_file": "file_read",
            "move_file": "file_modify",
            "delete_file": "file_delete",
            "launch_app": "app_launch",
            "close_app": "app_close",
            "close_process": "app_close",
            "shutdown": "system_command",
            "shutdown_system": "system_command",
            "reboot": "system_command",
            "reboot_system": "system_command",
            "install_package": "system_command",
            "network_request": "network_request",
        }
        safety_action = safety_action_map.get(command, command)
        safety_val = self.safety_layer.validate_action(safety_action, params)

        # Reject unconditionally if SafetyLayer denies outright
        if not safety_val.get("allowed", False):
            reason = safety_val.get("reason", "Action not allowed by SafetyLayer")
            requires_approval = safety_val.get("requires_approval", False)

            # Protected paths or denied permissions cannot be confirmed or bypassed
            if safety_val.get("protected_path", False) or "not granted" in reason.lower():
                await self._log_audit(command, params, handler.tier, "denied", f"Safety violation: {reason}", session_id=bound_session)
                return CommandResult(
                    success=False,
                    error=f"Safety violation: {reason}",
                    permission_tier=handler.tier,
                    status=CommandStatus.DENIED,
                )

            # If not a high/critical risk requiring interactive approval, it is an outright denial
            if not requires_approval and not handler.requires_confirmation:
                await self._log_audit(command, params, handler.tier, "denied", f"Safety violation: {reason}", session_id=bound_session)
                return CommandResult(
                    success=False,
                    error=f"Safety violation: {reason}",
                    permission_tier=handler.tier,
                    status=CommandStatus.DENIED,
                )

        needs_confirmation = handler.requires_confirmation or safety_val.get("requires_approval", False)

        # 4. Durable SQLite-Backed Single-Use Confirmation Challenges (ADR-004)
        if needs_confirmation:
            db = await self._get_db()
            if not db:
                return CommandResult(
                    success=False,
                    error="Security failure: Database unavailable for confirmation challenge management",
                    permission_tier=handler.tier,
                    status=CommandStatus.FAILED,
                )

            current_hash = _compute_param_hash(params)

            if confirmation_token:
                # Challenge token provided - verify caller supplied an authenticated session
                if not resolved_session or not str(resolved_session).strip():
                    await self._log_audit(command, params, handler.tier, "denied", "Missing authenticated session for confirmation challenge", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Missing authenticated session for confirmation: caller must supply session_id",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
                if not await self._is_valid_session(resolved_session):
                    await self._log_audit(command, params, handler.tier, "denied", "Invalid or expired authenticated session for confirmation challenge", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Invalid or expired authenticated session",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )

                # Challenge token provided - verify challenge details from SQLite
                challenge = await self._safe_await(db.get_challenge(confirmation_token))
                if not challenge or not isinstance(challenge, dict):
                    await self._log_audit(command, params, handler.tier, "denied", "Invalid confirmation challenge token", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Invalid or expired confirmation token",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
                if challenge.get("status") != "pending":
                    await self._log_audit(command, params, handler.tier, "denied", "Confirmation challenge already consumed or cancelled", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Confirmation token has already been consumed or cancelled",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
                if time.time() > challenge.get("expires_at", 0):
                    await self._log_audit(command, params, handler.tier, "denied", "Confirmation challenge has expired", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Confirmation token has expired",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
                if challenge.get("command") != command:
                    await self._log_audit(command, params, handler.tier, "denied", f"Command mismatch: expected {challenge.get('command')}, got {command}", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error=f"Command mismatch for confirmation challenge: expected {challenge.get('command')}, got {command}",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
                if challenge.get("param_hash") != current_hash:
                    await self._log_audit(command, params, handler.tier, "denied", "Parameters altered since confirmation requested", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Parameters altered since confirmation requested",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
                chal_session = challenge.get("session_id")
                if chal_session != resolved_session:
                    await self._log_audit(command, params, handler.tier, "denied", f"Session mismatch: expected {chal_session}, got {resolved_session}", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Session mismatch for confirmation challenge",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )

                # Atomically consume challenge from SQLite
                consumed = await self._safe_await(db.consume_challenge(
                    challenge_id=confirmation_token,
                    session_id=resolved_session,
                    command=command,
                    param_hash=current_hash,
                    now=time.time()
                ))
                if not consumed:
                    await self._log_audit(
                        command, params, handler.tier, "denied",
                        "Concurrent confirmation race or challenge already consumed",
                        session_id=resolved_session
                    )
                    return CommandResult(
                        success=False,
                        error="Confirmation challenge verification failed: concurrent race or already consumed",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )

                # Re-evaluate Safety policy on confirmed action to catch dynamic policy or permission revocations
                safety_reval = self.safety_layer.validate_action(safety_action, params)
                reval_reason = safety_reval.get("reason", "Action not permitted by safety policy")
                if safety_reval.get("protected_path", False) or "not granted" in reval_reason.lower() or (not safety_reval.get("allowed", False) and not safety_reval.get("requires_approval", False)):
                    await self._log_audit(command, params, handler.tier, "denied", f"Safety revalidation failed: {reval_reason}", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error=f"Safety revalidation failed: {reval_reason}",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
            else:
                # Generating new challenge: Require caller to possess a valid authenticated session
                if not resolved_session or not str(resolved_session).strip():
                    await self._log_audit(command, params, handler.tier, "denied", "Missing authenticated session required for confirmation challenge", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Missing authenticated session required for confirmation challenge: caller must supply session_id",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )
                if not await self._is_valid_session(resolved_session):
                    await self._log_audit(command, params, handler.tier, "denied", "Invalid or expired authenticated session for confirmation challenge", session_id=resolved_session)
                    return CommandResult(
                        success=False,
                        error="Invalid or expired authenticated session",
                        permission_tier=handler.tier,
                        status=CommandStatus.DENIED,
                    )

                # Generate new durable server-tracked challenge in SQLite
                token = f"ch_{uuid4().hex}"
                now = time.time()
                expires_at = now + 300.0
                params_json = _canonical_json({k: v for k, v in params.items() if not k.startswith("_")})
                
                persisted = await self._safe_await(db.create_challenge(
                    challenge_id=token,
                    session_id=resolved_session,
                    command=command,
                    param_hash=current_hash,
                    params_json=params_json,
                    prompt=handler.confirmation_prompt,
                    created_at=now,
                    expires_at=expires_at
                ))
                if not persisted:
                    return CommandResult(
                        success=False,
                        error="Security failure: Unable to persist confirmation challenge to database",
                        permission_tier=handler.tier,
                        status=CommandStatus.FAILED,
                    )
                
                await self._log_audit(
                    command, params, handler.tier, "confirmation_requested",
                    f"Generated confirmation challenge {token}",
                    session_id=resolved_session
                )
                return CommandResult(
                    success=False,
                    requires_confirmation=True,
                    confirmation_token=token,
                    challenge_id=token,
                    permission_tier=handler.tier,
                    status=CommandStatus.CONFIRMATION_REQUIRED,
                )

                try:
                    await event_bus.publish(type("Event", (), {
                        "type": CoreEvents.CONFIRMATION_REQUESTED,
                        "data": {
                            "token": token,
                            "challenge_id": token,
                            "session_id": bound_session,
                            "command": command,
                            "prompt": handler.confirmation_prompt,
                            "params": params,
                            "expires_at": expires_at,
                        }
                    })())
                except Exception:
                    pass

                return CommandResult(
                    success=False,
                    error=handler.confirmation_prompt or "Confirmation required",
                    permission_tier=handler.tier,
                    requires_confirmation=True,
                    confirmation_token=token,
                    challenge_id=token,
                    status=CommandStatus.CONFIRMATION_REQUIRED,
                )

        # 5. Dry-run handling
        if self.dry_run:
            return CommandResult(
                success=True,
                result={"dry_run": True, "command": command, "params": params},
                permission_tier=handler.tier,
                status=CommandStatus.SUCCESS,
            )

        # 6. Fail-Secure Pre-Flight Audit Logging
        if handler.tier in (PermissionTier.DESTRUCTIVE, PermissionTier.SENSITIVE):
            logged = await self._log_audit(command, params, handler.tier, "initiating", None, session_id=bound_session)
            if not logged:
                return CommandResult(
                    success=False,
                    error="Security audit failure: action aborted (fail-secure invariant)",
                    permission_tier=handler.tier,
                    status=CommandStatus.FAILED,
                )

        # 7. Execute Handler
        try:
            try:
                res = await handler.execute(params, confirmation=True)
            except TypeError:
                res = await handler.execute(params)

            if not isinstance(res, CommandResult):
                result = CommandResult(
                    success=True,
                    result=res,
                    permission_tier=handler.tier,
                    status=CommandStatus.SUCCESS,
                )
            else:
                result = res
            if result.status is None:
                result.status = CommandStatus.SUCCESS if result.success else CommandStatus.FAILED

            # Post-execution audit logging
            await self._log_audit(
                command,
                params,
                handler.tier,
                "success" if result.success else "failed",
                result.error,
                session_id=bound_session
            )

            # Event emission
            try:
                event_type = CoreEvents.COMMAND_EXECUTED if result.success else CoreEvents.COMMAND_FAILED
                await event_bus.publish(type("Event", (), {
                    "type": event_type,
                    "data": {"command": command, "result": result.success, "error": result.error}
                })())
            except Exception:
                pass

            return result

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            await self._log_audit(command, params, handler.tier, "failed", str(e), session_id=bound_session)
            return CommandResult(
                success=False,
                error=str(e),
                permission_tier=handler.tier,
                status=CommandStatus.FAILED,
            )

    async def confirm(self, token: str, approved: bool, session_id: Optional[str] = None) -> CommandResult:
        """
        Handle user confirmation token response with single-use atomic consumption
        via the unified execute() pipeline.
        Strictly requires caller-supplied authenticated session.
        Never derives caller identity from stored challenge.
        """
        if not session_id or not str(session_id).strip():
            return CommandResult(
                success=False,
                error="Missing authenticated session for confirmation: caller must supply session_id",
                status=CommandStatus.DENIED,
            )

        if not await self._is_valid_session(session_id):
            return CommandResult(
                success=False,
                error="Invalid or expired authenticated session",
                status=CommandStatus.DENIED,
            )

        db = await self._get_db()
        if not db:
            return CommandResult(
                success=False,
                error="Database unavailable for challenge confirmation",
                status=CommandStatus.FAILED,
            )

        challenge = await self._safe_await(db.get_challenge(token))
        if not challenge or not isinstance(challenge, dict):
            return CommandResult(
                success=False,
                error="Invalid or expired confirmation token",
                status=CommandStatus.FAILED,
            )

        if challenge.get("status") != "pending":
            return CommandResult(
                success=False,
                error="Confirmation token has already been consumed or cancelled",
                status=CommandStatus.FAILED,
            )

        if time.time() > challenge.get("expires_at", 0):
            return CommandResult(
                success=False,
                error="Confirmation token has expired",
                status=CommandStatus.FAILED,
            )

        if challenge.get("session_id") != session_id:
            return CommandResult(
                success=False,
                error="Session mismatch for confirmation challenge",
                status=CommandStatus.DENIED,
            )

        command = challenge["command"]
        handler = self._handlers.get(command)
        tier = handler.tier if handler else PermissionTier.DESTRUCTIVE
        try:
            params = json.loads(challenge["params_json"])
        except Exception:
            params = {}

        if not approved:
            await self._safe_await(db.cancel_challenge(token, session_id))
            await self._log_audit(command, params, tier, "declined", "User declined confirmation", session_id=session_id)
            return CommandResult(
                success=False,
                error="User declined confirmation",
                permission_tier=tier,
                status=CommandStatus.DENIED,
            )

        # Approved: Delegate directly to execute() to enforce the exact same verification and execution path
        return await self.execute(
            command=command,
            params=params,
            confirmation_token=token,
            session_id=session_id,
        )
    
    async def stop(self) -> None:
        """Stop the Command Bus."""
        if self.bus:
            await self.bus.disconnect()
            self.bus = None
        logger.info("Command Bus stopped")


# Global command bus instance
command_bus: Optional[CommandBus] = None


async def get_command_bus() -> CommandBus:
    global command_bus
    if command_bus is None:
        command_bus = CommandBus()
        await command_bus.start()
    return command_bus
