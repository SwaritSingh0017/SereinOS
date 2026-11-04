"""
Controlled Self-Evolution System - User-approved patch/rollback system.
Allows Serein to propose and apply changes to her own code with user approval.
Levels: 0=Observe only, 1=Suggest changes, 2=Generate/apply patches (with approval)
"""
import asyncio
import logging
import hashlib
import json
import subprocess
import tempfile
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Dict, Optional, Any, AsyncIterator
from uuid import uuid4

from serein_core.config import get_config as config
from serein_core.database import get_database
from serein_core.events import event_bus
from serein_core.ai import get_router, ModelTier, ChatMessage

logger = logging.getLogger("serein.self_evolution")


class EvolutionLevel(Enum):
    OBSERVE = 0      # Only observe and report
    SUGGEST = 1      # Suggest changes, user manually applies
    PATCH = 2        # Generate patches, user approves before apply
    AUTONOMOUS = 3   # NOT ALLOWED - Full autonomous modification


class PatchType(Enum):
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    OPTIMIZATION = "optimization"
    REFACTOR = "refactor"
    SECURITY = "security"
    CONFIG = "config"


class PatchStatus(Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class CodeChange:
    """Represents a single code change."""
    file_path: str
    old_content: str
    new_content: str
    description: str
    
    def to_unified_diff(self) -> str:
        """Generate unified diff format."""
        import difflib
        old_lines = self.old_content.splitlines(keepends=True)
        new_lines = self.new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, 
                                   fromfile=f"a/{self.file_path}",
                                   tofile=f"b/{self.file_path}")
        return "".join(diff)


@dataclass
class Patch:
    """A collection of related code changes."""
    id: str
    name: str
    description: str
    patch_type: PatchType
    changes: List[CodeChange] = field(default_factory=list)
    status: PatchStatus = PatchStatus.DRAFT
    created_at: datetime = field(default_factory=datetime.now)
    reviewed_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    rollback_hash: Optional[str] = None  # Git commit hash for rollback
    test_results: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def get_full_diff(self) -> str:
        """Get combined diff for all changes."""
        return "\n".join(change.to_unified_diff() for change in self.changes)
    
    def affected_files(self) -> List[str]:
        return [c.file_path for c in self.changes]


@dataclass
class EvolutionEvent:
    """Record of evolution activity."""
    id: str
    timestamp: datetime
    action: str  # proposed, reviewed, approved, applied, rolled_back
    patch_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    user_feedback: Optional[str] = None


class PatchGenerator:
    """Generates patches using AI analysis."""
    
    def __init__(self):
        self.templates = {
            PatchType.BUG_FIX: "Fix a bug in the code",
            PatchType.FEATURE: "Add a new feature",
            PatchType.OPTIMIZATION: "Optimize performance",
            PatchType.REFACTOR: "Refactor for maintainability",
            PatchType.SECURITY: "Fix security vulnerability",
            PatchType.CONFIG: "Update configuration",
        }
    
    async def analyze_issue(self, issue_description: str, 
                           target_files: List[str]) -> List[CodeChange]:
        """Analyze issue and generate proposed changes."""
        router = await get_router()
        
        # Read target files
        file_contents = {}
        for f in target_files:
            try:
                file_contents[f] = Path(f).read_text()
            except Exception as e:
                logger.warning(f"Could not read {f}: {e}")
                file_contents[f] = ""
        
        prompt = f"""You are an expert software engineer. Analyze the issue and generate precise code changes.

ISSUE: {issue_description}

TARGET FILES:
{json.dumps({f: c[:2000] for f, c in file_contents.items()}, indent=2)}

Generate the minimal necessary changes to fix/address the issue.
Output ONLY a JSON array of changes in this format:
[
  {{
    "file_path": "path/to/file.py",
    "old_content": "exact current content to replace",
    "new_content": "exact new content",
    "description": "what this change does"
  }}
]

Requirements:
- old_content must EXACTLY match current file content
- Make minimal, focused changes
- Preserve existing style and formatting
- Don't change unrelated code
"""

        response = await router.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier.SMART,
        )
        
        try:
            changes_data = json.loads(response.content)
            changes = []
            for c in changes_data:
                changes.append(CodeChange(
                    file_path=c["file_path"],
                    old_content=c["old_content"],
                    new_content=c["new_content"],
                    description=c["description"],
                ))
            return changes
        except Exception as e:
            logger.error(f"Failed to parse AI response: {e}")
            return []
    
    async def generate_patch(self, name: str, description: str, 
                           patch_type: PatchType, target_files: List[str]) -> Patch:
        """Generate a complete patch."""
        changes = await self.analyze_issue(description, target_files)
        
        patch = Patch(
            id=str(uuid4()),
            name=name,
            description=description,
            patch_type=patch_type,
            changes=changes,
        )
        
        return patch


class PatchValidator:
    """Validates patches before application."""
    
    def __init__(self):
        self.checks = [
            self._check_syntax,
            self._check_tests_exist,
            self._check_no_secrets,
            self._check_imports,
        ]
    
    async def validate(self, patch: Patch) -> Dict[str, Any]:
        """Run all validation checks."""
        results = {
            "passed": True,
            "errors": [],
            "warnings": [],
            "checks": {}
        }
        
        for check in self.checks:
            try:
                check_result = await check(patch)
                results["checks"][check.__name__] = check_result
                if not check_result.get("passed", True):
                    results["passed"] = False
                if check_result.get("errors"):
                    results["errors"].extend(check_result["errors"])
                if check_result.get("warnings"):
                    results["warnings"].extend(check_result["warnings"])
            except Exception as e:
                logger.error(f"Validation check {check.__name__} failed: {e}")
                results["passed"] = False
                results["errors"].append(f"{check.__name__}: {e}")
        
        return results
    
    async def _check_syntax(self, patch: Patch) -> Dict[str, Any]:
        """Check syntax of modified files."""
        import ast
        errors = []
        
        for change in patch.changes:
            if change.file_path.endswith(".py"):
                try:
                    ast.parse(change.new_content)
                except SyntaxError as e:
                    errors.append(f"{change.file_path}: {e}")
        
        return {"passed": len(errors) == 0, "errors": errors}
    
    async def _check_tests_exist(self, patch: Patch) -> Dict[str, Any]:
        """Check if tests exist for modified files."""
        warnings = []
        for change in patch.changes:
            test_path = self._get_test_path(change.file_path)
            if test_path and not Path(test_path).exists():
                warnings.append(f"No test found for {change.file_path} (expected {test_path})")
        return {"passed": True, "warnings": warnings}
    
    def _get_test_path(self, file_path: str) -> Optional[str]:
        """Get expected test file path."""
        path = Path(file_path)
        if path.name.startswith("test_"):
            return None
        test_names = [
            f"test_{path.name}",
            f"{path.stem}_test.py",
            f"tests/{path.name}",
            f"tests/test_{path.stem}.py",
        ]
        for t in test_names:
            if Path(t).exists():
                return t
        return test_names[0]
    
    async def _check_no_secrets(self, patch: Patch) -> Dict[str, Any]:
        """Check for hardcoded secrets."""
        import re
        errors = []
        secret_patterns = [
            (r"api_key\s*=\s*['\"][^'\"]+['\"]", "API key"),
            (r"password\s*=\s*['\"][^'\"]+['\"]", "Password"),
            (r"secret\s*=\s*['\"][^'\"]+['\"]", "Secret"),
            (r"token\s*=\s*['\"][^'\"]+['\"]", "Token"),
            (r"-----BEGIN PRIVATE KEY-----", "Private key"),
        ]
        
        for change in patch.changes:
            for pattern, name in secret_patterns:
                if re.search(pattern, change.new_content, re.IGNORECASE):
                    errors.append(f"{change.file_path}: Possible {name} in new content")
        
        return {"passed": len(errors) == 0, "errors": errors}
    
    async def _check_imports(self, patch: Patch) -> Dict[str, Any]:
        """Check for problematic imports."""
        warnings = []
        for change in patch.changes:
            if change.file_path.endswith(".py"):
                # Check for wildcard imports
                if re.search(r"from\s+\w+\s+import\s+\*", change.new_content):
                    warnings.append(f"{change.file_path}: Wildcard import detected")
                # Check for deprecated modules
                deprecated = ["imp", "optparse", "commands", "pipes"]
                for dep in deprecated:
                    if f"import {dep}" in change.new_content or f"from {dep}" in change.new_content:
                        warnings.append(f"{change.file_path}: Deprecated module '{dep}' imported")
        return {"passed": True, "warnings": warnings}


class PatchApplier:
    """Applies and rolls back patches."""
    
    def __init__(self, repo_path: Path = None):
        self.repo_path = repo_path or Path.cwd()
    
    async def apply(self, patch: Patch) -> Dict[str, Any]:
        """Apply a patch."""
        try:
            # Create backup
            backup_dir = self.repo_path / ".serein_backups" / patch.id
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # Apply each change
            for change in patch.changes:
                file_path = self.repo_path / change.file_path
                
                # Backup original
                if file_path.exists():
                    backup_path = backup_dir / change.file_path
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, backup_path)
                
                # Apply change
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(change.new_content)
                
                logger.info(f"Applied change to {change.file_path}")
            
            # Record rollback hash (git commit)
            rollback_hash = await self._get_git_hash()
            patch.rollback_hash = rollback_hash
            patch.status = PatchStatus.APPLIED
            patch.applied_at = datetime.now()
            
            # Run tests
            test_result = await self._run_tests()
            patch.test_results = test_result
            
            return {"success": True, "rollback_hash": rollback_hash, "tests": test_result}
            
        except Exception as e:
            patch.status = PatchStatus.FAILED
            logger.error(f"Patch apply failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def rollback(self, patch: Patch) -> Dict[str, Any]:
        """Rollback a patch."""
        if not patch.rollback_hash:
            return {"success": False, "error": "No rollback hash available"}
        
        try:
            # Use git to rollback
            await self._run_git(["reset", "--hard", patch.rollback_hash])
            patch.status = PatchStatus.ROLLED_BACK
            return {"success": True, "rolled_back_to": patch.rollback_hash}
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _get_git_hash(self) -> str:
        """Get current git commit hash."""
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.repo_path,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()
    
    async def _run_git(self, args: List[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.repo_path,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Git failed: {stderr.decode()}")
        return stdout.decode().strip()
    
    async def _run_tests(self) -> Dict[str, Any]:
        """Run test suite."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest", "-x", "--tb=short", "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.repo_path,
            )
            stdout, stderr = await proc.communicate()
            
            return {
                "passed": proc.returncode == 0,
                "output": stdout.decode(),
                "errors": stderr.decode() if stderr else "",
            }
        except Exception as e:
            return {"passed": False, "error": str(e)}


class SelfEvolutionEngine:
    """
    Controlled Self-Evolution Engine.
    
    Manages the complete lifecycle of self-modification:
    1. Observes system behavior
    2. Identifies improvement opportunities
    3. Generates patches
    4. Validates patches
    5. Requests user approval
    6. Applies with rollback capability
    """
    
    def __init__(self):
        self.level = EvolutionLevel.SUGGEST  # Default level
        self.generator = PatchGenerator()
        self.validator = PatchValidator()
        self.applier = PatchApplier()
        self.patches: Dict[str, Patch] = {}
        self.history: List[EvolutionEvent] = []
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
    
    async def initialize(self, level: EvolutionLevel = EvolutionLevel.SUGGEST) -> None:
        """Initialize the evolution engine."""
        self.level = level
        self._running = True
        logger.info(f"Self-Evolution Engine initialized at level {level.name}")
    
    def set_level(self, level: EvolutionLevel) -> None:
        """Set evolution level."""
        if level == EvolutionLevel.AUTONOMOUS:
            raise ValueError("AUTONOMOUS level is not allowed for safety")
        self.level = level
        logger.info(f"Evolution level set to {level.name}")
    
    def _record_event(self, action: str, patch_id: str = None, 
                     details: Dict = None, user_feedback: str = None):
        """Record evolution event."""
        event = EvolutionEvent(
            id=str(uuid4()),
            timestamp=datetime.now(),
            action=action,
            patch_id=patch_id,
            details=details or {},
            user_feedback=user_feedback,
        )
        self.history.append(event)
        if len(self.history) > 1000:
            self.history = self.history[-1000:]
    
    async def propose_improvement(self, name: str, description: str,
                                patch_type: PatchType, target_files: List[str]) -> Patch:
        """Propose a new improvement."""
        if self.level == EvolutionLevel.OBSERVE:
            raise PermissionError("Cannot propose at OBSERVE level")
        
        patch = await self.generator.generate_patch(name, description, patch_type, target_files)
        
        # Validate
        validation = await self.validator.validate(patch)
        patch.metadata["validation"] = validation
        
        if not validation["passed"]:
            self._record_event("proposed", patch_id=patch.id, 
                              details={"validation": validation, "result": "rejected"})
            raise ValueError(f"Patch validation failed: {validation['errors']}")
        
        patch.status = PatchStatus.PENDING_REVIEW
        self.patches[patch.id] = patch
        
        self._record_event("proposed", patch_id=patch.id, 
                          details={"validation": validation})
        
        # Emit event for UI notification
        await event_bus.publish(type("Event", (), {
            "type": "evolution.patch_proposed",
            "data": {
                "patch_id": patch.id,
                "name": patch.name,
                "type": patch.patch_type.value,
                "description": patch.description,
                "files": patch.affected_files(),
                "validation": validation,
            }
        })())
        
        return patch
    
    async def review_patch(self, patch_id: str, approved: bool, 
                          user_feedback: str = None) -> Dict[str, Any]:
        """Review a pending patch."""
        patch = self.patches.get(patch_id)
        if not patch:
            return {"success": False, "error": "Patch not found"}
        
        if patch.status != PatchStatus.PENDING_REVIEW:
            return {"success": False, "error": f"Patch not in review status: {patch.status.value}"}
        
        if approved:
            patch.status = PatchStatus.APPROVED
            patch.reviewed_at = datetime.now()
            
            # Apply if level allows
            if self.level >= EvolutionLevel.PATCH:
                result = await self.apply_patch(patch_id)
                self._record_event("approved_and_applied", patch_id=patch_id, 
                                  user_feedback=user_feedback)
                return result
            else:
                self._record_event("approved", patch_id=patch_id, 
                                  user_feedback=user_feedback)
                return {"success": True, "message": "Approved, awaiting manual apply"}
        else:
            patch.status = PatchStatus.REJECTED
            patch.reviewed_at = datetime.now()
            self._record_event("rejected", patch_id=patch_id, 
                              user_feedback=user_feedback)
            return {"success": True, "message": "Patch rejected"}
    
    async def apply_patch(self, patch_id: str) -> Dict[str, Any]:
        """Apply an approved patch."""
        patch = self.patches.get(patch_id)
        if not patch:
            return {"success": False, "error": "Patch not found"}
        
        if patch.status not in [PatchStatus.APPROVED, PatchStatus.PENDING_REVIEW]:
            return {"success": False, "error": f"Cannot apply patch in status: {patch.status.value}"}
        
        if self.level < EvolutionLevel.PATCH:
            return {"success": False, "error": "Evolution level too low for auto-apply"}
        
        result = await self.applier.apply(patch)
        
        if result["success"]:
            self._record_event("applied", patch_id=patch_id)
            await event_bus.publish(type("Event", (), {
                "type": "evolution.patch_applied",
                "data": {"patch_id": patch_id, "tests": result.get("tests")}
            })())
        else:
            self._record_event("apply_failed", patch_id=patch_id, 
                              details={"error": result.get("error")})
        
        return result
    
    async def rollback_patch(self, patch_id: str) -> Dict[str, Any]:
        """Rollback an applied patch."""
        patch = self.patches.get(patch_id)
        if not patch:
            return {"success": False, "error": "Patch not found"}
        
        if patch.status != PatchStatus.APPLIED:
            return {"success": False, "error": f"Patch not applied: {patch.status.value}"}
        
        result = await self.applier.rollback(patch)
        
        if result["success"]:
            self._record_event("rolled_back", patch_id=patch_id)
            await event_bus.publish(type("Event", (), {
                "type": "evolution.patch_rolled_back",
                "data": {"patch_id": patch_id}
            })())
        
        return result
    
    async def scan_for_improvements(self) -> List[Dict[str, Any]]:
        """Scan codebase for improvement opportunities."""
        if self.level == EvolutionLevel.OBSERVE:
            return []
        
        opportunities = []
        
        # Scan for TODO/FIXME comments
        # Scan for performance issues
        # Scan for deprecated patterns
        # Scan for test coverage gaps
        
        # This would integrate with static analysis tools
        # For now, return empty
        return opportunities
    
    async def auto_improve(self) -> List[Patch]:
        """Automatically find and propose improvements (requires level >= SUGGEST)."""
        if self.level < EvolutionLevel.SUGGEST:
            return []
        
        opportunities = await self.scan_for_improvements()
        proposed = []
        
        for opp in opportunities:
            try:
                patch = await self.propose_improvement(
                    name=opp["name"],
                    description=opp["description"],
                    patch_type=PatchType(opp["type"]),
                    target_files=opp["files"],
                )
                proposed.append(patch)
            except Exception as e:
                logger.warning(f"Failed to propose improvement: {e}")
        
        return proposed
    
    def get_patch(self, patch_id: str) -> Optional[Patch]:
        return self.patches.get(patch_id)
    
    def list_patches(self, status: PatchStatus = None) -> List[Patch]:
        patches = list(self.patches.values())
        if status:
            patches = [p for p in patches if p.status == status]
        return sorted(patches, key=lambda p: p.created_at, reverse=True)
    
    def get_history(self, limit: int = 100) -> List[EvolutionEvent]:
        return self.history[-limit:]
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "level": self.level.name,
            "total_patches": len(self.patches),
            "by_status": {
                s.value: len([p for p in self.patches.values() if p.status == s])
                for s in PatchStatus
            },
            "pending_review": len([p for p in self.patches.values() 
                                  if p.status == PatchStatus.PENDING_REVIEW]),
            "recent_events": len([e for e in self.history 
                                 if e.timestamp > datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)]),
        }
    
    async def shutdown(self):
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass


# Global instance
self_evolution = SelfEvolutionEngine()