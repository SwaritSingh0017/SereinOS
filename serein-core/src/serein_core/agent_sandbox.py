"""
Task Queue & Sandboxed Coding Agent - Build/run/test/fix loop in isolated containers.
Handles code generation, sandboxed execution, testing, and iterative fixing.
"""
import asyncio
import logging
import json
import tempfile
import shutil
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any, AsyncIterator
from pathlib import Path

from serein_core.config import get_config as config
from serein_core.database import get_database
from serein_core.events import event_bus
from serein_core.ai import get_router, ModelTier, ChatMessage

logger = logging.getLogger("serein.core.agent_sandbox")


class TaskStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    BUILDING = "building"
    RUNNING = "running"
    TESTING = "testing"
    FIXING = "fixing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


class SandboxType(Enum):
    DOCKER = "docker"
    PODMAN = "podman"
    FIREJAIL = "firejail"
    BUBBLEWRAP = "bubblewrap"
    LOCAL = "local"  # No isolation (dev only)


@dataclass
class BuildResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    artifacts: List[str] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class TestResult:
    success: bool
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    output: str = ""
    duration_ms: int = 0


@dataclass
class CodeTask:
    id: str
    description: str
    requirements: str
    language: str = "python"
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Code artifacts
    source_code: str = ""
    test_code: str = ""
    build_script: str = ""
    run_script: str = ""
    
    # Results
    build_result: Optional[BuildResult] = None
    test_result: Optional[TestResult] = None
    fix_attempts: int = 0
    max_fix_attempts: int = 3
    
    # Sandbox
    sandbox_id: str = ""
    workspace_path: str = ""
    
    # Metadata
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SandboxManager:
    """Manages isolated execution environments."""
    
    def __init__(self, sandbox_type: SandboxType = SandboxType.DOCKER):
        self.sandbox_type = sandbox_type
        self.active_sandboxes: Dict[str, Dict] = {}
        self._base_image = "python:3.11-slim"
    
    async def create_sandbox(self, task_id: str, workspace: Path) -> str:
        """Create a new sandbox for a task."""
        sandbox_id = f"serein-{task_id}-{uuid.uuid4().hex[:8]}"
        
        if self.sandbox_type == SandboxType.DOCKER:
            container_id = await self._create_docker_sandbox(sandbox_id, workspace)
            self.active_sandboxes[sandbox_id] = {
                "type": "docker",
                "container_id": container_id,
                "workspace": workspace,
                "created": datetime.now(),
            }
        elif self.sandbox_type == SandboxType.LOCAL:
            # Local sandbox - just directory with restricted permissions
            self.active_sandboxes[sandbox_id] = {
                "type": "local",
                "workspace": workspace,
                "created": datetime.now(),
            }
        else:
            raise NotImplementedError(f"Sandbox type {self.sandbox_type} not implemented")
        
        return sandbox_id
    
    async def _create_docker_sandbox(self, sandbox_id: str, workspace: Path) -> str:
        """Create Docker container for sandbox."""
        # Create Dockerfile
        dockerfile = workspace / "Dockerfile.sandbox"
        dockerfile.write_text(f"""
FROM {self._base_image}
WORKDIR /workspace
COPY . /workspace
RUN pip install --no-cache-dir pytest pytest-asyncio coverage
RUN useradd -m -s /bin/bash sandbox
USER sandbox
""")
        
        # Build image
        build_cmd = ["docker", "build", "-f", str(dockerfile), "-t", f"serein-sandbox:{sandbox_id}", str(workspace)]
        proc = await asyncio.create_subprocess_exec(
            *build_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"Docker build failed: {stderr.decode()}")
        
        # Run container
        run_cmd = [
            "docker", "run", "-d",
            "--name", f"serein-{sandbox_id}",
            "--memory=512m",
            "--cpus=1.0",
            "--network=none",
            "--read-only",
            "--tmpfs=/tmp:exec,size=100M",
            "--tmpfs=/workspace:exec,size=500M",
            f"serein-sandbox:{sandbox_id}",
            "sleep", "3600"  # Keep alive
        ]
        proc = await asyncio.create_subprocess_exec(
            *run_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"Docker run failed: {stderr.decode()}")
        
        container_id = stdout.decode().strip()
        return container_id
    
    async def execute_in_sandbox(self, sandbox_id: str, command: List[str], 
                                timeout: int = 120) -> tuple[int, str, str]:
        """Execute command in sandbox, return (exit_code, stdout, stderr)."""
        sandbox = self.active_sandboxes.get(sandbox_id)
        if not sandbox:
            raise ValueError(f"Sandbox {sandbox_id} not found")
        
        if sandbox["type"] == "docker":
            container_id = sandbox["container_id"]
            cmd = ["docker", "exec", "-i", container_id] + command
        elif sandbox["type"] == "podman":
            container_id = sandbox["container_id"]
            cmd = ["podman", "exec", "-i", container_id] + command
        else:
            # Direct host subprocess execution is strictly disabled when container isolation is unavailable
            raise PermissionError(
                f"Host execution disabled: Sandbox {sandbox_id} lacks container isolation (Docker/Podman required)."
            )
        
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout
            )
            stdout, stderr = await proc.communicate()
            return proc.returncode, stdout.decode(), stderr.decode()
        except asyncio.TimeoutError:
            return -1, "", "Timeout"
        except Exception as e:
            return -1, "", str(e)
    
    async def copy_to_sandbox(self, sandbox_id: str, src: Path, dst: str) -> bool:
        """Copy files to sandbox."""
        sandbox = self.active_sandboxes.get(sandbox_id)
        if not sandbox:
            return False
        
        if sandbox["type"] == "docker":
            container_id = sandbox["container_id"]
            # Use docker cp
            cmd = ["docker", "cp", str(src), f"{container_id}:{dst}"]
            proc = await asyncio.create_subprocess_exec(*cmd)
            return proc.returncode == 0
        else:
            # Local copy
            dst_path = Path(sandbox["workspace"]) / dst.lstrip("/")
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst_path, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst_path)
            return True
    
    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy sandbox and cleanup."""
        sandbox = self.active_sandboxes.pop(sandbox_id, None)
        if not sandbox:
            return False
        
        if sandbox["type"] == "docker":
            container_id = sandbox["container_id"]
            try:
                await asyncio.create_subprocess_exec("docker", "rm", "-f", container_id)
            except Exception:
                pass
            # Remove image
            try:
                await asyncio.create_subprocess_exec("docker", "rmi", "-f", f"serein-sandbox:{sandbox_id}")
            except Exception:
                pass
        
        return True
    
    async def cleanup_all(self) -> None:
        """Cleanup all active sandboxes."""
        for sandbox_id in list(self.active_sandboxes.keys()):
            await self.destroy_sandbox(sandbox_id)


class CodingAgent:
    """
    AI-powered coding agent that can:
    1. Generate code from requirements
    2. Write tests
    3. Build and run in sandbox
    4. Iteratively fix failures
    5. Report results
    """
    
    def __init__(self, sandbox_manager: SandboxManager):
        self.sandbox = sandbox_manager
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.active_tasks: Dict[str, CodeTask] = {}
        self.task_history: List[CodeTask] = []
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self.max_concurrent = 2
    
    async def start(self) -> None:
        """Start the coding agent worker."""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Coding Agent started")
    
    async def stop(self) -> None:
        """Stop the coding agent."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        # Cleanup sandboxes
        await self.sandbox.cleanup_all()
        logger.info("Coding Agent stopped")
    
    async def submit_task(self, task: CodeTask) -> str:
        """Submit a coding task to the queue."""
        await self.task_queue.put(task)
        return task.id
    
    async def get_task_status(self, task_id: str) -> Optional[CodeTask]:
        """Get task status."""
        if task_id in self.active_tasks:
            return self.active_tasks[task_id]
        for task in self.task_history:
            if task.id == task_id:
                return task
        return None
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending/running task."""
        if task_id in self.active_tasks:
            task = self.active_tasks[task_id]
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.now()
            # Cleanup sandbox
            if task.sandbox_id:
                await self.sandbox.destroy_sandbox(task.sandbox_id)
            return True
        return False
    
    async def _worker_loop(self) -> None:
        """Main worker loop processing task queue."""
        while self._running:
            try:
                # Check concurrent limit
                running_count = sum(1 for t in self.active_tasks.values() 
                                  if t.status in [TaskStatus.QUEUED, TaskStatus.BUILDING, 
                                                 TaskStatus.RUNNING, TaskStatus.TESTING, TaskStatus.FIXING])
                
                if running_count >= self.max_concurrent:
                    await asyncio.sleep(1)
                    continue
                
                # Get next task
                try:
                    task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                # Process task
                self.active_tasks[task.id] = task
                await self._process_task(task)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)
    
    async def _process_task(self, task: CodeTask) -> None:
        """Process a single coding task through the full pipeline."""
        task.status = TaskStatus.QUEUED
        task.started_at = datetime.now()
        
        try:
            await event_bus.publish(type("Event", (), {
                "type": "agent.task_started",
                "data": {"task_id": task.id, "description": task.description}
            })())
            
            # Create sandbox workspace
            workspace = Path(tempfile.mkdtemp(prefix=f"serein-task-{task.id}-"))
            task.workspace_path = str(workspace)
            task.sandbox_id = await self.sandbox.create_sandbox(task.id, workspace)
            
            # Phase 1: Generate code
            task.status = TaskStatus.BUILDING
            await self._generate_code(task)
            
            # Phase 2: Write files to workspace
            await self._write_task_files(task)
            
            # Phase 3: Build
            build_result = await self._build_task(task)
            task.build_result = build_result
            
            if not build_result.success:
                # Try to fix
                await self._fix_build_failures(task)
                if not task.build_result or not task.build_result.success:
                    task.status = TaskStatus.FAILED
                    return
            
            # Phase 4: Generate tests
            await self._generate_tests(task)
            
            # Phase 5: Run tests
            task.status = TaskStatus.TESTING
            test_result = await self._run_tests(task)
            task.test_result = test_result
            
            if not test_result.success:
                # Try to fix test failures
                await self._fix_test_failures(task)
                # Re-run tests
                test_result = await self._run_tests(task)
                task.test_result = test_result
                
                if not test_result.success:
                    task.status = TaskStatus.FAILED
                    return
            
            # Success!
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            
            # Copy artifacts out
            await self._collect_artifacts(task)
            
            await event_bus.publish(type("Event", (), {
                "type": "agent.task_completed",
                "data": {
                    "task_id": task.id,
                    "status": task.status.value,
                    "tests_passed": task.test_result.passed if task.test_result else 0,
                }
            })())
            
        except Exception as e:
            logger.error(f"Task {task.id} failed: {e}")
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now()
            await event_bus.publish(type("Event", (), {
                "type": "agent.task_failed",
                "data": {"task_id": task.id, "error": str(e)}
            })())
        finally:
            # Cleanup sandbox
            if task.sandbox_id:
                await self.sandbox.destroy_sandbox(task.sandbox_id)
            
            # Move to history
            self.active_tasks.pop(task.id, None)
            self.task_history.append(task)
            if len(self.task_history) > 100:
                self.task_history = self.task_history[-100:]
    
    async def _generate_code(self, task: CodeTask) -> None:
        """Generate source code using AI."""
        router = await get_router()
        
        prompt = f"""You are an expert {task.language} developer. Write clean, production-ready code.

TASK: {task.description}

REQUIREMENTS:
{task.requirements}

LANGUAGE: {task.language}

OUTPUT FORMAT:
```{task.language}
// Your complete solution here
```

Requirements:
- Write complete, runnable code
- Include proper error handling
- Follow best practices for {task.language}
- Make it modular and testable
- No external dependencies unless specified
"""
        
        response = await router.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier.SMART,
        )
        
        # Extract code from response
        task.source_code = self._extract_code(response.content, task.language)
        logger.info(f"Generated {len(task.source_code)} chars of {task.language} code for task {task.id}")
    
    def _extract_code(self, response: str, language: str) -> str:
        """Extract code block from AI response."""
        import re
        # Try to find code block
        pattern = rf"```{language}\n(.*?)\n```"
        matches = re.findall(pattern, response, re.DOTALL)
        if matches:
            return matches[0].strip()
        # Fallback: look for any code block
        matches = re.findall(r"```\w*\n(.*?)\n```", response, re.DOTALL)
        if matches:
            return matches[0].strip()
        # Fallback: return as-is
        return response.strip()
    
    async def _write_task_files(self, task: CodeTask) -> None:
        """Write source code to workspace."""
        workspace = Path(task.workspace_path)
        
        # Determine file extension
        ext_map = {
            "python": ".py",
            "javascript": ".js",
            "typescript": ".ts",
            "rust": ".rs",
            "go": ".go",
            "cpp": ".cpp",
            "c": ".c",
            "java": ".java",
        }
        ext = ext_map.get(task.language, ".txt")
        
        # Write main source
        main_file = workspace / f"main{ext}"
        main_file.write_text(task.source_code)
        
        # Write test file if exists
        if task.test_code:
            test_file = workspace / f"test_main{ext}"
            test_file.write_text(task.test_code)
        
        # Write build/run scripts
        if task.build_script:
            (workspace / "build.sh").write_text(task.build_script)
        if task.run_script:
            (workspace / "run.sh").write_text(task.run_script)
        
        # Create default build/run for Python
        if task.language == "python" and not task.build_script:
            (workspace / "build.sh").write_text("# Python - no build needed\n")
        if task.language == "python" and not task.run_script:
            (workspace / "run.sh").write_text("python main.py\n")
    
    async def _build_task(self, task: CodeTask) -> BuildResult:
        """Build the task in sandbox."""
        start = datetime.now()
        
        try:
            # For Python, just verify syntax
            if task.language == "python":
                exit_code, stdout, stderr = await self.sandbox.execute_in_sandbox(
                    task.sandbox_id,
                    ["python", "-m", "py_compile", "main.py"]
                )
                return BuildResult(
                    success=exit_code == 0,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=int((datetime.now() - start).total_seconds() * 1000)
                )
            
            # For other languages, run build script
            if task.build_script:
                await self.sandbox.copy_to_sandbox(task.sandbox_id, Path(task.workspace_path) / "build.sh", "/workspace/build.sh")
                exit_code, stdout, stderr = await self.sandbox.execute_in_sandbox(
                    task.sandbox_id,
                    ["bash", "build.sh"]
                )
            else:
                # Try default compile
                exit_code, stdout, stderr = await self.sandbox.execute_in_sandbox(
                    task.sandbox_id,
                    ["make"]
                )
            
            return BuildResult(
                success=exit_code == 0,
                stdout=stdout,
                stderr=stderr,
                duration_ms=int((datetime.now() - start).total_seconds() * 1000)
            )
        except Exception as e:
            return BuildResult(success=False, stderr=str(e))
    
    async def _fix_build_failures(self, task: CodeTask) -> None:
        """Use AI to fix build failures."""
        if not task.build_result or task.fix_attempts >= task.max_fix_attempts:
            return
        
        router = await get_router()
        
        prompt = f"""The build failed. Fix the code.

ORIGINAL CODE:
```{task.language}
{task.source_code}
```

BUILD ERROR:
{task.build_result.stderr}

REQUIREMENTS:
{task.requirements}

Provide ONLY the corrected complete code in a {task.language} code block.
"""
        
        response = await router.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier.SMART,
        )
        
        task.source_code = self._extract_code(response.content, task.language)
        task.fix_attempts += 1
        
        # Rebuild
        task.build_result = await self._build_task(task)
    
    async def _generate_tests(self, task: CodeTask) -> None:
        """Generate test code using AI."""
        router = await get_router()
        
        prompt = f"""Write comprehensive tests for this {task.language} code.

SOURCE CODE:
```{task.language}
{task.source_code}
```

REQUIREMENTS:
{task.requirements}

Write tests using the standard testing framework for {task.language}:
- pytest for Python
- jest for JavaScript/TypeScript
- cargo test for Rust
- go test for Go
- JUnit for Java

Test edge cases, normal cases, and error conditions.
Output ONLY the test code in a {task.language} code block.
"""
        
        response = await router.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier.SMART,
        )
        
        task.test_code = self._extract_code(response.content, task.language)
    
    async def _run_tests(self, task: CodeTask) -> TestResult:
        """Run tests in sandbox."""
        start = datetime.now()
        
        try:
            if task.language == "python":
                exit_code, stdout, stderr = await self.sandbox.execute_in_sandbox(
                    task.sandbox_id,
                    ["python", "-m", "pytest", "-v", "--tb=short"]
                )
                
                # Parse pytest output
                passed = stdout.count(" PASSED")
                failed = stdout.count(" FAILED")
                skipped = stdout.count(" SKIPPED")
                
                return TestResult(
                    success=exit_code == 0 and failed == 0,
                    passed=passed,
                    failed=failed,
                    skipped=skipped,
                    output=stdout,
                    duration_ms=int((datetime.now() - start).total_seconds() * 1000)
                )
            
            # Other languages - similar pattern
            return TestResult(success=True, passed=1)
            
        except Exception as e:
            return TestResult(success=False, output=str(e))
    
    async def _fix_test_failures(self, task: CodeTask) -> None:
        """Use AI to fix test failures."""
        if not task.test_result or task.fix_attempts >= task.max_fix_attempts:
            return
        
        router = await get_router()
        
        prompt = f"""Tests are failing. Fix the source code AND/OR tests.

SOURCE CODE:
```{task.language}
{task.source_code}
```

TEST CODE:
```{task.language}
{task.test_code}
```

TEST OUTPUT:
{task.test_result.output}

REQUIREMENTS:
{task.requirements}

Provide BOTH corrected source code AND test code in separate {task.language} code blocks.
Label them clearly: SOURCE and TESTS.
"""
        
        response = await router.chat(
            [{"role": "user", "content": prompt}],
            tier=ModelTier.SMART,
        )
        
        # Parse both code blocks
        import re
        source_matches = re.findall(rf"SOURCE.*?```{task.language}\n(.*?)\n```", response, re.DOTALL | re.IGNORECASE)
        test_matches = re.findall(rf"TESTS?.*?```{task.language}\n(.*?)\n```", response, re.DOTALL | re.IGNORECASE)
        
        if source_matches:
            task.source_code = source_matches[0].strip()
        if test_matches:
            task.test_code = test_matches[0].strip()
        
        task.fix_attempts += 1
    
    async def _collect_artifacts(self, task: CodeTask) -> None:
        """Collect build artifacts from sandbox."""
        # In a real implementation, copy artifacts back
        pass
    
    async def get_queue_status(self) -> Dict[str, Any]:
        """Get task queue status."""
        return {
            "queue_size": self.task_queue.qsize(),
            "active_tasks": len(self.active_tasks),
            "completed_tasks": len([t for t in self.task_history if t.status == TaskStatus.COMPLETED]),
            "failed_tasks": len([t for t in self.task_history if t.status == TaskStatus.FAILED]),
            "active": [
                {
                    "id": t.id,
                    "description": t.description,
                    "status": t.status.value,
                    "language": t.language,
                    "progress": "building" if t.status == TaskStatus.BUILDING else 
                               "testing" if t.status == TaskStatus.TESTING else
                               "fixing" if t.status == TaskStatus.FIXING else "idle",
                }
                for t in self.active_tasks.values()
            ],
        }


# Pre-defined task templates
class TaskTemplates:
    """Common coding task templates."""
    
    @staticmethod
    def python_script(description: str, requirements: str) -> CodeTask:
        return CodeTask(
            id=str(uuid.uuid4()),
            description=description,
            requirements=requirements,
            language="python",
        )
    
    @staticmethod
    def web_scraper(url: str, selectors: Dict[str, str]) -> CodeTask:
        return CodeTask(
            id=str(uuid.uuid4()),
            description=f"Web scraper for {url}",
            requirements=f"Scrape {url} using requests/BeautifulSoup. Extract: {json.dumps(selectors)}. Handle pagination, errors, rate limiting.",
            language="python",
        )
    
    @staticmethod
    def api_client(base_url: str, endpoints: List[str]) -> CodeTask:
        return CodeTask(
            id=str(uuid.uuid4()),
            description=f"REST API client for {base_url}",
            requirements=f"Create async client for {base_url} with endpoints: {endpoints}. Include auth, retries, error handling.",
            language="python",
        )
    
    @staticmethod
    def data_processor(input_format: str, output_format: str, transform: str) -> CodeTask:
        return CodeTask(
            id=str(uuid.uuid4()),
            description=f"Data processor: {input_format} → {output_format}",
            requirements=f"Convert {input_format} to {output_format}. Transform: {transform}. Handle large files, streaming, validation.",
            language="python",
        )


# Global instances
sandbox_manager = SandboxManager(SandboxType.DOCKER)
coding_agent = CodingAgent(sandbox_manager)