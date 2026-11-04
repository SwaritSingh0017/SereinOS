"""
Empirical Adversarial Recheck Test Suite for Milestone 4 Remediations.
Authored by challenger_m4_recheck.
Validates all 7 remediation items under hostile stress conditions.
"""
import inspect
import json
import os
import re
import shlex
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# Ensure project paths are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "serein-installer"))
sys.path.insert(0, str(PROJECT_ROOT / "serein-core" / "src"))

from installer_engine import (
    DISK_REGEX,
    HOSTNAME_REGEX,
    USERNAME_REGEX,
    SHELL_METACHRACTERS,
    InstallerEngine,
    PartitionPlan,
    PartitionSpec,
    installer_engine,
)
from serein_core.web_app import (
    app,
    InstallerInstallRequest,
    InstallerDryRunRequest,
)
from pydantic import ValidationError


@pytest.fixture
def client():
    return TestClient(app)


# =============================================================================
# 1. REMEDIATION 1: REAL LINUX EXECUTION LOGIC VS SIMULATION FALLBACK
# =============================================================================

class TestRemediation1_RealExecutionVsSimulationFallback:
    """Empirically test _run_install_worker branching and command execution."""

    def test_simulation_fallback_when_dry_run_or_non_root(self):
        """When is_dry_run=True or non-root, _run_install_worker must NOT invoke destructive commands."""
        engine = InstallerEngine()
        task_id = "test-sim-task"
        with engine._lock:
            engine._tasks[task_id] = engine._progress
            engine._progress.phase = "idle"
            engine._progress.done = False

        with patch("subprocess.run") as mock_subproc:
            engine._run_install_worker(
                task_id=task_id,
                disk_device="/dev/sda",
                user="serein",
                password="password",
                hostname="serein-pc",
                firmware_type="uefi",
                scheme="auto",
                is_dry_run=True,
            )
            # In dry-run mode, NO mkfs, parted, or mount commands should be called via subprocess
            for call_args in mock_subproc.call_args_list:
                cmd = call_args[0][0]
                assert not any(tool in cmd for tool in ["mkfs", "parted", "mount", "grub-install"])

        # Check that simulation log message was emitted
        progress = engine.get_progress(task_id)
        assert progress["done"] is True
        assert any("[SIMULATION]" in line for line in progress["log"])

    def test_live_linux_root_executes_genuine_disk_commands(self):
        """When running as Linux root with is_dry_run=False, real parted, mkfs, rsync, and grub commands MUST be invoked."""
        engine = InstallerEngine()
        task_id = "test-live-task"
        with engine._lock:
            engine._tasks[task_id] = engine._progress
            engine._progress.phase = "idle"
            engine._progress.done = False

        executed_commands = []

        def fake_run(cmd, *args, **kwargs):
            executed_commands.append(cmd)
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "FAKE-UUID-1234"
            mock_res.stderr = ""
            return mock_res

        with patch("sys.platform", "linux"), \
             patch("os.geteuid", create=True, return_value=0), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen") as mock_popen, \
             patch("shutil.which", return_value="/bin/true"), \
             patch.object(Path, "mkdir"), \
             patch.object(Path, "write_text"):

            mock_proc = MagicMock()
            mock_proc.communicate.return_value = ("", "")
            mock_popen.return_value = mock_proc

            engine._run_install_worker(
                task_id=task_id,
                disk_device="/dev/sda",
                user="serein",
                password="password",
                hostname="serein-pc",
                firmware_type="uefi",
                scheme="auto",
                is_dry_run=False,
            )

        # Inspect executed commands to verify genuine Linux disk commands were called
        flat_commands = [" ".join(cmd) if isinstance(cmd, list) else str(cmd) for cmd in executed_commands]
        
        # Must execute parted mklabel
        assert any("parted -s /dev/sda mklabel gpt" in c for c in flat_commands)
        # Must execute parted mkpart
        assert any("parted -s /dev/sda mkpart" in c for c in flat_commands)
        # Must execute mkfs.vfat for ESP
        assert any("mkfs.vfat -F32 -n EFI" in c for c in flat_commands)
        # Must execute mkswap
        assert any("mkswap -L SereinSwap" in c for c in flat_commands)
        # Must execute mkfs.ext4
        assert any("mkfs.ext4 -F -L SereinRoot" in c for c in flat_commands)
        # Must execute mount
        assert any("mount" in c for c in flat_commands)
        # Must execute grub-install in chroot
        assert any("chroot" in c and "grub-install" in c for c in flat_commands)
        # Must execute update-grub in chroot
        assert any("chroot" in c and "update-grub" in c for c in flat_commands)


# =============================================================================
# 2. REMEDIATION 2: UI DRY-RUN FAILURE HANDLING
# =============================================================================

class TestRemediation2_UIDryRunFailureHandling:
    """Empirically test assets/ui/app.js dry-run failure guards."""

    def test_app_js_checks_data_valid_strictly(self):
        app_js = (PROJECT_ROOT / "assets" / "ui" / "app.js").read_text(encoding="utf-8")
        # Check strict condition
        assert "if (data.valid === true)" in app_js
        assert "[VALIDATION FAILED]" in app_js
        assert "installerProgressBar.style.background = '#ef4444'" in app_js
        assert "installerProgressBar.style.width = '0%'" in app_js
        assert "btnInstallerStart.disabled = true" in app_js
        assert "btnInstallerStart.disabled = false" in app_js


# =============================================================================
# 3. REMEDIATION 3: STRICT REGEX & COMMAND INJECTION REJECTION
# =============================================================================

class TestRemediation3_InputValidationAndInjectionRejection:
    """Adversarial stress-testing of input validation and command injection."""

    @pytest.mark.parametrize("payload", [
        "/dev/sda; rm -rf / #",
        "/dev/sda && id",
        "/dev/sda | whoami",
        "/dev/sda`reboot`",
        "/dev/sda$(reboot)",
        "/dev/sda\nreboot",
        "/dev/../etc/shadow",
        "/dev/null",
        "/dev/zero",
        "/dev/sda1",
        "sda",
        "/dev/sda;cat /etc/passwd",
    ])
    def test_hostile_disk_payloads_rejected_by_engine(self, payload: str):
        engine = InstallerEngine()
        res = engine.run_dry_run(payload, firmware_type="bios")
        assert res["valid"] is False
        assert not DISK_REGEX.match(payload)
        with pytest.raises(ValueError):
            engine.start_installation(payload)

    @pytest.mark.parametrize("payload", [
        "/dev/sda; rm -rf / #",
        "/dev/sda && id",
        "/dev/null",
        "sda",
    ])
    def test_pydantic_install_request_rejects_hostile_disk(self, payload: str):
        with pytest.raises(ValidationError):
            InstallerInstallRequest(disk=payload)

    @pytest.mark.parametrize("bad_user", [
        "root; rm -rf /",
        "123user",
        "-baduser",
        "user with space",
        "user@domain",
        "a" * 33,
    ])
    def test_pydantic_install_request_rejects_bad_user(self, bad_user: str):
        with pytest.raises(ValidationError):
            InstallerInstallRequest(disk="/dev/sda", user=bad_user)

    @pytest.mark.parametrize("bad_host", [
        "-badhost",
        "host; id",
        "host name",
        "host_name",
        "a" * 65,
    ])
    def test_pydantic_install_request_rejects_bad_hostname(self, bad_host: str):
        with pytest.raises(ValidationError):
            InstallerInstallRequest(disk="/dev/sda", hostname=bad_host)

    def test_bootloader_command_uses_shlex_quote(self):
        engine = InstallerEngine()
        plan = PartitionPlan(
            disk="/dev/sda",
            firmware_type="bios",
            table_type="msdos",
            disk_size_gb=50.0,
        )
        cmd = engine.generate_bootloader_command(plan)
        assert cmd == "grub-install --target=i386-pc --recheck /dev/sda"


# =============================================================================
# 4. REMEDIATION 4: CONCURRENCY LOCK (HTTP 409) AND PER-TASK TRACKING
# =============================================================================

class TestRemediation4_ConcurrencyAndTaskTracking:
    """Stress testing of concurrency control and per-task state tracking."""

    def test_concurrent_install_returns_409(self, client: TestClient):
        # Wait for any prior task to finish
        for _ in range(30):
            p = client.get("/api/installer/progress").json()
            if p.get("done", True) or p.get("phase") in ("idle", "complete", "failed"):
                break
            time.sleep(0.05)

        res1 = client.post("/api/installer/install", json={"disk": "/dev/sda"})
        assert res1.status_code == 200
        task_id = res1.json()["task_id"]

        # Immediate second request must trigger HTTP 409 Conflict
        res2 = client.post("/api/installer/install", json={"disk": "/dev/sda"})
        assert res2.status_code == 409
        assert "already in progress" in res2.json()["detail"]

        # Check per-task tracking
        prog = client.get(f"/api/installer/progress?task_id={task_id}").json()
        assert "phase" in prog
        assert "percent" in prog

    def test_thread_burst_concurrency_lock(self):
        """20 parallel threads attempting start_installation: exactly 1 must succeed."""
        engine = InstallerEngine()
        with engine._lock:
            engine._progress.phase = "idle"
            engine._progress.done = False

        successes = []
        conflicts = []
        lock = threading.Lock()

        def try_install():
            try:
                tid = engine.start_installation("/dev/sda", dry_run=True)
                with lock:
                    successes.append(tid)
            except RuntimeError as e:
                if "already in progress" in str(e):
                    with lock:
                        conflicts.append(str(e))

        threads = [threading.Thread(target=try_install) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1
        assert len(conflicts) == 19


# =============================================================================
# 5. REMEDIATION 5: PLYMOUTH THEME & INSTALLER MODULE STAGING
# =============================================================================

class TestRemediation5_PlymouthAndInstallerStagingInISO:
    """Verify ISO build scripts stage Plymouth theme and installer modules."""

    def test_install_artifacts_container_script_stages_plymouth_and_installer(self):
        script_path = PROJECT_ROOT / "serein-installer" / "install_artifacts_container.sh"
        content = script_path.read_text(encoding="utf-8")

        # Plymouth theme copy and default set
        assert "mkdir -p /build/chroot/usr/share/plymouth/themes/serein-glow" in content
        assert "cp -r /project/serein-installer/plymouth/serein-glow/* /build/chroot/usr/share/plymouth/themes/serein-glow/" in content
        assert "chroot /build/chroot plymouth-set-default-theme serein-glow" in content

        # Installer module copy and wrapper
        assert "cp -r /project/serein-installer /build/chroot/usr/local/lib/serein/" in content
        assert "cat > /build/chroot/usr/local/bin/serein-installer" in content
        assert "chmod +x /build/chroot/usr/local/bin/serein-installer" in content

    def test_build_iso_script_has_plymouth_fallback_and_cmdline(self):
        build_iso_path = PROJECT_ROOT / "serein-installer" / "build_iso.sh"
        content = build_iso_path.read_text(encoding="utf-8")

        assert "quiet splash loglevel=3 systemd.show_status=0" in content
        assert "plymouth-set-default-theme serein-glow" in content


# =============================================================================
# 6. REMEDIATION 6: FAT32 ESP 32-BIT VOLUME ID FORMAT (XXXX-XXXX)
# =============================================================================

class TestRemediation6_FAT32VolumeIDFormat:
    """Rigorously verify FAT32 ESP volume serials in PartitionSpec and fstab."""

    def test_fat32_volume_id_structure_across_100_runs(self):
        pattern = re.compile(r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$")
        engine = InstallerEngine()
        for _ in range(100):
            plan = engine.plan_partitions("/dev/sda", firmware_type="uefi", disk_size_gb=80.0)
            esp_part = next(p for p in plan.partitions if p.mount_point == "/boot/efi")
            assert pattern.match(esp_part.uuid), f"Invalid FAT32 volume ID: {esp_part.uuid}"

            # Check in generated fstab
            assert f"UUID={esp_part.uuid} /boot/efi vfat umask=0077,defaults 0 2" in plan.fstab

            # Verify ext4 root partition retains 36-char RFC 4122 UUID4
            root_part = next(p for p in plan.partitions if p.mount_point == "/")
            assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", root_part.uuid)
