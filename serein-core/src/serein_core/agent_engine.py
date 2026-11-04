"""
Agent Engine - Acts on behalf of the user.
System actions, application control, file management, task execution, automation.
"""
from .base import BaseEngine, EngineState, event_bus
from .database import Database
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum
import json
import subprocess
import os
import shutil


class ActionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentEngine(BaseEngine):
    """Executes actions on behalf of the user."""
    
    @property
    def engine_name(self) -> str:
        return "agent"
    
    def __init__(self, database: Database = None, safety_layer=None):
        super().__init__(database)
        self._safety = safety_layer
        self._action_history: List[Dict] = []
        self._running_actions: Dict[str, Dict] = {}
    
    def initialize(self) -> None:
        """Initialize agent engine."""
        if not self.db:
            self.db = Database()
        
        self._initialized = True
        event_bus.emit("agent.initialized", {})
    
    def save_state(self) -> None:
        pass
    
    def get_state(self) -> EngineState:
        return EngineState()
    
    def execute_action(self, action: str, params: Dict, safety_check: Dict = None) -> Dict[str, Any]:
        """
        Executes an action with safety validation.
        Returns result dictionary.
        """
        # Safety check
        if safety_check is None and self._safety:
            safety_check = self._safety.validate_action(action, params)
        
        if safety_check and not safety_check.get("allowed", True):
            if safety_check.get("requires_approval"):
                # Request approval
                request_id = self._safety.request_approval(
                    action, params, 
                    safety_check.get("reason", "Action requires approval")
                )
                return {
                    "status": ActionStatus.PENDING.value,
                    "approval_required": True,
                    "approval_id": request_id,
                    "message": f"This action requires your approval. {safety_check.get('reason')}"
                }
            else:
                return {
                    "status": ActionStatus.FAILED.value,
                    "error": safety_check.get("reason", "Action not allowed"),
                    "risk": safety_check.get("risk", "UNKNOWN")
                }
        
        # Execute the action
        action_method = getattr(self, f"_action_{action}", None)
        if not action_method:
            return {
                "status": ActionStatus.FAILED.value,
                "error": f"Unknown action: {action}"
            }
        
        action_id = f"{action}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        self._running_actions[action_id] = {
            "id": action_id,
            "action": action,
            "params": params,
            "status": ActionStatus.RUNNING.value,
            "started_at": datetime.now().isoformat()
        }
        
        try:
            result = action_method(params)
            result["status"] = ActionStatus.COMPLETED.value
            result["action_id"] = action_id
            
            self._running_actions[action_id]["status"] = ActionStatus.COMPLETED.value
            self._running_actions[action_id]["completed_at"] = datetime.now().isoformat()
            self._running_actions[action_id]["result"] = result
            
        except Exception as e:
            result = {
                "status": ActionStatus.FAILED.value,
                "error": str(e),
                "action_id": action_id
            }
            self._running_actions[action_id]["status"] = ActionStatus.FAILED.value
            self._running_actions[action_id]["error"] = str(e)
        
        # Record in history
        self._action_history.append(self._running_actions[action_id].copy())
        self._action_history = self._action_history[-200:]
        
        del self._running_actions[action_id]
        
        event_bus.emit("agent.action_completed", result)
        return result
    
    # ==================== FILE ACTIONS ====================
    
    def _action_file_read(self, params: Dict) -> Dict:
        path = params.get("path", "")
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return {"content": content, "path": path, "size": len(content)}
    
    def _action_file_write(self, params: Dict) -> Dict:
        path = params.get("path", "")
        content = params.get("content", "")
        mode = params.get("mode", "w")  # w or a
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, mode, encoding='utf-8') as f:
            f.write(content)
        
        return {"path": path, "bytes_written": len(content.encode('utf-8'))}
    
    def _action_file_delete(self, params: Dict) -> Dict:
        path = params.get("path", "")
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        
        return {"path": path, "deleted": True}
    
    def _action_file_list(self, params: Dict) -> Dict:
        path = params.get("path", ".")
        pattern = params.get("pattern", "*")
        
        import glob
        files = glob.glob(os.path.join(path, pattern))
        
        result = []
        for f in files:
            stat = os.stat(f)
            result.append({
                "name": os.path.basename(f),
                "path": f,
                "is_dir": os.path.isdir(f),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        
        return {"path": path, "files": result}
    
    def _action_file_move(self, params: Dict) -> Dict:
        src = params.get("source", "")
        dst = params.get("destination", "")
        
        if not os.path.exists(src):
            raise FileNotFoundError(f"Source not found: {src}")
        
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        
        return {"source": src, "destination": dst, "moved": True}
    
    # ==================== APPLICATION ACTIONS ====================
    
    def _action_app_launch(self, params: Dict) -> Dict:
        app = params.get("app", "")
        args = params.get("args", [])
        
        if os.name == 'nt':  # Windows
            if not app.endswith('.exe'):
                app += '.exe'
            cmd = [app] + args
        else:
            cmd = [app] + args
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        return {"app": app, "pid": proc.pid, "launched": True}
    
    def _action_app_close(self, params: Dict) -> Dict:
        app_name = params.get("app", "")
        
        if os.name == 'nt':
            # Windows - use taskkill
            subprocess.run(['taskkill', '/IM', app_name, '/F'], capture_output=True)
        else:
            # Unix - use pkill
            subprocess.run(['pkill', '-f', app_name], capture_output=True)
        
        return {"app": app_name, "closed": True}
    
    def _action_app_list(self, params: Dict) -> Dict:
        """Lists running applications."""
        apps = []
        
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    info = proc.info
                    if info['name']:
                        apps.append({
                            "name": info['name'],
                            "pid": info['pid'],
                            "cpu": info['cpu_percent'],
                            "memory": info['memory_percent']
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            pass
        
        # Sort by CPU usage
        apps.sort(key=lambda x: x.get('cpu', 0), reverse=True)
        
        return {"apps": apps[:20]}
    
    # ==================== SYSTEM ACTIONS ====================
    
    def _action_system_command(self, params: Dict) -> Dict:
        """Executes a system command (with extreme caution)."""
        command = params.get("command", "")
        shell = params.get("shell", False)
        
        # This is dangerous - only allow a whitelist in production
        allowed_commands = ['ping', 'ipconfig', 'ifconfig', 'netstat', 'df', 'free', 'uptime']
        
        cmd_parts = command.split()
        if cmd_parts[0] not in allowed_commands:
            raise PermissionError(f"Command not allowed: {cmd_parts[0]}")
        
        result = subprocess.run(command, shell=shell, capture_output=True, text=True, timeout=30)
        
        return {
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    
    def _action_system_info(self, params: Dict) -> Dict:
        """Returns system information."""
        import platform
        import psutil
        
        return {
            "platform": platform.system(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
            "cpu_count": psutil.cpu_count(),
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "disk_total_gb": round(psutil.disk_usage('/').total / (1024**3), 2)
        }
    
    # ==================== NETWORK ACTIONS ====================
    
    def _action_network_request(self, params: Dict) -> Dict:
        """Makes an HTTP request."""
        import requests
        
        url = params.get("url", "")
        method = params.get("method", "GET")
        headers = params.get("headers", {})
        data = params.get("data", None)
        timeout = params.get("timeout", 30)
        
        response = requests.request(method, url, headers=headers, 
                                  json=data if method in ["POST", "PUT", "PATCH"] else None,
                                  params=data if method == "GET" else None,
                                  timeout=timeout)
        
        return {
            "url": url,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": response.text[:5000]  # Limit response size
        }
    
    # ==================== TASK MANAGEMENT ACTIONS ====================
    
    def _action_task_create(self, params: Dict) -> Dict:
        """Creates a task in the database."""
        title = params.get("title", "")
        description = params.get("description", "")
        due_date = params.get("due_date", None)
        
        if not self.db:
            raise RuntimeError("Database not available")
        
        task_id = self.db.add_task(title, description, due_date)
        
        return {"task_id": task_id, "title": title, "created": True}
    
    def _action_task_complete(self, params: Dict) -> Dict:
        """Marks a task as complete."""
        task_id = params.get("task_id", 0)
        
        if not self.db:
            raise RuntimeError("Database not available")
        
        self.db.complete_task(task_id)
        
        return {"task_id": task_id, "completed": True}
    
    def _action_task_list(self, params: Dict) -> Dict:
        """Lists tasks."""
        status = params.get("status", "pending")
        
        if not self.db:
            raise RuntimeError("Database not available")
        
        tasks = self.db.get_tasks(status)
        
        return {"tasks": tasks}
    
    # ==================== UTILITY ACTIONS ====================
    
    def _action_reminder_set(self, params: Dict) -> Dict:
        """Sets a reminder (stored as system event)."""
        message = params.get("message", "")
        when = params.get("when", "")  # ISO format datetime
        
        if not self.db:
            raise RuntimeError("Database not available")
        
        self.db.add_system_event("reminder", f"{message} (at {when})")
        
        return {"reminder_set": True, "message": message, "when": when}
    
    def _action_note_save(self, params: Dict) -> Dict:
        """Saves a note (as a memory)."""
        content = params.get("content", "")
        tags = params.get("tags", ["note"])
        
        # This would go through the memory engine
        event_bus.emit("memory.store_requested", {
            "content": content,
            "tags": tags,
            "importance": 50,
            "source": "note"
        })
        
        return {"note_saved": True, "content": content[:100]}
    
    def get_action_history(self, limit: int = 20) -> List[Dict]:
        """Returns recent action history."""
        return self._action_history[-limit:]
    
    def get_running_actions(self) -> List[Dict]:
        """Returns currently running actions."""
        return list(self._running_actions.values())
    
    def get_agent_context(self) -> str:
        """Returns agent capabilities for context."""
        lines = ["AGENT CAPABILITIES:"]
        lines.append("- File operations: read, write, delete, list, move")
        lines.append("- Application control: launch, close, list")
        lines.append("- System info and safe commands")
        lines.append("- Network requests (with permission)")
        lines.append("- Task management: create, complete, list")
        lines.append("- Reminders and notes")
        lines.append("\nAll actions require appropriate permissions.")
        
        if self._action_history:
            recent = [a["action"] for a in self._action_history[-3:]]
            lines.append(f"\nRecent actions: {', '.join(recent)}")
        
        return "\n".join(lines)
    
    def close(self) -> None:
        # Wait for running actions to complete
        for action in self._running_actions.values():
            if action["status"] == ActionStatus.RUNNING.value:
                action["status"] = ActionStatus.CANCELLED.value
        super().close()