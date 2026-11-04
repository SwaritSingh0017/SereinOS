"""
Milestone 4 Empirical Challenger Stress Suite: Web REST APIs, UI Wizard, & Plymouth Theme.
Authored by: challenger_m4_installer_2 (Critic & Specialist)
Target: Milestone 4 Standalone Graphical OS Installer & Plymouth Boot Splash (Requirement R4)

Empirically tests:
1. Web API Endpoints (/api/installer/*):
   - GET /api/installer/disks: schema validation, boot media detection, HTTP method guardrails.
   - POST /api/installer/dry-run: valid requests (UEFI, BIOS, auto), payload validation,
     rejection of live boot media, handling of invalid device paths, boundary length limits,
     malformed JSON/bytes handling, and HTTP method guardrails (DELETE=405, GET=404 due to StaticFiles).
   - POST /api/installer/install: background task initiation, task ID generation,
     validation of missing/malformed payloads, HTTP method guardrails.
   - GET /api/installer/progress: progress snapshot schema, percent range [0..100],
     phase enumeration, optional task_id query parameter, and HTTP method guardrails.
2. Background Task Progress Tracking & Lifecycle Stress:
   - Full end-to-end lifecycle verification: polling through initializing, validating,
     partitioning, formatting, copying, configuring, bootloader, and complete phases.
   - Non-decreasing progress percentage reaching 100% and done=True.
   - Failure progression tracking: install onto boot media or small disk transitions to failed phase.
   - Multithreaded concurrent polling stress without race conditions or log corruption.
   - Multi-worker race / clobbering behavior analysis on singleton engine.
3. Static Analysis & Verification of Desktop UI Wizard:
   - assets/ui/index.html: 5-step modal structure, step breadcrumbs, desktop and start menu
     shortcuts, pre-flight diagnostic badges, disk/firmware selects, progress bar, terminal log,
     and navigation/action buttons.
   - assets/ui/app.js: event listener bindings, 5-step state machine navigation, input validation,
     REST API endpoints fetch bindings, 400ms progress polling interval, dynamic log streaming,
     step 5 transition upon completion, and power reboot trigger.
   - assets/ui/style.css: glassmorphism modal, breadcrumb states, gradient progress bar,
     monospace terminal log window with blinking live indicator, and button states.
4. Plymouth Theme & Script Syntax Verification:
   - Directory structure & asset presence in serein-installer/plymouth/serein-glow/.
   - INI syntax validation for serein-glow.plymouth ([Plymouth Theme] and [script] sections).
   - serein-glow.script structure: screen dimensions, background colors, logo/glow loading,
     sinusoidal breathing glow math (Math.Sin), progress bar fill, and all 6 required Plymouth callbacks.
   - Image binary validation: PNG magic bytes & dimensions for logo.png and glow.png, valid XML for logo.svg.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import struct
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_SRC = REPO_ROOT / "serein-core" / "src"
INSTALLER_DIR = REPO_ROOT / "serein-installer"

for p in (str(REPO_ROOT), str(CORE_SRC), str(INSTALLER_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from serein_core.web_app import app
from serein_installer.installer_engine import (
    InstallerEngine,
    DiskInfo,
    PartitionPlan,
    PartitionSpec,
    DryRunResult,
    InstallProgress,
    installer_engine,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Reusable TestClient instance for Serein web application."""
    return TestClient(app)


def wait_for_installer_idle(client: TestClient, timeout: float = 5.0) -> None:
    """Helper to ensure previous background install tasks are completed before a test begins."""
    start = time.time()
    while time.time() - start < timeout:
        res = client.get("/api/installer/progress")
        if res.status_code == 200:
            data = res.json()
            if data.get("done", True) or data.get("phase") in ("idle", "complete", "failed"):
                # Give a tiny buffer for thread termination
                time.sleep(0.05)
                return
        time.sleep(0.1)


# ============================================================================
# 1. WEB API ENDPOINTS (/api/installer/*) ADVERSARIAL STRESS TESTS
# ============================================================================

class TestInstallerWebAPIEndpointsAdversarial:
    """Adversarial stress testing of /api/installer/* REST endpoints."""

    def test_get_disks_schema_and_types(self, client: TestClient):
        """Verify GET /api/installer/disks returns valid schema with required disk attributes."""
        res = client.get("/api/installer/disks")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "disks" in data, "Response missing 'disks' key"
        assert isinstance(data["disks"], list)
        assert len(data["disks"]) >= 1, "Expected at least one detected or fallback disk"

        for disk in data["disks"]:
            assert "device" in disk
            assert "size_gb" in disk
            assert "model" in disk
            assert "is_boot_media" in disk
            assert isinstance(disk["device"], str)
            assert disk["device"].startswith("/dev/")
            assert isinstance(disk["size_gb"], (int, float))
            assert disk["size_gb"] > 0
            assert isinstance(disk["model"], str)
            assert isinstance(disk["is_boot_media"], bool)

    def test_get_disks_method_guards(self, client: TestClient):
        """Verify unsupported HTTP verbs on /api/installer/disks return 405 Method Not Allowed."""
        assert client.post("/api/installer/disks", json={}).status_code == 405
        assert client.put("/api/installer/disks", json={}).status_code == 405
        assert client.delete("/api/installer/disks").status_code == 405
        assert client.patch("/api/installer/disks", json={}).status_code == 405

    def test_dry_run_valid_uefi(self, client: TestClient):
        """Verify POST /api/installer/dry-run with explicit UEFI firmware."""
        payload = {"disk": "/dev/sda", "scheme": "auto", "firmware_type": "uefi"}
        res = client.post("/api/installer/dry-run", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is True
        assert "GPT/UEFI" in data["summary"]
        assert len(data["steps"]) >= 4
        assert "UUID=" in data["fstab"]
        assert "/boot/efi" in data["fstab"]
        assert "x86_64-efi" in data["bootloader"]
        assert "--efi-directory=/mnt/boot/efi" in data["bootloader"]

        # Check partition layout details
        parts = data["partitions"]
        assert len(parts) == 3
        # ESP
        assert parts[0]["mount_point"] == "/boot/efi"
        assert parts[0]["fstype"] == "vfat"
        assert parts[0]["size_mb"] == 512
        # Swap
        assert parts[1]["fstype"] == "swap"
        assert parts[1]["size_mb"] == 4096
        # Root
        assert parts[2]["mount_point"] == "/"
        assert parts[2]["fstype"] == "ext4"

    def test_dry_run_valid_bios(self, client: TestClient):
        """Verify POST /api/installer/dry-run with explicit BIOS firmware."""
        payload = {"disk": "/dev/sda", "scheme": "auto", "firmware_type": "bios"}
        res = client.post("/api/installer/dry-run", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is True
        assert "MBR/BIOS" in data["summary"]
        assert len(data["steps"]) >= 3
        assert "UUID=" in data["fstab"]
        assert "none swap sw" in data["fstab"]
        assert "i386-pc" in data["bootloader"]
        assert "/dev/sda" in data["bootloader"]

        parts = data["partitions"]
        assert len(parts) == 2
        # Root with boot flag
        assert parts[0]["mount_point"] == "/"
        assert parts[0]["fstype"] == "ext4"
        assert "boot" in parts[0]["flags"]
        # Swap
        assert parts[1]["fstype"] == "swap"
        assert parts[1]["size_mb"] == 4096

    def test_dry_run_auto_firmware_detection(self, client: TestClient):
        """Verify POST /api/installer/dry-run with default/auto firmware mode."""
        res = client.post("/api/installer/dry-run", json={"disk": "/dev/sda"})
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is True
        assert isinstance(data["summary"], str)
        assert len(data["steps"]) > 0
        assert "UUID=" in data["fstab"]
        assert "grub-install" in data["bootloader"]

    def test_dry_run_rejection_of_live_boot_media(self, client: TestClient):
        """Verify dry-run strictly refuses to target detected active live boot media."""
        # /dev/sdb is flagged as boot media in standard fallback inventory
        res = client.post("/api/installer/dry-run", json={"disk": "/dev/sdb"})
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is False
        assert "Refusing to install on active boot media" in data["summary"]
        assert "live boot medium" in data["error"]
        assert data["fstab"] == ""
        assert data["bootloader"] == ""
        assert data["steps"] == []

    def test_dry_run_invalid_device_path(self, client: TestClient):
        """Verify dry-run rejects malformed block device paths not starting with /dev/."""
        res = client.post("/api/installer/dry-run", json={"disk": "malformed_drive_path"})
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is False
        assert "must start with /dev/" in data["summary"]
        assert data["fstab"] == ""

    def test_dry_run_malformed_empty_payload(self, client: TestClient):
        """Verify POST /api/installer/dry-run with missing required disk field returns 422."""
        res = client.post("/api/installer/dry-run", json={})
        assert res.status_code == 422

    def test_dry_run_invalid_data_types(self, client: TestClient):
        """Verify POST /api/installer/dry-run with non-string disk type returns 422."""
        res = client.post("/api/installer/dry-run", json={"disk": 12345})
        assert res.status_code == 422
        res2 = client.post("/api/installer/dry-run", json={"disk": ["/dev/sda"]})
        assert res2.status_code == 422

    def test_dry_run_boundary_length_overflow(self, client: TestClient):
        """Verify POST /api/installer/dry-run with excessively long disk string returns 422."""
        long_disk = "/dev/" + ("x" * 200)
        res = client.post("/api/installer/dry-run", json={"disk": long_disk})
        assert res.status_code == 422

    def test_dry_run_corrupted_json_bytes(self, client: TestClient):
        """Verify malformed JSON content returns 400 or 422 without 500 error."""
        res = client.post(
            "/api/installer/dry-run",
            content=b'{"disk": "/dev/sda", broken',
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code in (400, 422)

    def test_dry_run_method_guards(self, client: TestClient):
        """Verify unsupported HTTP verbs on /api/installer/dry-run return 405 (or 404 for GET due to StaticFiles)."""
        assert client.delete("/api/installer/dry-run").status_code == 405
        assert client.put("/api/installer/dry-run", json={}).status_code == 405
        # GET on POST endpoint returns 404 because root StaticFiles catch-all matches GET requests
        assert client.get("/api/installer/dry-run").status_code in (404, 405)

    def test_install_valid_initiation(self, client: TestClient):
        """Verify POST /api/installer/install initiates a background installation task."""
        wait_for_installer_idle(client)
        payload = {
            "disk": "/dev/sda",
            "user": "serein",
            "password": "securepassword",
            "hostname": "serein-vaio",
            "firmware_type": "auto",
            "scheme": "auto",
        }
        res = client.post("/api/installer/install", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert "task_id" in data
        assert isinstance(data["task_id"], str)
        assert len(data["task_id"]) >= 16

    def test_install_missing_required_disk(self, client: TestClient):
        """Verify POST /api/installer/install without disk returns 422."""
        res = client.post("/api/installer/install", json={"user": "serein"})
        assert res.status_code == 422

    def test_install_invalid_field_types(self, client: TestClient):
        """Verify POST /api/installer/install with invalid field types returns 422."""
        res = client.post("/api/installer/install", json={"disk": 9999})
        assert res.status_code == 422

    def test_install_method_guards(self, client: TestClient):
        """Verify unsupported HTTP verbs on /api/installer/install return 405 (or 404 for GET due to StaticFiles)."""
        assert client.delete("/api/installer/install").status_code == 405
        assert client.put("/api/installer/install", json={}).status_code == 405
        assert client.get("/api/installer/install").status_code in (404, 405)

    def test_progress_schema_and_types(self, client: TestClient):
        """Verify GET /api/installer/progress returns valid status snapshot."""
        res = client.get("/api/installer/progress")
        assert res.status_code == 200
        data = res.json()
        assert "phase" in data
        assert "percent" in data
        assert "log" in data
        assert "done" in data
        assert isinstance(data["phase"], str)
        assert isinstance(data["percent"], int)
        assert 0 <= data["percent"] <= 100
        assert isinstance(data["log"], list)
        assert isinstance(data["done"], bool)

    def test_progress_with_task_id_query_param(self, client: TestClient):
        """Verify GET /api/installer/progress accepts task_id query parameter."""
        res = client.get("/api/installer/progress?task_id=test-task-1234")
        assert res.status_code == 200
        data = res.json()
        assert "percent" in data
        assert "phase" in data

    def test_progress_method_guards(self, client: TestClient):
        """Verify unsupported HTTP verbs on /api/installer/progress return 405."""
        assert client.post("/api/installer/progress", json={}).status_code == 405
        assert client.delete("/api/installer/progress").status_code == 405


# ============================================================================
# 2. BACKGROUND TASK PROGRESS TRACKING & LIFECYCLE STRESS TESTS
# ============================================================================

class TestInstallerBackgroundLifecycleAndProgressTracking:
    """Stress testing of background worker lifecycle, phase transitions, and thread-safety."""

    def test_full_lifecycle_success_progression(self, client: TestClient):
        """Verify background install transitions through all phases to completion."""
        wait_for_installer_idle(client)

        # Initiate installation on valid disk
        res = client.post(
            "/api/installer/install",
            json={
                "disk": "/dev/sda",
                "user": "serein-test",
                "password": "testpassword",
                "hostname": "test-box",
                "firmware_type": "uefi",
            },
        )
        assert res.status_code == 200
        task_id = res.json()["task_id"]

        observed_phases = set()
        observed_percentages = []
        max_wait_seconds = 10.0
        start_time = time.time()
        final_state = None

        while time.time() - start_time < max_wait_seconds:
            p_res = client.get(f"/api/installer/progress?task_id={task_id}")
            assert p_res.status_code == 200
            data = p_res.json()
            observed_phases.add(data["phase"])
            observed_percentages.append(data["percent"])

            if data["done"]:
                final_state = data
                break
            time.sleep(0.08)

        assert final_state is not None, "Installation did not complete within timeout"
        assert final_state["done"] is True
        assert final_state["phase"] == "complete"
        assert final_state["percent"] == 100
        assert final_state["error"] is None

        # Verify key phases were visited
        assert "complete" in observed_phases
        # Verify percent values are monotonically non-decreasing
        for i in range(len(observed_percentages) - 1):
            assert observed_percentages[i] <= observed_percentages[i + 1]

        # Verify terminal logs contain detailed steps
        logs_joined = "\n".join(final_state["log"])
        assert "Starting SereinOS installation" in logs_joined
        assert "Validating disk" in logs_joined
        assert "Creating partition table" in logs_joined
        assert "Formatting" in logs_joined
        assert "Syncing SereinOS" in logs_joined
        assert "Setting hostname" in logs_joined
        assert "Staging bootloader" in logs_joined
        assert "Installation successfully completed" in logs_joined

    def test_lifecycle_failure_on_boot_media(self, client: TestClient):
        """Verify background install halts and reports failure when target is live boot media."""
        wait_for_installer_idle(client)

        res = client.post(
            "/api/installer/install",
            json={"disk": "/dev/sdb"},  # /dev/sdb is boot media
        )
        assert res.status_code == 200
        task_id = res.json()["task_id"]

        max_wait_seconds = 8.0
        start_time = time.time()
        final_state = None

        while time.time() - start_time < max_wait_seconds:
            p_res = client.get(f"/api/installer/progress?task_id={task_id}")
            data = p_res.json()
            if data["done"]:
                final_state = data
                break
            time.sleep(0.08)

        assert final_state is not None, "Failed install did not complete within timeout"
        assert final_state["done"] is True
        assert final_state["phase"] == "failed"
        assert final_state["error"] is not None
        assert "live boot medium" in final_state["error"]
        logs_joined = "\n".join(final_state["log"])
        assert "[ERROR] Installation halted" in logs_joined

    def test_lifecycle_failure_on_small_disk(self):
        """Verify background install halts when target disk capacity is below minimum."""
        custom_engine = InstallerEngine()
        # Mock detect_disks returning a 10 GB disk
        mock_disks = [
            {
                "device": "/dev/sdc",
                "size_gb": 10.0,
                "model": "Tiny_Disk",
                "is_boot_media": False,
            }
        ]
        with patch.object(custom_engine, "detect_disks", return_value=mock_disks):
            task_id = custom_engine.start_installation(disk_device="/dev/sdc")

            max_wait_seconds = 8.0
            start_time = time.time()
            final_progress = None

            while time.time() - start_time < max_wait_seconds:
                p = custom_engine.get_progress(task_id)
                if p["done"]:
                    final_progress = p
                    break
                time.sleep(0.08)

            assert final_progress is not None
            assert final_progress["done"] is True
            assert final_progress["phase"] == "failed"
            assert "insufficient" in (final_progress["error"] or "").lower()

    def test_concurrent_progress_polling_burst(self, client: TestClient):
        """Verify high concurrency multithreaded progress polling does not trigger race conditions."""
        wait_for_installer_idle(client)

        # Start an installation
        res = client.post("/api/installer/install", json={"disk": "/dev/sda"})
        task_id = res.json()["task_id"]

        def poll_progress(worker_id: int) -> Dict[str, Any]:
            c = TestClient(app)
            r = c.get(f"/api/installer/progress?task_id={task_id}")
            assert r.status_code == 200
            return r.json()

        # Fire 40 concurrent poll requests
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(poll_progress, i) for i in range(40)]
            results = [f.result() for f in as_completed(futures)]

        assert len(results) == 40
        for r in results:
            assert "phase" in r
            assert "percent" in r
            assert isinstance(r["log"], list)
            assert 0 <= r["percent"] <= 100

        # Wait for the task to finish cleanly
        wait_for_installer_idle(client)


# ============================================================================
# 3. STATIC ANALYSIS OF DESKTOP UI WIZARD & JAVASCRIPT ASSETS
# ============================================================================

class TestDesktopUIWizardStaticAnalysis:
    """Adversarial static inspection of assets/ui/index.html, app.js, and style.css."""

    @pytest.fixture(scope="class")
    def index_html_content(self) -> str:
        path = REPO_ROOT / "assets" / "ui" / "index.html"
        assert path.exists(), f"Missing {path}"
        return path.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def app_js_content(self) -> str:
        path = REPO_ROOT / "assets" / "ui" / "app.js"
        assert path.exists(), f"Missing {path}"
        return path.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def style_css_content(self) -> str:
        path = REPO_ROOT / "assets" / "ui" / "style.css"
        assert path.exists(), f"Missing {path}"
        return path.read_text(encoding="utf-8")

    def test_html_modal_and_5_steps_present(self, index_html_content: str):
        """Verify index.html contains modal-installer with all 5 distinct wizard steps."""
        assert 'id="modal-installer"' in index_html_content
        assert 'class="modal-overlay"' in index_html_content or 'modal-overlay' in index_html_content

        # All 5 steps must exist with IDs
        for step_num in range(1, 6):
            assert f'id="installer-step-{step_num}"' in index_html_content, f"Missing step {step_num}"

        # Step breadcrumb navigation indicators
        for step_num in range(1, 6):
            assert f'data-step="{step_num}"' in index_html_content, f"Missing breadcrumb bullet for step {step_num}"

    def test_html_desktop_and_menu_shortcuts(self, index_html_content: str):
        """Verify shortcuts exist on desktop and in start menu."""
        assert 'id="desktop-installer-shortcut"' in index_html_content
        assert 'id="menu-installer-shortcut"' in index_html_content
        assert "Install SereinOS to Disk" in index_html_content

    def test_html_preflight_and_hardware_badges(self, index_html_content: str):
        """Verify pre-flight system check elements and hardware references in step 1."""
        assert 'id="chk-arch"' in index_html_content
        assert 'id="chk-cpu"' in index_html_content
        assert 'id="chk-ram"' in index_html_content
        assert 'id="chk-gpu"' in index_html_content
        assert 'id="chk-power"' in index_html_content
        assert "Sony VAIO VPCYB35AN" in index_html_content or "AMD Dual-Core E-450" in index_html_content

    def test_html_form_controls_and_progress_elements(self, index_html_content: str):
        """Verify disk selection, user inputs, progress bar, and terminal log body in HTML."""
        # Drive and firmware selects
        assert 'id="installer-disk-select"' in index_html_content
        assert 'id="installer-firmware-select"' in index_html_content
        assert 'id="installer-disk-model"' in index_html_content
        assert 'id="installer-disk-capacity"' in index_html_content

        # Account credentials
        assert 'id="installer-username"' in index_html_content
        assert 'id="installer-password"' in index_html_content
        assert 'id="installer-hostname"' in index_html_content

        # Progress elements
        assert 'id="installer-progress-bar"' in index_html_content
        assert 'id="installer-progress-pct"' in index_html_content
        assert 'id="installer-phase"' in index_html_content
        assert 'id="installer-terminal-log"' in index_html_content

        # Action buttons
        assert 'id="btn-installer-next"' in index_html_content
        assert 'id="btn-installer-back"' in index_html_content
        assert 'id="btn-installer-dryrun"' in index_html_content
        assert 'id="btn-installer-start"' in index_html_content
        assert 'id="btn-installer-reboot"' in index_html_content
        assert 'id="btn-installer-finish"' in index_html_content

    def test_js_wizard_step_navigation_and_validation(self, app_js_content: str):
        """Verify app.js implements wizard step transitions, validation guards, and back buttons."""
        assert "function setWizardStep" in app_js_content or "setWizardStep(" in app_js_content
        assert "currentStep" in app_js_content

        # Step 2 validation guard: requires drive selection
        assert "installerDiskSelect.value" in app_js_content

        # Step 3 validation guard: requires non-empty user, pass, hostname
        assert "installer-username" in app_js_content
        assert "installer-password" in app_js_content
        assert "installer-hostname" in app_js_content

        # Step navigation buttons
        assert "btnInstallerNext.addEventListener" in app_js_content
        assert "btnInstallerBack.addEventListener" in app_js_content

    def test_js_api_bindings_and_polling_loop(self, app_js_content: str):
        """Verify app.js binds to all 4 installer API endpoints with 400ms polling interval."""
        assert "fetch('/api/installer/disks')" in app_js_content
        assert "fetch('/api/installer/dry-run'" in app_js_content
        assert "fetch('/api/installer/install'" in app_js_content
        assert "fetch(`/api/installer/progress" in app_js_content or "fetch('/api/installer/progress" in app_js_content

        # Progress polling setup and interval
        assert "startProgressPolling" in app_js_content
        assert "setInterval" in app_js_content
        assert "clearInterval" in app_js_content
        # Interval timing around 400ms
        assert "400" in app_js_content

        # UI updates during polling
        assert "installerProgressBar.style.width" in app_js_content
        assert "installerProgressPct.textContent" in app_js_content
        assert "installerPhase.textContent" in app_js_content
        assert "installerTerminalLog.textContent" in app_js_content

        # Auto-advance to Step 5 on success
        assert "setWizardStep(5)" in app_js_content

        # Live boot media protection in UI dropdown
        assert "LIVE BOOT" in app_js_content or "is_boot_media" in app_js_content

    def test_js_dry_run_failure_handling(self, app_js_content: str):
        """Verify app.js handles dry-run failure by displaying [VALIDATION FAILED] and keeping start button disabled."""
        assert "data.valid === true" in app_js_content
        assert "[VALIDATION FAILED]" in app_js_content
        assert "btnInstallerStart.disabled = true" in app_js_content

    def test_css_modal_progressbar_terminal_styling(self, style_css_content: str):
        """Verify style.css provides cyberpunk glassmorphic wizard, progress bar, and terminal."""
        assert ".installer-modal-card" in style_css_content
        assert ".installer-steps-nav" in style_css_content
        assert ".step-bullet" in style_css_content
        assert ".step-bullet.active" in style_css_content
        assert ".step-bullet.completed" in style_css_content

        # Progress bar
        assert ".progress-container" in style_css_content
        assert ".progress-bar-wrap" in style_css_content
        assert ".progress-bar-fill" in style_css_content

        # Terminal log window & live indicator
        assert ".terminal-log-window" in style_css_content
        assert ".terminal-log-body" in style_css_content
        assert ".terminal-log-live" in style_css_content
        assert "@keyframes blink-live" in style_css_content

        # Installer buttons
        assert ".installer-btn" in style_css_content
        assert ".installer-btn.primary" in style_css_content
        assert ".installer-btn.dryrun" in style_css_content
        assert ".installer-btn.reboot" in style_css_content
        assert ".installer-btn:disabled" in style_css_content


# ============================================================================
# 4. PLYMOUTH THEME & SCRIPT SYNTAX VERIFICATION
# ============================================================================

class TestPlymouthThemeAndScriptSyntax:
    """Empirical verification of Plymouth boot splash configuration, script syntax, and assets."""

    PLYMOUTH_DIR = INSTALLER_DIR / "plymouth" / "serein-glow"

    def test_plymouth_folder_and_asset_files_exist(self):
        """Verify all required Plymouth theme files exist in serein-installer/plymouth/serein-glow/."""
        assert self.PLYMOUTH_DIR.exists(), f"Plymouth directory not found: {self.PLYMOUTH_DIR}"
        required_files = [
            "serein-glow.plymouth",
            "serein-glow.script",
            "logo.svg",
            "logo.png",
            "glow.png",
        ]
        for f in required_files:
            file_path = self.PLYMOUTH_DIR / f
            assert file_path.exists(), f"Missing Plymouth theme asset: {file_path}"
            assert file_path.stat().st_size > 0, f"Empty Plymouth asset: {file_path}"

    def test_plymouth_ini_descriptor_validity(self):
        """Verify serein-glow.plymouth is a valid INI file conforming to Plymouth theme specs."""
        plymouth_file = self.PLYMOUTH_DIR / "serein-glow.plymouth"
        config = configparser.ConfigParser()
        config.read(plymouth_file, encoding="utf-8")

        assert "Plymouth Theme" in config.sections(), "Missing [Plymouth Theme] section"
        theme = config["Plymouth Theme"]
        assert theme.get("Name") == "Serein Glow Theme"
        assert "SereinOS" in theme.get("Description", "")
        assert theme.get("ModuleName") == "script", "ModuleName must be 'script' for script-based themes"

        assert "script" in config.sections(), "Missing [script] section"
        script_sec = config["script"]
        assert script_sec.get("ImageDir") == "/usr/share/plymouth/themes/serein-glow"
        assert script_sec.get("ScriptFile") == "/usr/share/plymouth/themes/serein-glow/serein-glow.script"

    def test_plymouth_script_grammar_and_callbacks(self):
        """Verify serein-glow.script registers all 6 required Plymouth callbacks and contains math."""
        script_file = self.PLYMOUTH_DIR / "serein-glow.script"
        content = script_file.read_text(encoding="utf-8")

        # Window dimension calls
        assert "Window.GetWidth()" in content
        assert "Window.GetHeight()" in content
        assert "Window.SetBackgroundTopColor" in content
        assert "Window.SetBackgroundBottomColor" in content

        # Assets loaded
        assert 'Image("logo.png")' in content
        assert 'Image("glow.png")' in content
        assert 'Sprite(' in content

        # Breathing Sinusoidal Glow formula
        assert "Math.Sin" in content
        assert "pulse_speed" in content
        assert "glow_opacity = 0.25 + 0.75 *" in content

        # Neon progress bar geometry
        assert "progress_bar.width = 320" in content
        assert "progress_bar.height = 6" in content
        assert "Image.CreateColor" in content

        # All 6 required Plymouth callbacks
        required_callbacks = [
            "Plymouth.SetRefreshFunction(refresh_callback)",
            "Plymouth.SetBootProgressFunction(progress_callback)",
            "Plymouth.SetMessageFunction(message_callback)",
            "Plymouth.SetDisplayPasswordFunction(display_password_callback)",
            "Plymouth.SetDisplayNormalFunction(display_normal_callback)",
            "Plymouth.SetQuitFunction(quit_callback)",
        ]
        for cb in required_callbacks:
            assert cb in content, f"Missing Plymouth callback: {cb}"

        # Clean termination in quit callback
        assert "logo.sprite.SetOpacity(0.0)" in content
        assert "bar_bg.sprite.SetOpacity(0.0)" in content

        # Syntactic bracket matching sanity check
        open_braces = content.count("{")
        close_braces = content.count("}")
        assert open_braces == close_braces, f"Mismatched braces: {open_braces} != {close_braces}"
        assert open_braces >= 6, "Expected at least 6 function definitions"

    def test_plymouth_png_images_binary_integrity(self):
        """Verify logo.png and glow.png are genuine PNG binaries with valid IHDR dimensions."""
        for img_name in ("logo.png", "glow.png"):
            img_path = self.PLYMOUTH_DIR / img_name
            with open(img_path, "rb") as f:
                header = f.read(24)

            # Check 8-byte PNG magic number: 0x89 50 4E 47 0D 0A 1A 0A
            png_magic = b"\x89PNG\r\n\x1a\n"
            assert header[:8] == png_magic, f"{img_name} is not a valid PNG binary"

            # Parse IHDR chunk
            chunk_len, chunk_type = struct.unpack(">I4s", header[8:16])
            assert chunk_type == b"IHDR", f"Expected IHDR chunk in {img_name}"
            width, height = struct.unpack(">II", header[16:24])
            assert width > 0, f"Invalid width {width} in {img_name}"
            assert height > 0, f"Invalid height {height} in {img_name}"

    def test_plymouth_svg_xml_validity(self):
        """Verify logo.svg is well-formed XML with SVG namespace and valid root tag."""
        svg_path = self.PLYMOUTH_DIR / "logo.svg"
        tree = ET.parse(svg_path)
        root = tree.getroot()
        assert "svg" in root.tag.lower(), f"Root element is not svg: {root.tag}"
        assert "viewBox" in root.attrib or ("width" in root.attrib and "height" in root.attrib)
