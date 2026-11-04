"""
Command Bus - D-Bus interface for system control.
Provides tiered permission model for system actions.
"""
import asyncio
import logging
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Awaitable, Set
from uuid import uuid4

from dbus_next import BusType, MessageType
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal
from dbus_next.constants import PropertyAccess

from serein_core.config import get_config as config
from serein_core.database import get_database
from serein_core.events import event_bus, CoreEvents

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
    status: Optional[CommandStatus] = None

    def __post_init__(self):
        if self.status is None:
            if self.requires_confirmation:
                self.status = CommandStatus.CONFIRMATION_REQUIRED
            elif not self.success:
                self.status = CommandStatus.FAILED
            else:
                self.status = CommandStatus.SUCCESS


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
        # Would use wlrctl or similar for Wayland
        return CommandResult(success=True, result={"windows": []}, permission_tier=PermissionTier.READ_ONLY)
    
    @staticmethod
    async def read_file(params: Dict) -> CommandResult:
        """Read file contents."""
        path = params.get("path")
        if not path:
            return CommandResult(success=False, error="path required")
        try:
            from pathlib import Path
            p = Path(path)
            if not p.exists():
                return CommandResult(success=False, error="File not found")
            if not p.is_file():
                return CommandResult(success=False, error="Not a file")
            content = p.read_text(encoding="utf-8")
            return CommandResult(success=True, result={"path": str(p), "content": content}, permission_tier=PermissionTier.READ_ONLY)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class ReversibleHandlers:
    """Reversible system actions."""
    
    @staticmethod
    async def launch_app(params: Dict) -> CommandResult:
        """Launch an application."""
        app = params.get("app")
        args = params.get("args", [])
        if not app:
            return CommandResult(success=False, error="app required")
        try:
            import subprocess
            proc = await asyncio.create_subprocess_exec(app, *args)
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
        level = params.get("level", 50)  # 0-100
        try:
            # Use pactl or wpctl for Wayland
            import subprocess
            proc = await asyncio.create_subprocess_exec(
                "wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level/100:.2f}"
            )
            await proc.wait()
            return CommandResult(success=True, result={"volume": level}, permission_tier=PermissionTier.REVERSIBLE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def move_file(params: Dict) -> CommandResult:
        """Move file to new location."""
        src = params.get("source")
        dst = params.get("destination")
        if not src or not dst:
            return CommandResult(success=False, error="source and destination required")
        try:
            import shutil
            from pathlib import Path
            shutil.move(src, dst)
            return CommandResult(success=True, result={"moved": src, "to": dst}, permission_tier=PermissionTier.REVERSIBLE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class DestructiveHandlers:
    """Destructive/irreversible actions."""
    
    @staticmethod
    async def delete_file(params: Dict) -> CommandResult:
        """Delete file or directory."""
        path = params.get("path")
        recursive = params.get("recursive", False)
        if not path:
            return CommandResult(success=False, error="path required")
        try:
            import shutil
            from pathlib import Path
            p = Path(path)
            if p.is_dir():
                if recursive:
                    shutil.rmtree(p)
                else:
                    return CommandResult(success=False, error="Directory requires recursive=true")
            else:
                p.unlink()
            return CommandResult(success=True, result={"deleted": path}, permission_tier=PermissionTier.DESTRUCTIVE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def shutdown_system(params: Dict) -> CommandResult:
        """Shutdown the system."""
        delay = params.get("delay", 0)
        try:
            import subprocess
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "poweroff"
            )
            await proc.wait()
            return CommandResult(success=True, result={"action": "shutdown"}, permission_tier=PermissionTier.DESTRUCTIVE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def reboot_system(params: Dict) -> CommandResult:
        """Reboot the system."""
        try:
            import subprocess
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "reboot"
            )
            await proc.wait()
            return CommandResult(success=True, result={"action": "reboot"}, permission_tier=PermissionTier.DESTRUCTIVE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def install_package(params: Dict) -> CommandResult:
        """Install a package."""
        package = params.get("package")
        manager = params.get("manager", "apt")  # apt, pacman, flatpak, snap
        if not package:
            return CommandResult(success=False, error="package required")
        try:
            import subprocess
            cmds = {
                "apt": ["apt", "install", "-y", package],
                "pacman": ["pacman", "-S", "--noconfirm", package],
                "flatpak": ["flatpak", "install", "-y", package],
                "snap": ["snap", "install", package],
            }
            if manager not in cmds:
                return CommandResult(success=False, error=f"Unknown manager: {manager}")
            
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
        """Make network request."""
        url = params.get("url")
        method = params.get("method", "GET")
        headers = params.get("headers", {})
        data = params.get("data")
        if not url:
            return CommandResult(success=False, error="url required")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers, json=data) as resp:
                    text = await resp.text()
                    return CommandResult(success=True, result={
                        "status": resp.status,
                        "headers": dict(resp.headers),
                        "body": text[:10000],
                    }, permission_tier=PermissionTier.SENSITIVE)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
    
    @staticmethod
    async def access_secrets(params: Dict) -> CommandResult:
        """Access system secrets/keyring."""
        # Would use secretstorage or similar
        return CommandResult(success=False, error="Not implemented", permission_tier=PermissionTier.SENSITIVE)


class CommandBus:
    """Main Command Bus - D-Bus service for system control."""
    
    def __init__(self, allowed_tiers: Optional[Set[PermissionTier]] = None, dry_run: bool = False):
        self.bus: Optional[MessageBus] = None
        bus_cfg = getattr(config, "command_bus", None)
        self.service_name = getattr(bus_cfg, "dbus_service_name", "os.serein.CommandBus") if bus_cfg else "os.serein.CommandBus"
        self.object_path = getattr(bus_cfg, "dbus_object_path", "/os/serein/CommandBus") if bus_cfg else "/os/serein/CommandBus"
        self.allowed_tiers = set(allowed_tiers) if allowed_tiers is not None else {PermissionTier.READ_ONLY}
        self.dry_run = dry_run
        self._handlers: Dict[str, CommandHandler] = {}
        self._pending_confirmations: Dict[str, Any] = {}
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Register all command handlers."""
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
                      PermissionTier.DESTRUCTIVE, True, "Delete file? This cannot be undone.")
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
        
        # Connect to system bus
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        
        # Request service name
        await self.bus.request_name(self.service_name)
        
        # Export interface
        interface = self._create_interface()
        self.bus.export(self.object_path, interface)
        
        logger.info(f"Command Bus started on {self.service_name} at {self.object_path}")
        
        # Emit event
        await event_bus.publish(type("Event", (), {
            "type": CoreEvents.COMMAND_BUS_STARTED,
            "data": {"service": self.service_name}
        })())
    
    def _create_interface(self) -> ServiceInterface:
        """Create D-Bus service interface."""
        interface = ServiceInterface("os.serein.CommandBus")
        
        @method()
        async def ExecuteCommand(command: str, params: str, confirmation_token: str) -> str:
            """Execute a command via D-Bus."""
            try:
                params_dict = json.loads(params) if params else {}
                result = await self.execute(command, params_dict, confirmation_token)
                return json.dumps({
                    "success": result.success,
                    "result": result.result,
                    "error": result.error,
                    "permission_tier": result.permission_tier.value,
                    "requires_confirmation": result.requires_confirmation,
                })
            except Exception as e:
                logger.error(f"Command execution error: {e}")
                return json.dumps({"success": False, "error": str(e)})
        
        @method()
        async def RequestConfirmation(token: str, approved: bool) -> str:
            """Handle confirmation response."""
            res = await self.confirm(token, approved)
            return json.dumps({"success": res.success, "status": res.status.value if res.status else ""})
        
        @method()
        async def ListCommands() -> str:
            """List available commands."""
            return json.dumps(self.list_commands())
        
        interface.add_method(ExecuteCommand)
        interface.add_method(RequestConfirmation)
        interface.add_method(ListCommands)
        
        return interface
    
    async def execute(self, command: str, params: Optional[Dict[str, Any]] = None, 
                     confirmation_token: str = "") -> CommandResult:
        """Execute a command with permission checking."""
        params = params or {}
        
        if command not in self._handlers:
            return CommandResult(
                success=False,
                error=f"Unknown command: {command}",
                status=CommandStatus.UNKNOWN,
            )
        
        handler = self._handlers[command]
        
        # Check permissions against allowed_tiers
        if handler.tier not in self.allowed_tiers:
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
        
        # Dry-run handling
        if self.dry_run:
            return CommandResult(
                success=True,
                result={"dry_run": True, "command": command, "params": params},
                permission_tier=handler.tier,
                status=CommandStatus.SUCCESS,
            )
        
        # Handle confirmation
        if handler.requires_confirmation:
            if confirmation_token:
                # Token provided - execute confirmed command
                pending = self._pending_confirmations.pop(confirmation_token, None)
                if not pending:
                    return CommandResult(
                        success=False,
                        error="Invalid or expired confirmation token",
                        permission_tier=handler.tier,
                        status=CommandStatus.FAILED,
                    )
            else:
                token = str(uuid4())
                self._pending_confirmations[token] = {
                    "command": command,
                    "params": params,
                    "handler": handler,
                }
                
                try:
                    await event_bus.publish(type("Event", (), {
                        "type": CoreEvents.CONFIRMATION_REQUESTED,
                        "data": {
                            "token": token,
                            "command": command,
                            "prompt": handler.confirmation_prompt,
                            "params": params,
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
                    status=CommandStatus.CONFIRMATION_REQUIRED,
                )
        
        # Execute the command
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
            
            # Safe audit logging
            try:
                db = await get_database()
                if hasattr(db, "log_action"):
                    await db.log_action({
                        "action_type": command,
                        "target": str(params.get("target", "")),
                        "backend": "command_bus",
                        "initiated_by": params.get("_initiated_by", "user"),
                        "permission_tier": handler.tier.value,
                        "result": "success" if result.success else "failed",
                        "error_message": result.error,
                    })
            except Exception:
                pass
            
            # Safe event emitting
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
            return CommandResult(
                success=False,
                error=str(e),
                permission_tier=handler.tier,
                status=CommandStatus.FAILED,
            )

    async def confirm(self, token: str, approved: bool) -> CommandResult:
        """Handle user confirmation token response."""
        if token not in self._pending_confirmations:
            return CommandResult(
                success=False,
                error="Invalid or expired confirmation token",
                status=CommandStatus.FAILED,
            )
        
        pending = self._pending_confirmations.pop(token)
        handler = pending["handler"]
        params = pending["params"]
        
        if not approved:
            return CommandResult(
                success=False,
                error="User declined confirmation",
                permission_tier=handler.tier,
                status=CommandStatus.DENIED,
            )
        
        try:
            try:
                res = await handler.execute(params, confirmation=True)
            except TypeError:
                res = await handler.execute(params)
            
            if not isinstance(res, CommandResult):
                res = CommandResult(
                    success=True,
                    result=res,
                    permission_tier=handler.tier,
                    status=CommandStatus.SUCCESS,
                )
            elif res.status is None:
                res.status = CommandStatus.SUCCESS if res.success else CommandStatus.FAILED
            return res
        except Exception as e:
            return CommandResult(
                success=False,
                error=str(e),
                permission_tier=handler.tier,
                status=CommandStatus.FAILED,
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