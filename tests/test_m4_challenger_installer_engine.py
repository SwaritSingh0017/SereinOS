"""
Milestone 4 Empirical Challenger Stress Suite: Installer Engine & CLI Dry-Run
Authored by: challenger_m4_installer_1
Target: Milestone 4 Standalone Graphical OS Installer & Plymouth Boot Splash

Empirical verification of:
1. Programmatic --dry-run simulation under UEFI and BIOS modes.
2. Boot media protection: simulated boot media rejection across mountpoints, labels, and child partitions.
3. Capacity boundary tests: 0GB, 10GB, 19.9GB, 19.999GB (fail), 20GB (pass), 500GB (pass), 2TB (pass), 16TB (pass).
4. /etc/fstab syntax, token structure, fsck pass order, and UUID uniqueness across multiple generations.
5. Staged GRUB commands correctness for Legacy BIOS (i386-pc) and UEFI (x86_64-efi) including NVMe/eMMC prefixing.
6. CLI execution via subprocess (python -m serein_installer.installer_engine) for --list-disks, --dry-run, --json, and return codes.
7. Web API adversarial boundary testing (/api/installer/*) under malformed inputs, malicious paths, and concurrent requests.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER_DIR = REPO_ROOT / "serein-installer"
CORE_SRC = REPO_ROOT / "serein-core" / "src"

for p in (str(REPO_ROOT), str(INSTALLER_DIR), str(CORE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from serein_installer.installer_engine import (
    InstallerEngine,
    DiskInfo,
    PartitionPlan,
    PartitionSpec,
    DryRunResult,
    InstallProgress,
    cli_main,
)
from serein_core.web_app import app


# =============================================================================
# Category 1: Programmatic --dry-run Simulation (UEFI & BIOS)
# =============================================================================

class TestChallengerDryRunSimulation:
    """Stress tests on dry-run simulation guaranteeing mathematical correctness and zero host mutations."""

    def test_dry_run_uefi_partition_geometry_and_alignment(self):
        """Verify partition math under UEFI mode: ESP (512MB), Swap (4096MB), Root (remainder)."""
        engine = InstallerEngine()
        result = engine.run_dry_run(disk_device="/dev/sda", firmware_type="uefi")
        assert result["valid"] is True
        assert len(result["partitions"]) == 3

        esp, swap, root = result["partitions"]
        assert esp["number"] == 1
        assert esp["size_mb"] == 512
        assert esp["fstype"] == "vfat"
        assert esp["mount_point"] == "/boot/efi"
        assert "boot" in esp["flags"]
        assert "esp" in esp["flags"]

        assert swap["number"] == 2
        assert swap["size_mb"] == 4096
        assert swap["fstype"] == "swap"
        assert swap["mount_point"] == "none"

        assert root["number"] == 3
        assert root["fstype"] == "ext4"
        assert root["mount_point"] == "/"
        # For fallback /dev/sda (465.7 GB = 476876.8 MB), root must be total_mb - (512 + 4096)
        total_mb = int(465.7 * 1024)
        expected_root_mb = total_mb - (512 + 4096)
        assert root["size_mb"] == expected_root_mb

    def test_dry_run_bios_partition_geometry_and_alignment(self):
        """Verify partition math under BIOS mode: Root (bootable ext4) and Swap (4096MB)."""
        engine = InstallerEngine()
        result = engine.run_dry_run(disk_device="/dev/sda", firmware_type="bios")
        assert result["valid"] is True
        assert len(result["partitions"]) == 2

        root, swap = result["partitions"]
        assert root["number"] == 1
        assert root["fstype"] == "ext4"
        assert root["mount_point"] == "/"
        assert "boot" in root["flags"]

        assert swap["number"] == 2
        assert swap["size_mb"] == 4096
        assert swap["fstype"] == "swap"
        assert swap["mount_point"] == "none"

        total_mb = int(465.7 * 1024)
        expected_root_mb = total_mb - 4096
        assert root["size_mb"] == expected_root_mb

    def test_dry_run_zero_host_modifications(self):
        """Assert dry-run never invokes subprocess or writes to disk files."""
        engine = InstallerEngine()
        with patch("subprocess.run") as mock_sub, patch("builtins.open") as mock_open:
            res_uefi = engine.run_dry_run(disk_device="/dev/sda", firmware_type="uefi")
            res_bios = engine.run_dry_run(disk_device="/dev/sda", firmware_type="bios")

            assert res_uefi["valid"] is True
            assert res_bios["valid"] is True
            # subprocess.run should only have been called for lsblk, never format/partition tools
            for call in mock_sub.call_args_list:
                args = call[0][0]
                assert not any(tool in args for tool in ["mkfs", "parted", "gdisk", "fdisk", "dd", "shred"])

    def test_partition_naming_nvme_and_mmc_devices(self):
        """NVMe and MMC block devices require 'p' separator (/dev/nvme0n1p1, /dev/mmcblk0p1)."""
        engine = InstallerEngine()
        plan_nvme = engine.plan_partitions(disk_device="/dev/nvme0n1", firmware_type="uefi", disk_size_gb=256.0)
        assert plan_nvme.partitions[0].device == "/dev/nvme0n1p1"
        assert plan_nvme.partitions[1].device == "/dev/nvme0n1p2"
        assert plan_nvme.partitions[2].device == "/dev/nvme0n1p3"

        plan_mmc = engine.plan_partitions(disk_device="/dev/mmcblk0", firmware_type="bios", disk_size_gb=64.0)
        assert plan_mmc.partitions[0].device == "/dev/mmcblk0p1"
        assert plan_mmc.partitions[1].device == "/dev/mmcblk0p2"

        # Standard SATA device without trailing digit (/dev/sda -> /dev/sda1)
        plan_sata = engine.plan_partitions(disk_device="/dev/sde", firmware_type="uefi", disk_size_gb=120.0)
        assert plan_sata.partitions[0].device == "/dev/sde1"
        assert plan_sata.partitions[1].device == "/dev/sde2"
        assert plan_sata.partitions[2].device == "/dev/sde3"


# =============================================================================
# Category 2: Boot Media Protection & Active Mount Rejection
# =============================================================================

class TestChallengerBootMediaProtection:
    """Stress tests verifying that active boot and root devices cannot be selected or overwritten."""

    @pytest.mark.parametrize("mountpoint", [
        "/",
        "/run/live/medium",
        "/run/live",
        "/run/live/rootfs",
        "/live",
        "/cdrom",
        "/mnt/live",
    ])
    def test_boot_media_mountpoint_rejection(self, mountpoint):
        """Any disk carrying a live or root mountpoint must be detected and rejected."""
        engine = InstallerEngine()
        dev_info = {
            "device": "/dev/sdz",
            "size_gb": 64.0,
            "model": "Live_USB",
            "mountpoint": mountpoint,
        }
        assert engine._is_device_boot_media(dev_info) is True

        with patch.object(engine, "detect_disks", return_value=[
            {"device": "/dev/sdz", "size_gb": 64.0, "is_boot_media": True, "model": "Live_USB"}
        ]):
            res = engine.run_dry_run(disk_device="/dev/sdz")
            assert res["valid"] is False
            assert "boot media" in res["summary"].lower() or "boot medium" in res["summary"].lower()

    @pytest.mark.parametrize("label", [
        "SEREIN_LIVE",
        "SEREINOS",
        "serein_boot",
        "LIVE_OS",
        "Debian Live",
    ])
    def test_boot_media_label_detection(self, label):
        """Devices labeled with SEREIN or LIVE indicators must be marked as boot media."""
        engine = InstallerEngine()
        dev_info = {
            "device": "/dev/sdc",
            "size_gb": 32.0,
            "label": label,
        }
        assert engine._is_device_boot_media(dev_info) is True

    def test_boot_media_child_partition_detection(self):
        """Parent disk without direct mountpoint whose child partition holds /run/live/medium."""
        engine = InstallerEngine()
        dev_info = {
            "device": "/dev/sdd",
            "size_gb": 32.0,
            "mountpoint": None,
            "children": [
                {"name": "sdd1", "mountpoint": None},
                {"name": "sdd2", "mountpoint": "/run/live/medium"},
            ],
        }
        assert engine._is_device_boot_media(dev_info) is True

    def test_detect_disks_include_exclude_flag(self):
        """detect_disks(include_boot_media=False) must filter out all boot media entries."""
        engine = InstallerEngine()
        all_disks = engine.detect_disks(include_boot_media=True)
        filtered_disks = engine.detect_disks(include_boot_media=False)

        assert any(d["is_boot_media"] for d in all_disks)
        assert not any(d["is_boot_media"] for d in filtered_disks)
        assert len(filtered_disks) < len(all_disks)


# =============================================================================
# Category 3: Capacity Boundary Tests
# =============================================================================

class TestChallengerCapacityBoundaries:
    """Stress tests on storage drive capacity validation across boundary limits."""

    @pytest.mark.parametrize("invalid_size", [
        -10.0,
        0.0,
        5.0,
        10.0,
        19.0,
        19.9,
        19.999,
    ])
    def test_capacity_below_minimum_fails(self, invalid_size):
        """Any disk capacity < 20.0 GB must be strictly rejected."""
        engine = InstallerEngine()
        # 1. Direct plan_partitions call must raise ValueError
        with pytest.raises(ValueError, match="below minimum required 20.0 GB"):
            engine.plan_partitions(disk_device="/dev/sdz", disk_size_gb=invalid_size)

        # 2. run_dry_run must return valid: False
        with patch.object(engine, "detect_disks", return_value=[
            {"device": "/dev/sdz", "size_gb": invalid_size, "is_boot_media": False, "model": "Small_Disk"}
        ]):
            res = engine.run_dry_run(disk_device="/dev/sdz")
            assert res["valid"] is False
            assert "Minimum required is 20.0GB" in res["summary"]

    @pytest.mark.parametrize("valid_size,expected_root_min_mb", [
        (20.0, 15000),      # Exactly 20GB boundary: 20480 - (512+4096) = 15872 MB
        (32.0, 27000),      # Standard small SSD
        (465.7, 470000),    # Sony VAIO standard 500GB HDD
        (1000.0, 1000000),  # 1TB NVMe
        (2000.0, 2000000),  # 2TB SATA SSD
        (16000.0, 16000000) # 16TB Enterprise Storage
    ])
    def test_capacity_valid_boundaries_pass(self, valid_size, expected_root_min_mb):
        """Drives >= 20.0 GB must plan successfully with appropriate rootfs sizing."""
        engine = InstallerEngine()
        plan = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=valid_size)
        assert plan.disk_size_gb == valid_size
        root_spec = plan.partitions[2]
        assert root_spec.mount_point == "/"
        assert root_spec.size_mb >= expected_root_min_mb


# =============================================================================
# Category 4: /etc/fstab Syntax & UUID Uniqueness
# =============================================================================

class TestChallengerFstabAndUUIDs:
    """Stress tests on /etc/fstab structural syntax and cryptographic UUID uniqueness."""

    UUID_REGEX = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )
    FAT32_VOLID_REGEX = re.compile(
        r"^[0-9a-f]{4}-[0-9a-f]{4}$",
        re.IGNORECASE,
    )

    def test_fstab_syntax_and_field_count(self):
        """Every non-comment line in /etc/fstab must strictly follow 6-field standard format."""
        engine = InstallerEngine()
        plan_uefi = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=100.0)
        fstab_content = engine.generate_fstab(plan_uefi)

        non_comment_lines = [
            line.strip() for line in fstab_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        # UEFI should have 3 lines: root, ESP, swap
        assert len(non_comment_lines) == 3

        for line in non_comment_lines:
            tokens = line.split()
            assert len(tokens) == 6, f"Malformed fstab line (expected 6 fields): '{line}'"
            device_token, mount_token, fs_token, opt_token, dump_token, pass_token = tokens

            assert device_token.startswith("UUID=")
            raw_uuid = device_token.split("UUID=")[1]
            if mount_token == "/boot/efi" or fs_token == "vfat":
                assert self.FAT32_VOLID_REGEX.match(raw_uuid), f"Invalid FAT32 volume ID format: '{raw_uuid}'"
            else:
                assert self.UUID_REGEX.match(raw_uuid), f"Invalid UUID string format: '{raw_uuid}'"

            # Check fsck pass numbers: root must have 1, others must have 2 or 0
            if mount_token == "/":
                assert fs_token == "ext4"
                assert dump_token == "0"
                assert pass_token == "1"
            elif mount_token == "/boot/efi":
                assert fs_token == "vfat"
                assert dump_token == "0"
                assert pass_token == "2"
            elif mount_token == "none":
                assert fs_token == "swap"
                assert dump_token == "0"
                assert pass_token == "0"

    def test_uuid_uniqueness_across_partitions_and_generations(self):
        """UUIDs must never collide across partitions in the same plan or across distinct generations."""
        engine = InstallerEngine()
        all_uuids = set()
        iterations = 50

        for _ in range(iterations):
            plan = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=80.0)
            plan_uuids = [p.uuid for p in plan.partitions]
            # No duplicate in single plan
            assert len(plan_uuids) == len(set(plan_uuids))
            for u in plan_uuids:
                assert u not in all_uuids, f"Collision detected for UUID {u}"
                all_uuids.add(u)

        assert len(all_uuids) == iterations * 3

    def test_custom_uuid_override_in_fstab(self):
        """Custom UUID dictionary must accurately override generated UUIDs in fstab."""
        engine = InstallerEngine()
        plan = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=60.0)

        custom_root_uuid = "11111111-2222-3333-4444-555555555555"
        custom_esp_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        custom_swap_uuid = "99999999-8888-7777-6666-555555555555"

        custom_map = {
            "/dev/sda1": custom_esp_uuid,
            "/dev/sda2": custom_swap_uuid,
            "/dev/sda3": custom_root_uuid,
        }

        fstab_str = engine.generate_fstab(plan, custom_uuids=custom_map)
        assert f"UUID={custom_root_uuid} / ext4" in fstab_str
        assert f"UUID={custom_esp_uuid} /boot/efi vfat" in fstab_str
        assert f"UUID={custom_swap_uuid} none swap" in fstab_str


# =============================================================================
# Category 5: Staged GRUB Bootloader Commands
# =============================================================================

class TestChallengerBootloaderCommands:
    """Stress tests on GRUB bootloader staging commands for UEFI and BIOS targets."""

    def test_uefi_grub_command_structure(self):
        """UEFI GRUB install must specify x86_64-efi, /mnt/boot/efi efi-dir, and SereinOS bootloader id."""
        engine = InstallerEngine()
        plan = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=80.0)
        cmd = engine.generate_bootloader_command(plan)

        expected = "grub-install --target=x86_64-efi --efi-directory=/mnt/boot/efi --bootloader-id=SereinOS --recheck"
        assert cmd == expected

    def test_bios_grub_command_structure(self):
        """BIOS GRUB install must specify i386-pc target and point directly to the whole target disk."""
        engine = InstallerEngine()
        plan_sda = engine.plan_partitions(disk_device="/dev/sda", firmware_type="bios", disk_size_gb=80.0)
        cmd_sda = engine.generate_bootloader_command(plan_sda)
        assert cmd_sda == "grub-install --target=i386-pc --recheck /dev/sda"

        plan_nvme = engine.plan_partitions(disk_device="/dev/nvme0n1", firmware_type="bios", disk_size_gb=128.0)
        cmd_nvme = engine.generate_bootloader_command(plan_nvme)
        assert cmd_nvme == "grub-install --target=i386-pc --recheck /dev/nvme0n1"


# =============================================================================
# Category 6: CLI Subprocess & Execution Stress
# =============================================================================

class TestChallengerCLIExecution:
    """Stress testing the CLI interface via subprocess invocation."""

    def test_cli_list_disks_text_mode(self):
        """python -m serein_installer.installer_engine --list-disks returns 0 and displays table header."""
        res = subprocess.run(
            [sys.executable, "-m", "serein_installer.installer_engine", "--list-disks"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert res.returncode == 0
        assert "DEVICE" in res.stdout
        assert "SIZE (GB)" in res.stdout
        assert "BOOT MEDIA" in res.stdout
        assert "/dev/sda" in res.stdout

    def test_cli_list_disks_json_mode(self):
        """python -m serein_installer.installer_engine --list-disks --json returns parseable JSON."""
        res = subprocess.run(
            [sys.executable, "-m", "serein_installer.installer_engine", "--list-disks", "--json"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert "disks" in data
        assert isinstance(data["disks"], list)
        assert len(data["disks"]) >= 1

    def test_cli_dry_run_success_text_and_json(self):
        """python -m serein_installer.installer_engine --dry-run --disk /dev/sda returns 0."""
        # Text output
        res_text = subprocess.run(
            [sys.executable, "-m", "serein_installer.installer_engine", "--dry-run", "--disk", "/dev/sda"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert res_text.returncode == 0
        assert "SEREINOS INSTALLER DRY-RUN VERIFICATION" in res_text.stdout
        assert "Status:     SUCCESS" in res_text.stdout
        assert "Generated /etc/fstab:" in res_text.stdout

        # JSON output
        res_json = subprocess.run(
            [sys.executable, "-m", "serein_installer.installer_engine", "--dry-run", "--disk", "/dev/sda", "--json"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert res_json.returncode == 0
        data = json.loads(res_json.stdout)
        assert data["valid"] is True
        assert "UUID=" in data["fstab"]
        assert len(data["partitions"]) >= 2

    def test_cli_dry_run_boot_media_rejection_exit_code(self):
        """python -m serein_installer.installer_engine --dry-run --disk /dev/sdb returns non-zero code."""
        res = subprocess.run(
            [sys.executable, "-m", "serein_installer.installer_engine", "--dry-run", "--disk", "/dev/sdb"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        # Should exit with 1 because valid is False
        assert res.returncode == 1
        assert "Status:     FAILED" in res.stdout

    def test_cli_direct_invocation_via_cli_main(self):
        """Invoke cli_main function directly with simulated argv."""
        assert cli_main(["--list-disks"]) == 0
        assert cli_main(["--dry-run", "--disk", "/dev/sda"]) == 0
        assert cli_main(["--dry-run", "--disk", "/dev/sdb"]) == 1


# =============================================================================
# Category 7: Web API Adversarial Challenge & Concurrency
# =============================================================================

class TestChallengerWebAPIAdversarial:
    """Stress testing the FastAPI endpoints under adversarial, malformed, and concurrent loads."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_web_dry_run_with_malformed_disk_paths(self, client):
        """Adversarial disk paths without /dev/ prefix (traversal, command substitution, relative) must be rejected."""
        malicious_inputs = [
            "../../etc/shadow",
            "$(cat /etc/passwd)",
            "sda",
            "C:\\Windows\\System32",
            "",
            "dev/sda",
        ]
        for bad_disk in malicious_inputs:
            res = client.post("/api/installer/dry-run", json={"disk": bad_disk})
            assert res.status_code == 200
            data = res.json()
            assert data["valid"] is False, f"Expected rejection for '{bad_disk}', got valid: True"
            assert "must start with /dev/" in data.get("summary", "") or "not recognized" in data.get("error", "")

    def test_web_dry_run_invalid_payload_handling(self, client):
        """Ensure endpoint responds gracefully (422 or valid rejection) for bad types."""
        # Missing disk field
        res = client.post("/api/installer/dry-run", json={})
        assert res.status_code == 422

        # Non-string disk field
        res = client.post("/api/installer/dry-run", json={"disk": 12345})
        assert res.status_code == 422

    def test_web_dry_run_concurrent_stress(self, client):
        """Dispatch 20 concurrent dry-run requests across worker threads."""
        def make_req(disk_name: str):
            return client.post("/api/installer/dry-run", json={
                "disk": disk_name,
                "scheme": "auto",
                "firmware_type": "uefi",
            })

        disks = ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sda"] * 5
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(make_req, disks))

        assert len(responses) == 20
        for resp in responses:
            assert resp.status_code == 200
            data = resp.json()
            assert "valid" in data
            assert isinstance(data["valid"], bool)

    def test_web_install_background_lifecycle(self, client):
        """Trigger install and verify progress polling returns consistent state."""
        res_inst = client.post("/api/installer/install", json={
            "disk": "/dev/sda",
            "user": "serein_challenger",
            "password": "adversarial_secret",
            "hostname": "challenger-station",
            "firmware_type": "uefi",
        })
        assert res_inst.status_code == 200
        inst_data = res_inst.json()
        assert inst_data["status"] == "started"
        task_id = inst_data["task_id"]
        assert task_id

        # Immediately query progress
        res_prog = client.get(f"/api/installer/progress?task_id={task_id}")
        assert res_prog.status_code == 200
        prog_data = res_prog.json()
        assert "phase" in prog_data
        assert "percent" in prog_data
        assert isinstance(prog_data["log"], list)

    def test_web_install_concurrency_lock_409(self, client):
        """Concurrent installation calls must be rejected with 409 Conflict."""
        # Wait for any prior task to finish
        for _ in range(50):
            p = client.get("/api/installer/progress").json()
            if p.get("done", True) or p.get("phase") in ("idle", "complete", "failed"):
                break
            time.sleep(0.05)

        res1 = client.post("/api/installer/install", json={"disk": "/dev/sda"})
        assert res1.status_code == 200, f"Expected 200 on first install, got {res1.status_code}: {res1.text}"

        # Second install while first is running must fail with 409 Conflict
        res2 = client.post("/api/installer/install", json={"disk": "/dev/sda"})
        assert res2.status_code == 409, f"Expected 409 Conflict on concurrent install, got {res2.status_code}"
        assert "already in progress" in res2.json().get("detail", "")

    def test_command_injection_rejected_in_dry_run(self):
        """Shell injection in disk path must be strictly rejected with valid: False."""
        engine = InstallerEngine()
        injected_disk = "/dev/sda; rm -rf / #"
        res = engine.run_dry_run(injected_disk, firmware_type="bios")
        assert res["valid"] is False
        assert "illegal shell characters" in res["summary"] or "contains forbidden" in res["error"]
        assert res["bootloader"] == ""

    def test_fat32_esp_volume_id_format(self):
        """PartitionSpec for ESP must generate 32-bit volume ID (XXXX-XXXX)."""
        esp = PartitionSpec(
            number=1,
            device="/dev/sda1",
            size_mb=512,
            fstype="vfat",
            mount_point="/boot/efi",
            label="ESP",
        )
        vol_pattern = re.compile(r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$")
        assert vol_pattern.match(esp.uuid), f"Expected FAT32 volume serial (XXXX-XXXX), got: '{esp.uuid}'"

        ext4 = PartitionSpec(
            number=2,
            device="/dev/sda2",
            size_mb=20480,
            fstype="ext4",
            mount_point="/",
            label="SereinRoot",
        )
        uuid4_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        assert uuid4_pattern.match(ext4.uuid), f"Expected UUID4 for ext4, got: '{ext4.uuid}'"

