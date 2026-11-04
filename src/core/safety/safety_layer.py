"""
Safety Layer - Cross-cutting system that protects user, data, files, and privacy.
Validates important actions before execution.
"""
from src.core.base import BaseEngine, EngineState, event_bus
from src.database.database import Database
from datetime import datetime
from typing import Dict, List, Optional, Any, Set
from enum import Enum
import json
import os


class PermissionLevel(Enum):
    """Permission levels for different actions."""
    NONE = 0      # No access
    READ = 1      # Read only
    WRITE = 2     # Read + write
    ADMIN = 3     # Full control including system actions


class ActionRisk(Enum):
    """Risk levels for actions."""
    NONE = 0      # Safe, read-only
    LOW = 1       # Minor changes, easily reversible
    MEDIUM = 2    # Significant changes, reversible with effort
    HIGH = 3      # Major changes, potentially irreversible
    CRITICAL = 4  # System-critical, irreversible


class SafetyLayer(BaseEngine):
    """Cross-cutting safety system that validates actions before execution."""
    
    @property
    def engine_name(self) -> str:
        return "safety"
    
    # Actions that require explicit permission
    PROTECTED_ACTIONS = {
        "file_delete": ActionRisk.HIGH,
        "file_modify": ActionRisk.MEDIUM,
        "file_create": ActionRisk.LOW,
        "system_command": ActionRisk.HIGH,
        "app_launch": ActionRisk.LOW,
        "app_close": ActionRisk.MEDIUM,
        "network_request": ActionRisk.MEDIUM,
        "database_write": ActionRisk.MEDIUM,
        "database_delete": ActionRisk.HIGH,
        "settings_change": ActionRisk.HIGH,
        "permission_change": ActionRisk.CRITICAL,
        "memory_delete": ActionRisk.MEDIUM,
        "self_modify": ActionRisk.CRITICAL,
    }
    
    # File paths that are always protected
    PROTECTED_PATHS = {
        "system": ["/windows", "/system32", "/program files", "/boot", "/etc", "/usr/bin", "/bin", "/sbin"],
        "serein": ["data/database", "src/core", "docs"],
        "user_sensitive": [".ssh", ".gnupg", ".aws", ".config", "wallet", "keys", "passwords"],
    }
    
    def __init__(self, database: Database = None):
        super().__init__(database)
        self._permissions: Dict[str, PermissionLevel] = {}
        self._action_log: List[Dict] = []
        self._pending_approvals: Dict[str, Dict] = {}
    
    def initialize(self) -> None:
        """Load safety settings from database."""
        if not self.db:
            self.db = Database()
        
        # Load permissions from database
        perm_row = self.db.get_permissions()
        if perm_row:
            self._permissions = {
                "camera": PermissionLevel(PermissionLevel.READ.value if perm_row[1] else PermissionLevel.NONE.value),
                "microphone": PermissionLevel(PermissionLevel.READ.value if perm_row[2] else PermissionLevel.NONE.value),
                "file_access": PermissionLevel(PermissionLevel.WRITE.value if perm_row[3] else PermissionLevel.NONE.value),
                "internet": PermissionLevel(PermissionLevel.WRITE.value if perm_row[4] else PermissionLevel.NONE.value),
                "automation": PermissionLevel(PermissionLevel.WRITE.value if perm_row[5] else PermissionLevel.NONE.value),
            }
        else:
            # Default permissions - conservative
            self._permissions = {
                "camera": PermissionLevel.NONE,
                "microphone": PermissionLevel.NONE,
                "file_access": PermissionLevel.READ,
                "internet": PermissionLevel.WRITE,
                "automation": PermissionLevel.NONE,
            }
            self.save_state()
        
        self._initialized = True
        event_bus.emit("safety.initialized", {"permissions": self.get_permissions()})
    
    def save_state(self) -> None:
        """Persist permissions to database."""
        if self.db:
            self.db.update_permissions(
                camera_access=self._permissions.get("camera", PermissionLevel.NONE).value >= PermissionLevel.READ.value,
                microphone_access=self._permissions.get("microphone", PermissionLevel.NONE).value >= PermissionLevel.READ.value,
                file_access=self._permissions.get("file_access", PermissionLevel.NONE).value >= PermissionLevel.WRITE.value,
                internet_access=self._permissions.get("internet", PermissionLevel.NONE).value >= PermissionLevel.WRITE.value,
                automation_access=self._permissions.get("automation", PermissionLevel.NONE).value >= PermissionLevel.WRITE.value,
            )
    
    def get_state(self) -> EngineState:
        return EngineState()
    
    def get_permissions(self) -> Dict[str, str]:
        """Returns permissions as readable strings."""
        return {k: v.name for k, v in self._permissions.items()}
    
    def check_permission(self, permission: str, required: PermissionLevel = PermissionLevel.READ) -> bool:
        """Checks if a permission is granted at required level."""
        current = self._permissions.get(permission, PermissionLevel.NONE)
        return current.value >= required.value
    
    def request_permission(self, permission: str, level: PermissionLevel, reason: str) -> bool:
        """Requests a permission (in real implementation, would prompt user)."""
        # For now, auto-approve safe requests, deny dangerous ones
        if level == PermissionLevel.NONE:
            return True
        elif level == PermissionLevel.READ and permission in ["internet", "file_access"]:
            self._permissions[permission] = level
            self.save_state()
            return True
        else:
            # Would prompt user in real implementation
            event_bus.emit("safety.permission_requested", {
                "permission": permission,
                "level": level.name,
                "reason": reason
            })
            return False
    
    def validate_action(self, action: str, params: Dict = None) -> Dict[str, Any]:
        """
        Validates an action before execution.
        Returns: {"allowed": bool, "reason": str, "risk": str, "requires_approval": bool}
        """
        params = params or {}
        
        # Check if action is known
        risk = self.PROTECTED_ACTIONS.get(action, ActionRisk.NONE)
        
        # Check protected paths for file operations
        if action in ["file_delete", "file_modify", "file_create"]:
            path = params.get("path", "")
            if self._is_protected_path(path):
                return {
                    "allowed": False,
                    "reason": f"Path is protected: {path}",
                    "risk": ActionRisk.CRITICAL.name,
                    "requires_approval": True,
                    "protected_path": True
                }
        
        # Check camera/microphone permissions
        if action in ["camera_access", "microphone_access"]:
            perm = "camera" if "camera" in action else "microphone"
            if not self.check_permission(perm, PermissionLevel.READ):
                return {
                    "allowed": False,
                    "reason": f"{perm.title()} access not granted",
                    "risk": ActionRisk.MEDIUM.name,
                    "requires_approval": True
                }
        
        # Check file access
        if action in ["file_read", "file_write", "file_delete", "file_modify"]:
            if not self.check_permission("file_access", PermissionLevel.READ):
                return {
                    "allowed": False,
                    "reason": "File access not granted",
                    "risk": risk.name,
                    "requires_approval": True
                }
        
        # Check internet access
        if action == "network_request":
            if not self.check_permission("internet", PermissionLevel.READ):
                return {
                    "allowed": False,
                    "reason": "Internet access not granted",
                    "risk": ActionRisk.MEDIUM.name,
                    "requires_approval": True
                }
        
        # Check automation access
        if action in ["system_command", "app_launch", "app_close"]:
            if not self.check_permission("automation", PermissionLevel.READ):
                return {
                    "allowed": False,
                    "reason": "Automation access not granted",
                    "risk": risk.name,
                    "requires_approval": True
                }
        
        # High/Critical risk actions require approval
        requires_approval = risk in [ActionRisk.HIGH, ActionRisk.CRITICAL]
        
        # Self-modification is never allowed automatically
        if action == "self_modify":
            return {
                "allowed": False,
                "reason": "Self-modification requires explicit user approval",
                "risk": ActionRisk.CRITICAL.name,
                "requires_approval": True
            }
        
        # Log the validation
        self._log_validation(action, params, risk, requires_approval)
        
        return {
            "allowed": not requires_approval,
            "reason": "Action validated" if not requires_approval else "Requires approval",
            "risk": risk.name,
            "requires_approval": requires_approval
        }
    
    def _is_protected_path(self, path: str) -> bool:
        """Checks if a path is in protected directories."""
        path_lower = path.lower()
        
        for category, paths in self.PROTECTED_PATHS.items():
            for protected in paths:
                if protected.lower() in path_lower:
                    return True
        
        return False
    
    def _log_validation(self, action: str, params: Dict, risk: ActionRisk, requires_approval: bool) -> None:
        """Logs action validation for audit trail."""
        self._action_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "params": {k: v for k, v in params.items() if k not in ["password", "token", "key"]},
            "risk": risk.name,
            "requires_approval": requires_approval,
            "allowed": not requires_approval
        })
        self._action_log = self._action_log[-500:]  # Keep last 500
    
    def approve_action(self, action_id: str, approved: bool, user_reason: str = "") -> bool:
        """Approves or denies a pending action."""
        if action_id not in self._pending_approvals:
            return False
        
        pending = self._pending_approvals[action_id]
        pending["approved"] = approved
        pending["user_reason"] = user_reason
        pending["decided_at"] = datetime.now().isoformat()
        
        if approved:
            event_bus.emit("safety.action_approved", pending)
        else:
            event_bus.emit("safety.action_denied", pending)
        
        del self._pending_approvals[action_id]
        return True
    
    def request_approval(self, action: str, params: Dict, reason: str) -> str:
        """Creates an approval request and returns request ID."""
        import uuid
        request_id = str(uuid.uuid4())[:8]
        
        self._pending_approvals[request_id] = {
            "id": request_id,
            "action": action,
            "params": params,
            "reason": reason,
            "requested_at": datetime.now().isoformat(),
            "approved": None
        }
        
        event_bus.emit("safety.approval_requested", self._pending_approvals[request_id])
        
        return request_id
    
    def get_pending_approvals(self) -> List[Dict]:
        """Returns list of pending approval requests."""
        return list(self._pending_approvals.values())
    
    def get_action_log(self, limit: int = 50) -> List[Dict]:
        """Returns recent action validation log."""
        return self._action_log[-limit:]
    
    def get_safety_context(self) -> str:
        """Returns safety status for context building."""
        perms = self.get_permissions()
        pending = len(self._pending_approvals)
        
        lines = ["SAFETY STATUS:"]
        lines.append(f"- Permissions: {', '.join(f'{k}={v}' for k, v in perms.items())}")
        lines.append(f"- Pending Approvals: {pending}")
        
        if self._action_log:
            recent_actions = [a["action"] for a in self._action_log[-5:]]
            lines.append(f"- Recent Validations: {', '.join(recent_actions)}")
        
        return "\n".join(lines)
    
    def emergency_lockdown(self, reason: str = "Emergency lockdown") -> None:
        """Revokes all permissions except read-only file access."""
        self._permissions = {
            "camera": PermissionLevel.NONE,
            "microphone": PermissionLevel.NONE,
            "file_access": PermissionLevel.READ,
            "internet": PermissionLevel.NONE,
            "automation": PermissionLevel.NONE,
        }
        self.save_state()
        event_bus.emit("safety.lockdown", {"reason": reason})
    
    def close(self) -> None:
        self.save_state()
        super().close()