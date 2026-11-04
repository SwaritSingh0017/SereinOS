"""
Unit tests for SereinOS Standalone Graphical OS Installer & Plymouth Boot Splash.
Verifies InstallerEngine disk enumeration, UEFI/BIOS partitioning, fstab generation,
GRUB bootloader staging, programmatic --dry-run simulation, FastAPI web endpoints,
and Plymouth theme configuration.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch, MagicMock

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_DIR = REPO_ROOT / "serein-installer"

for p in (str(REPO_ROOT), str(INSTALLER_DIR)):
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
    cli_main,
)


@pytest.fixture
def test_client():
    return TestClient(app)


# =============================================================================
# 1. Disk Detection & Boot Media Filtering
# =============================================================================

class TestInstallerDisks:
    def test_detect_disks_returns_valid_structure(self):
        engine = InstallerEngine()
        disks = engine.detect_disks(include_boot_media=True)
        assert len(disks) >= 1
        d = disks[0]
        assert "device" in d
        assert "size_gb" in d
        assert "model" in d
        assert "is_boot_media" in d
        assert isinstance(d["size_gb"], (int, float))
        assert isinstance(d["is_boot_media"], bool)

    def test_boot_media_filtering(self):
        engine = InstallerEngine()
        all_disks = engine.detect_disks(include_boot_media=True)
        installable_disks = engine.detect_disks(include_boot_media=False)
        assert all(not d["is_boot_media"] for d in installable_disks)
        assert len(installable_disks) <= len(all_disks)

    def test_lsblk_json_parsing_logic(self):
        engine = InstallerEngine()
        mock_lsblk_output = json.dumps({
            "blockdevices": [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "size": 500107862016,
                    "type": "disk",
                    "model": "TOSHIBA_MK5065GSX",
                    "rm": False,
                    "ro": False,
                    "mountpoint": None,
                    "children": [{"name": "sda1", "mountpoint": "/mnt/data"}]
                },
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "size": 16000000000,
                    "type": "disk",
                    "model": "SanDisk_Ultra",
                    "rm": True,
                    "ro": True,
                    "mountpoint": None,
                    "children": [{"name": "sdb1", "mountpoint": "/run/live/medium"}]
                }
            ]
        })

        with patch("shutil.which", return_value="/bin/lsblk"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=mock_lsblk_output, check_returncode=lambda: None)
            disks = engine._query_lsblk()
            assert len(disks) == 2
            assert disks[0]["device"] == "/dev/sda"
            assert disks[0]["is_boot_media"] is False
            assert disks[1]["device"] == "/dev/sdb"
            assert disks[1]["is_boot_media"] is True


# =============================================================================
# 2. Dual-Mode Partition Planning (UEFI GPT & Legacy BIOS MBR)
# =============================================================================

class TestInstallerPartitionPlanning:
    def test_plan_partitions_uefi_layout(self):
        engine = InstallerEngine()
        plan = engine.plan_partitions(
            disk_device="/dev/sda",
            firmware_type="uefi",
            disk_size_gb=120.0,
        )
        assert plan.disk == "/dev/sda"
        assert plan.firmware_type == "uefi"
        assert plan.table_type == "gpt"
        assert len(plan.partitions) == 3

        # P1: ESP (512MB FAT32)
        p1 = plan.partitions[0]
        assert p1.number == 1
        assert p1.size_mb == 512
        assert p1.fstype == "vfat"
        assert p1.mount_point == "/boot/efi"
        assert "boot" in p1.flags
        assert "esp" in p1.flags

        # P2: Swap (4096MB)
        p2 = plan.partitions[1]
        assert p2.number == 2
        assert p2.size_mb == 4096
        assert p2.fstype == "swap"

        # P3: Root (ext4 > 20GB)
        p3 = plan.partitions[2]
        assert p3.number == 3
        assert p3.fstype == "ext4"
        assert p3.mount_point == "/"
        assert p3.size_mb > 20480

    def test_plan_partitions_bios_layout(self):
        engine = InstallerEngine()
        plan = engine.plan_partitions(
            disk_device="/dev/sda",
            firmware_type="bios",
            disk_size_gb=80.0,
        )
        assert plan.disk == "/dev/sda"
        assert plan.firmware_type == "bios"
        assert plan.table_type == "msdos"
        assert len(plan.partitions) == 2

        # P1: Root (ext4 > 20GB)
        p1 = plan.partitions[0]
        assert p1.number == 1
        assert p1.fstype == "ext4"
        assert p1.mount_point == "/"
        assert "boot" in p1.flags
        assert p1.size_mb > 20480

        # P2: Swap (4096MB)
        p2 = plan.partitions[1]
        assert p2.number == 2
        assert p2.size_mb == 4096
        assert p2.fstype == "swap"

    def test_plan_partitions_disk_size_under_20gb_raises_error(self):
        engine = InstallerEngine()
        with pytest.raises(ValueError, match="below minimum required 20.0 GB"):
            engine.plan_partitions(
                disk_device="/dev/sda",
                firmware_type="uefi",
                disk_size_gb=16.0,
            )

    def test_auto_firmware_detection(self):
        engine = InstallerEngine()
        with patch("os.path.exists", return_value=True):
            assert engine.detect_firmware() == "uefi"
        with patch("os.path.exists", return_value=False):
            assert engine.detect_firmware() == "bios"


# =============================================================================
# 3. /etc/fstab Generation & Dual-Mode Bootloader Staging
# =============================================================================

class TestInstallerFstabAndBootloader:
    def test_fstab_generation_uefi(self):
        engine = InstallerEngine()
        plan = engine.plan_partitions(
            disk_device="/dev/sda",
            firmware_type="uefi",
            disk_size_gb=100.0,
        )
        fstab = engine.generate_fstab(plan)
        assert "UUID=" in fstab
        assert "/boot/efi" in fstab
        assert "vfat" in fstab
        assert "/ " in fstab or "/\t" in fstab or "/ ext4" in fstab
        assert "ext4" in fstab
        assert "swap" in fstab

        import re
        for line in fstab.splitlines():
            if "/boot/efi" in line:
                vol_id = line.split()[0].replace("UUID=", "")
                assert re.match(r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$", vol_id), f"Invalid ESP volume ID: {vol_id}"

    def test_fstab_generation_bios(self):
        engine = InstallerEngine()
        plan = engine.plan_partitions(
            disk_device="/dev/sda",
            firmware_type="bios",
            disk_size_gb=100.0,
        )
        fstab = engine.generate_fstab(plan)
        assert "UUID=" in fstab
        assert "ext4" in fstab
        assert "swap" in fstab
        assert "/boot/efi" not in fstab

    def test_bootloader_command_uefi(self):
        engine = InstallerEngine()
        plan = engine.plan_partitions("/dev/sda", firmware_type="uefi", disk_size_gb=100.0)
        cmd = engine.generate_bootloader_command(plan)
        assert "grub-install" in cmd
        assert "--target=x86_64-efi" in cmd
        assert "--efi-directory=/mnt/boot/efi" in cmd
        assert "--bootloader-id=SereinOS" in cmd

    def test_bootloader_command_bios(self):
        engine = InstallerEngine()
        plan = engine.plan_partitions("/dev/sda", firmware_type="bios", disk_size_gb=100.0)
        cmd = engine.generate_bootloader_command(plan)
        assert "grub-install" in cmd
        assert "--target=i386-pc" in cmd
        assert "/dev/sda" in cmd


# =============================================================================
# 4. Programmatic Dry-Run Mode (--dry-run)
# =============================================================================

class TestInstallerDryRun:
    def test_dry_run_success_response_structure(self):
        engine = InstallerEngine()
        result = engine.run_dry_run(disk_device="/dev/sda", firmware_type="uefi")
        assert result["valid"] is True
        assert "Simulated install to /dev/sda" in result["summary"]
        assert len(result["steps"]) == 5
        assert "UUID=" in result["fstab"]
        assert "grub-install --target=x86_64-efi" in result["bootloader"]
        assert len(result["partitions"]) == 3

    def test_dry_run_refuses_boot_media(self):
        engine = InstallerEngine()
        # /dev/sdb is marked as boot media in mock inventory
        result = engine.run_dry_run(disk_device="/dev/sdb")
        assert result["valid"] is False
        assert "boot media" in result["summary"].lower() or "boot medium" in result["summary"].lower()

    def test_dry_run_rejects_disk_below_20gb(self):
        engine = InstallerEngine()
        with patch.object(engine, "detect_disks", return_value=[
            {"device": "/dev/sdc", "size_gb": 12.0, "is_boot_media": False, "model": "Small_Disk"}
        ]):
            result = engine.run_dry_run(disk_device="/dev/sdc")
            assert result["valid"] is False
            assert "Minimum required is 20.0GB" in result["summary"]

    def test_dry_run_executes_zero_writes(self):
        engine = InstallerEngine()
        with patch("subprocess.run") as mock_sub:
            result = engine.run_dry_run(disk_device="/dev/sda")
            assert result["valid"] is True
            # Confirm no destructive write tools (mkfs, dd, parted) were executed
            for call in mock_sub.call_args_list:
                args = call[0][0]
                assert not any(tool in args for tool in ["mkfs", "dd", "parted -s", "format"])


# =============================================================================
# 5. Web API Endpoints (/api/installer/*)
# =============================================================================

class TestInstallerWebAPI:
    def test_get_disks_endpoint(self, test_client):
        res = test_client.get("/api/installer/disks")
        assert res.status_code == 200
        data = res.json()
        assert "disks" in data
        assert isinstance(data["disks"], list)
        assert len(data["disks"]) >= 1
        d = data["disks"][0]
        assert "device" in d
        assert "size_gb" in d
        assert "model" in d
        assert "is_boot_media" in d

    def test_post_dry_run_endpoint_success(self, test_client):
        res = test_client.post("/api/installer/dry-run", json={
            "disk": "/dev/sda",
            "scheme": "auto",
            "firmware_type": "uefi"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is True
        assert "summary" in data
        assert "steps" in data
        assert "fstab" in data
        assert "bootloader" in data
        assert "grub-install --target=x86_64-efi" in data["bootloader"]

    def test_post_dry_run_endpoint_boot_media_rejected(self, test_client):
        res = test_client.post("/api/installer/dry-run", json={
            "disk": "/dev/sdb",
            "scheme": "auto",
            "firmware_type": "uefi"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is False

    def test_post_install_and_progress_lifecycle(self, test_client):
        res = test_client.post("/api/installer/install", json={
            "disk": "/dev/sda",
            "user": "testuser",
            "password": "testpassword",
            "hostname": "test-box",
            "firmware_type": "uefi"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "started"
        assert "task_id" in data
        task_id = data["task_id"]

        # Poll progress
        res_prog = test_client.get(f"/api/installer/progress?task_id={task_id}")
        assert res_prog.status_code == 200
        prog = res_prog.json()
        assert "phase" in prog
        assert "percent" in prog
        assert "log" in prog
        assert "done" in prog
        assert isinstance(prog["percent"], int)
        assert 0 <= prog["percent"] <= 100


# =============================================================================
# 6. Plymouth Glowing Splash Configuration & Assets
# =============================================================================

class TestPlymouthGlowTheme:
    def test_plymouth_theme_file_properties(self):
        theme_file = REPO_ROOT / "serein-installer" / "plymouth" / "serein-glow" / "serein-glow.plymouth"
        assert theme_file.exists()
        content = theme_file.read_text(encoding="utf-8")
        assert "ModuleName=script" in content
        assert "serein-glow" in content.lower()
        assert "serein-glow.script" in content

    def test_plymouth_script_contains_animation_and_callbacks(self):
        script_file = REPO_ROOT / "serein-installer" / "plymouth" / "serein-glow" / "serein-glow.script"
        assert script_file.exists()
        content = script_file.read_text(encoding="utf-8")
        assert "Math.Sin" in content
        assert "Plymouth.SetRefreshFunction" in content
        assert "Plymouth.SetBootProgressFunction" in content
        assert "Plymouth.SetMessageFunction" in content
        assert "Plymouth.SetQuitFunction" in content

    def test_plymouth_logo_and_glow_assets_exist(self):
        glow_dir = REPO_ROOT / "serein-installer" / "plymouth" / "serein-glow"
        assert (glow_dir / "logo.svg").exists()
        assert (glow_dir / "logo.png").exists()
        assert (glow_dir / "glow.png").exists()
        assert (glow_dir / "logo.png").stat().st_size > 100
        assert (glow_dir / "glow.png").stat().st_size > 100


# =============================================================================
# 7. CLI Entrypoint
# =============================================================================

class TestInstallerCLI:
    def test_cli_list_disks(self, capsys):
        code = cli_main(["--list-disks", "--json"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "disks" in data
        assert len(data["disks"]) >= 1

    def test_cli_dry_run(self, capsys):
        code = cli_main(["--disk", "/dev/sda", "--dry-run", "--json", "--firmware", "uefi"])
        assert code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["valid"] is True
        assert len(data["steps"]) == 5
