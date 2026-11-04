"""
Tier 5 Adversarial Stress & Empirical Verification Suite: Hardware Enablement & OS Installer
Authored by: challenger_tier5_hw_installer
Target Scope:
1. Hardware Enablement (serein_core/hardware.py): Backlight sysfs, battery monitor,
   audio amixer controller, network monitor, Sony VAIO VPCYB35AN inventory, hardware tiering.
2. Serein Installer (serein-installer/installer_engine.py): Input injection, boundary sizing,
   dual-mode partition geometry (UEFI GPT / BIOS MBR), fstab UUID specification, GRUB staging,
   installer lifecycle concurrency, worker failure recovery, and CLI.
3. Web REST Endpoints (serein_core/web_app.py): Hardware and installer routes stress testing.
4. ISO Build Scripts & Plymouth Splash (build_iso.sh, install_artifacts_container.sh, serein-glow).
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# Ensure repo root and sub-packages are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_SRC = REPO_ROOT / "serein-core" / "src"
INSTALLER_DIR = REPO_ROOT / "serein-installer"

for p in (str(REPO_ROOT), str(CORE_SRC), str(INSTALLER_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from serein_core.hardware import (
    AudioController,
    BacklightController,
    BatteryMonitor,
    BatteryStatus,
    BrightnessInfo,
    HardwareDetector,
    HardwareProfile,
    NetworkHardwareMonitor,
    SonyVaioHardwareManager,
    VolumeInfo,
    get_battery_status,
    get_brightness,
    get_hardware_inventory,
    get_hardware_profile,
    get_network_hardware_status,
    get_volume,
    set_brightness,
    set_volume,
)
from installer_engine import (
    DEFAULT_EFI_SIZE_MB,
    DEFAULT_SWAP_SIZE_MB,
    DISK_REGEX,
    HOSTNAME_REGEX,
    MIN_DISK_SIZE_GB,
    SHELL_METACHRACTERS,
    USERNAME_REGEX,
    DiskInfo,
    DryRunResult,
    InstallerEngine,
    InstallProgress,
    PartitionPlan,
    PartitionSpec,
    cli_main,
)
from serein_core.web_app import app


@pytest.fixture
def test_client():
    return TestClient(app)


# ============================================================================
# Category 1: BacklightController Adversarial Stress & Boundary Limits
# ============================================================================

class TestTier5BacklightAdversarialStress:
    """Stress testing sysfs screen backlight controller under corrupt filesystems and extreme inputs."""

    def test_backlight_nonexistent_and_permission_denied_sysfs(self, tmp_path):
        """Verify behavior when sysfs path does not exist or raises permission denied."""
        nonexistent_dir = tmp_path / "does_not_exist_sysfs_bl"
        ctrl = BacklightController(sysfs_dir=nonexistent_dir)
        info = ctrl.get_brightness()
        assert 0 <= info["brightness_percent"] <= 100
        assert info["device"] == "radeon_bl0"
        assert info["max_brightness"] == 255

        # Simulated device with unwritable brightness file
        dev_dir = tmp_path / "radeon_bl0"
        dev_dir.mkdir(parents=True, exist_ok=True)
        (dev_dir / "max_brightness").write_text("255\n")
        bright_file = dev_dir / "brightness"
        bright_file.write_text("128\n")

        ctrl_dev = BacklightController(sysfs_dir=tmp_path)
        with patch.object(Path, "write_text", side_effect=PermissionError("Permission denied: /sys/class/backlight")):
            res = ctrl_dev.set_brightness(50)
            assert res["brightness_percent"] == 50
            assert res["success"] is False
            assert "Permission denied" in res["error"]
            # Internal state still records the requested percentage
            assert ctrl_dev._fallback_percent == 50

    def test_backlight_clamping_boundary_and_types(self, tmp_path):
        """Verify extreme values, type conversions, and invalid type safety."""
        ctrl = BacklightController(sysfs_dir=tmp_path)

        # Extreme negatives
        res_neg = ctrl.set_brightness(-9999)
        assert res_neg["brightness_percent"] == 0

        # Extreme positive
        res_pos = ctrl.set_brightness(9999)
        assert res_pos["brightness_percent"] == 100

        # Exact boundaries
        assert ctrl.set_brightness(0)["brightness_percent"] == 0
        assert ctrl.set_brightness(100)["brightness_percent"] == 100

        # Numeric float string conversion
        assert ctrl.set_brightness("45")["brightness_percent"] == 45
        assert ctrl.set_brightness(67.8)["brightness_percent"] == 67

        with pytest.raises(ValueError):
            ctrl.set_brightness("not_a_number")

    def test_backlight_corrupted_sysfs_files(self, tmp_path):
        """Verify division-by-zero protection, non-numeric values, and binary garbage."""
        dev = tmp_path / "radeon_bl0"
        dev.mkdir()
        ctrl = BacklightController(sysfs_dir=tmp_path)

        # Division by zero: max_brightness == 0
        (dev / "max_brightness").write_text("0\n")
        (dev / "brightness").write_text("50\n")
        info = ctrl.get_brightness()
        assert 0 <= info["brightness_percent"] <= 100

        # Negative max_brightness
        (dev / "max_brightness").write_text("-255\n")
        info_neg = ctrl.get_brightness()
        assert 0 <= info_neg["brightness_percent"] <= 100

        # Corrupted non-numeric strings & binary garbage
        (dev / "max_brightness").write_text("INVALID_MAX\n")
        (dev / "brightness").write_text("\x00\xffCORRUPT\n")
        info_corrupt = ctrl.get_brightness()
        assert 0 <= info_corrupt["brightness_percent"] <= 100

    def test_backlight_device_fallback_and_discovery_priority(self, tmp_path):
        """Preferred device order for Sony VAIO VPCYB35AN (Radeon HD 6320)."""
        # Create multiple devices
        dev_intel = tmp_path / "intel_backlight"
        dev_acpi = tmp_path / "acpi_video0"
        dev_radeon = tmp_path / "radeon_bl0"

        for d in (dev_intel, dev_acpi, dev_radeon):
            d.mkdir()
            (d / "brightness").write_text("100\n")
            (d / "max_brightness").write_text("200\n")

        ctrl = BacklightController(sysfs_dir=tmp_path)
        found = ctrl._find_backlight_device()
        # radeon_bl0 must have top priority for VAIO
        assert found == dev_radeon

        # Non-preferred fallback when preferred devices don't exist
        for d in (dev_intel, dev_acpi, dev_radeon):
            (d / "brightness").unlink()
            (d / "max_brightness").unlink()
            d.rmdir()

        custom_dev = tmp_path / "custom_panel_bl"
        custom_dev.mkdir()
        (custom_dev / "brightness").write_text("50\n")
        (custom_dev / "max_brightness").write_text("100\n")

        ctrl_custom = BacklightController(sysfs_dir=tmp_path)
        found_custom = ctrl_custom._find_backlight_device()
        assert found_custom == custom_dev

    def test_backlight_concurrent_read_write_stress(self, tmp_path):
        """Stress-test concurrent calls to get_brightness and set_brightness across 20 threads."""
        ctrl = BacklightController(sysfs_dir=tmp_path)

        def worker(idx: int):
            ctrl.set_brightness((idx * 5) % 101)
            info = ctrl.get_brightness()
            assert 0 <= info["brightness_percent"] <= 100

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker, i) for i in range(100)]
            for f in concurrent.futures.as_completed(futures):
                f.result()


# ============================================================================
# Category 2: BatteryMonitor Adversarial Stress & Telemetry Validation
# ============================================================================

class TestTier5BatteryAdversarialStress:
    """Stress testing ACPI power supply monitoring under simulated hardware states."""

    def test_battery_corrupted_and_out_of_range_capacity(self, tmp_path):
        """Verify negative, excessive, or corrupt capacity values are clamped to [0, 100]."""
        bat_dir = tmp_path / "BAT1"
        bat_dir.mkdir()
        ctrl = BatteryMonitor(sysfs_dir=tmp_path)

        # Negative capacity
        (bat_dir / "capacity").write_text("-42\n")
        (bat_dir / "status").write_text("Discharging\n")
        st = ctrl.get_status()
        assert st["capacity_percent"] == 0

        # Excessive capacity
        (bat_dir / "capacity").write_text("199\n")
        st2 = ctrl.get_status()
        assert st2["capacity_percent"] == 100

        # Non-numeric capacity -> falls back safely
        (bat_dir / "capacity").write_text("CORRUPT_HEX\n")
        st3 = ctrl.get_status()
        assert 0 <= st3["capacity_percent"] <= 100

    def test_battery_status_file_edge_cases(self, tmp_path):
        """Missing or non-standard status file gracefully infers charging status."""
        bat_dir = tmp_path / "BAT0"
        bat_dir.mkdir()
        (bat_dir / "capacity").write_text("85\n")
        ctrl = BatteryMonitor(sysfs_dir=tmp_path)

        # Missing status file with AC online
        ac_dir = tmp_path / "ACAD"
        ac_dir.mkdir()
        (ac_dir / "online").write_text("1\n")

        st_online = ctrl.get_status()
        assert st_online["status"] == "Charging"
        assert st_online["ac_online"] is True

        # Missing status file with AC offline
        (ac_dir / "online").write_text("0\n")
        st_offline = ctrl.get_status()
        assert st_offline["status"] == "Discharging"
        assert st_offline["ac_online"] is False

    def test_battery_ac_adapter_naming_conventions(self, tmp_path):
        """Tests both AC and ADP power adapter naming variants."""
        bat = tmp_path / "BAT1"
        bat.mkdir()
        (bat / "capacity").write_text("90\n")
        (bat / "status").write_text("Full\n")

        # ADP variant (common on some Sony VAIO BIOS builds)
        adp = tmp_path / "ADP1"
        adp.mkdir()
        (adp / "online").write_text("1\n")

        ctrl = BatteryMonitor(sysfs_dir=tmp_path)
        status = ctrl.get_status()
        assert status["ac_online"] is True
        assert status["device"] == "BAT1"

    def test_battery_psutil_fallback_resilience(self, tmp_path):
        """Fallback to psutil when sysfs is empty or absent."""
        empty_dir = tmp_path / "empty_ps"
        empty_dir.mkdir()
        ctrl = BatteryMonitor(sysfs_dir=empty_dir)

        # Mock psutil.sensors_battery returning None
        with patch("psutil.sensors_battery", return_value=None):
            status = ctrl.get_status()
            assert status["present"] is True
            assert status["capacity_percent"] == 100
            assert status["status"] == "Full"
            assert status["ac_online"] is True

        # Mock psutil.sensors_battery raising exception
        with patch("psutil.sensors_battery", side_effect=RuntimeError("psutil hardware error")):
            status_err = ctrl.get_status()
            assert status_err["present"] is True
            assert status_err["capacity_percent"] == 100

    def test_battery_dataclass_contract(self):
        """Verify BatteryStatus dataclass matches system schema."""
        b = BatteryStatus(present=True, capacity_percent=60, status="Discharging", ac_online=False, device="BAT1")
        d = b.to_dict()
        assert d["present"] is True
        assert d["capacity_percent"] == 60
        assert d["status"] == "Discharging"
        assert d["ac_online"] is False
        assert d["device"] == "BAT1"


# ============================================================================
# Category 3: AudioController Adversarial Stress & ALSA Recovery
# ============================================================================

class TestTier5AudioAdversarialStress:
    """Stress testing ALSA amixer audio subsystem and mute toggling."""

    def test_audio_volume_clamping_and_mute_state(self):
        """Verify volume clamping [0, 100] and mute preservation."""
        ctrl = AudioController(amixer_bin=None)

        # Clamping
        assert ctrl.set_volume(-50)["volume_percent"] == 0
        assert ctrl.set_volume(150)["volume_percent"] == 100
        assert ctrl.set_volume(42)["volume_percent"] == 42

        # Mute preservation
        ctrl.set_mute(True)
        vol_info = ctrl.get_volume()
        assert vol_info["muted"] is True
        assert vol_info["volume_percent"] == 42

        ctrl.set_mute(False)
        vol_info2 = ctrl.get_volume()
        assert vol_info2["muted"] is False
        assert vol_info2["volume_percent"] == 42

    def test_audio_amixer_failure_and_timeout_recovery(self):
        """Verify graceful fallback when amixer subprocess times out or crashes."""
        with patch("shutil.which", return_value="/usr/bin/amixer"):
            ctrl = AudioController(amixer_bin="/usr/bin/amixer")

            # Mock subprocess timeout
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="amixer", timeout=2)):
                res = ctrl.get_volume()
                assert 0 <= res["volume_percent"] <= 100

                res_set = ctrl.set_volume(60)
                assert res_set["volume_percent"] == 60
                assert res_set["success"] is True

    def test_audio_amixer_regex_output_parsing(self):
        """Verify regex parsing of varied amixer output strings."""
        ctrl = AudioController(amixer_bin="/usr/bin/amixer")

        # Standard active volume
        mock_stdout1 = "Simple mixer control 'Master',0\n  Capabilities: pvolume pswitch\n  Playback channels: Front Left - Front Right\n  Limits: Playback 0 - 65536\n  Front Left: Playback 32768 [50%] [on]\n  Front Right: Playback 32768 [50%] [on]\n"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=mock_stdout1)):
            info = ctrl.get_volume()
            assert info["volume_percent"] == 50
            assert info["muted"] is False

        # Muted volume
        mock_stdout2 = "Simple mixer control 'Master',0\n  Mono: Playback 52428 [80%] [off]\n"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=mock_stdout2)):
            info2 = ctrl.get_volume()
            assert info2["volume_percent"] == 80
            assert info2["muted"] is True

    def test_audio_secondary_channels_adjustment(self):
        """Verify secondary channels (Speaker, Headphone, PCM) are also set on ALC269."""
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="")

        ctrl = AudioController(amixer_bin="/usr/bin/amixer")
        with patch("subprocess.run", side_effect=mock_run):
            ctrl.set_volume(85, muted=False)

        # Check that Master, Speaker, Headphone, PCM were all called
        controls_called = [c[4] for c in calls if len(c) > 4 and c[0] == "/usr/bin/amixer" and c[3] == "set"]
        assert "Master" in controls_called
        assert "Speaker" in controls_called
        assert "Headphone" in controls_called
        assert "PCM" in controls_called

    def test_audio_concurrent_volume_adjustments(self):
        """Stress-test concurrent volume adjustments across threads."""
        ctrl = AudioController(amixer_bin=None)

        def worker(idx: int):
            ctrl.set_volume((idx * 7) % 101, muted=(idx % 2 == 0))
            vol = ctrl.get_volume()
            assert 0 <= vol["volume_percent"] <= 100

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker, i) for i in range(80)]
            for f in concurrent.futures.as_completed(futures):
                f.result()


# ============================================================================
# Category 4: Network & Sony VAIO Hardware Inventory Compliance
# ============================================================================

class TestTier5NetworkAndVaioHardwareStress:
    """Stress testing network sysfs scanning, VAIO hardware tiering, and inventory compliance."""

    def test_network_interface_discovery_and_carrier_detection(self, tmp_path):
        """Verify detection of wlan0, eth0, carrier status, and speed handling."""
        # Simulated wlan0
        wlan = tmp_path / "wlan0"
        wlan.mkdir()
        (wlan / "wireless").mkdir()
        (wlan / "operstate").write_text("up\n")
        (wlan / "carrier").write_text("1\n")

        # Simulated eth0
        eth = tmp_path / "eth0"
        eth.mkdir()
        (eth / "operstate").write_text("up\n")
        (eth / "carrier").write_text("1\n")
        (eth / "speed").write_text("1000\n")

        # Simulated loopback (must be ignored)
        lo = tmp_path / "lo"
        lo.mkdir()

        monitor = NetworkHardwareMonitor(sysfs_dir=tmp_path)
        status = monitor.get_status()

        assert status["wifi"]["interface"] == "wlan0"
        assert status["wifi"]["driver"] == "ath9k"
        assert status["wifi"]["connected"] is True

        assert status["ethernet"]["interface"] == "eth0"
        assert status["ethernet"]["driver"] == "r8169"
        assert status["ethernet"]["carrier"] is True
        assert status["ethernet"]["speed_mbps"] == 1000

    def test_sony_vaio_hardware_inventory_specification(self):
        """Verify complete hardware profile matches Sony VAIO VPCYB35AN hardware."""
        mgr = SonyVaioHardwareManager()
        inv = mgr.get_inventory()

        # System
        sys_info = inv["system"]
        assert sys_info["manufacturer"] == "Sony Corporation"
        assert sys_info["product_name"] == "Sony VAIO VPCYB35AN"
        assert "VPCYB Series" in sys_info["model_series"]
        assert sys_info["tier"] == "minimum"

        # CPU
        cpu = inv["cpu"]
        assert "AMD E-450 APU" in cpu["name"]
        assert cpu["architecture"] == "Bobcat x86_64"
        assert cpu["cores"] == 2
        assert cpu["threads"] == 2
        assert cpu["nominal_frequency_ghz"] == 1.65
        assert cpu["tdp_watts"] == 18.0

        # GPU
        gpu = inv["gpu"]
        assert gpu["name"] == "AMD Radeon HD 6320 Graphics"
        assert gpu["vendor_id"] == "0x1002"
        assert gpu["device_id"] == "0x9806"
        assert gpu["driver"] == "radeon"
        assert gpu["mesa_driver"] == "r600"
        assert gpu["vaapi_supported"] is True
        assert gpu["vdpau_supported"] is True

        # Network
        net = inv["network"]
        assert "ath9k" in net["wifi"]["driver"]
        assert "r8169" in net["ethernet"]["driver"]

        # Audio
        audio = inv["audio"]
        assert audio["codec"] == "Realtek ALC269"
        assert audio["driver"] == "snd_hda_codec_realtek"

    def test_hardware_tier_classification_boundary_matrix(self):
        """Adversarial stress of the classify() algorithm across edge boundaries."""
        classify = HardwareDetector.classify

        # Sony VAIO Target: 4GB RAM, 2 threads -> 'minimum'
        assert classify(ram_gb=4.0, cpu_threads=2, vram_gb=0.5, has_cuda=False, has_rocm=False) == "minimum"

        # Boundary: RAM < 8GB or CPU threads <= 2 always forces 'minimum'
        assert classify(ram_gb=7.99, cpu_threads=8, vram_gb=8.0, has_cuda=True, has_rocm=False) == "minimum"
        assert classify(ram_gb=64.0, cpu_threads=2, vram_gb=16.0, has_cuda=True, has_rocm=False) == "minimum"
        assert classify(ram_gb=2.0, cpu_threads=1, vram_gb=0.0, has_cuda=False, has_rocm=False) == "minimum"

        # Standard Tier: 8GB RAM, 4 threads, no GPU acceleration
        assert classify(ram_gb=8.0, cpu_threads=4, vram_gb=0.0, has_cuda=False, has_rocm=False) == "standard"
        assert classify(ram_gb=16.0, cpu_threads=8, vram_gb=2.0, has_cuda=False, has_rocm=False) == "standard"

        # Power Tier: GPU accelerated with >= 8GB VRAM
        assert classify(ram_gb=16.0, cpu_threads=8, vram_gb=8.0, has_cuda=True, has_rocm=False) == "power"
        assert classify(ram_gb=16.0, cpu_threads=8, vram_gb=8.0, has_cuda=False, has_rocm=True) == "power"

        # Power Tier: High system compute without discrete GPU
        assert classify(ram_gb=24.0, cpu_threads=4, vram_gb=0.0, has_cuda=False, has_rocm=False) == "power"
        assert classify(ram_gb=16.0, cpu_threads=12, vram_gb=0.0, has_cuda=False, has_rocm=False) == "power"
        assert classify(ram_gb=16.0, cpu_threads=6, vram_gb=6.0, has_cuda=False, has_rocm=False) == "power"

    def test_drm_gpu_vendor_identification(self, tmp_path):
        """Sysfs DRM vendor ID parsing for AMD (0x1002), NVIDIA (0x10de), Intel (0x8086)."""
        card0 = tmp_path / "card0" / "device"
        card0.mkdir(parents=True)
        vendor_file = card0 / "vendor"

        with patch("serein_core.hardware.SYSFS_DRM_ROOT", tmp_path), \
             patch("shutil.which", return_value=None):

            # AMD Radeon
            vendor_file.write_text("0x1002\n")
            name, vram, cuda, rocm = HardwareDetector._detect_gpu()
            assert name == "AMD Radeon Graphics"
            assert vram == 0.5
            assert cuda is False
            assert rocm is True

            # NVIDIA
            vendor_file.write_text("0x10de\n")
            name_nv, vram_nv, cuda_nv, rocm_nv = HardwareDetector._detect_gpu()
            assert name_nv == "NVIDIA Graphics"
            assert cuda_nv is True
            assert rocm_nv is False

            # Intel
            vendor_file.write_text("0x8086\n")
            name_in, vram_in, cuda_in, rocm_in = HardwareDetector._detect_gpu()
            assert name_in == "Intel Graphics"
            assert cuda_in is False
            assert rocm_in is False


# ============================================================================
# Category 5: InstallerEngine Input Validation & Injection Resistance
# ============================================================================

class TestTier5InstallerValidationAdversarial:
    """Stress testing the installer against command injection, traversal, and invalid parameter bounds."""

    def test_installer_disk_injection_and_path_traversal_rejection(self):
        """Ensure dry-run and install reject malicious shell characters and paths."""
        engine = InstallerEngine()

        injection_attacks = [
            "/dev/sda; rm -rf /",
            "/dev/sda && touch /tmp/pwned",
            "/dev/sda|echo pwned",
            "/dev/sda`reboot`",
            "/dev/sda$(id)",
            "/dev/sda\nrm -rf /",
            "/dev/../etc/shadow",
            "C:\\Windows\\System32",
            "/dev/null",
            "/dev/zero",
            "/dev/ram0",
            "/dev/loop0",
        ]

        for payload in injection_attacks:
            # dry-run must return valid=False without throwing an unhandled exception
            res = engine.run_dry_run(disk_device=payload)
            assert res["valid"] is False, f"Payload unexpectedly accepted: {payload}"
            assert res["error"] is not None

            # start_installation must immediately raise ValueError
            with pytest.raises(ValueError):
                engine.start_installation(disk_device=payload)

    def test_partition_path_rejection_for_partitions_as_disks(self):
        """Partitions (e.g. /dev/sda1) must not be accepted as whole install targets."""
        engine = InstallerEngine()
        partition_paths = ["/dev/sda1", "/dev/sdb2", "/dev/nvme0n1p1", "/dev/vda1", "/dev/mmcblk0p1"]

        for part in partition_paths:
            res = engine.run_dry_run(disk_device=part)
            assert res["valid"] is False, f"Partition path accepted as target disk: {part}"
            with pytest.raises(ValueError):
                engine.start_installation(disk_device=part)

    def test_installer_hostname_adversarial_validation(self):
        """RFC 1123 compliant hostname validation in start_installation."""
        engine = InstallerEngine()

        invalid_hostnames = [
            "",
            "-startwithhyphen",
            "endwithhyphen-",
            "contains space",
            "invalid_underscore",
            "host!@#$",
            "a" * 64,  # Exceeds max 63 chars per label
        ]

        for h in invalid_hostnames:
            with pytest.raises(ValueError, match="Invalid hostname"):
                engine.start_installation(disk_device="/dev/sda", hostname=h)

        # Valid hostnames must not raise hostname ValueError
        valid_hostnames = ["serein", "serein-vaio", "serein01", "my-desktop-host"]
        for v in valid_hostnames:
            assert HOSTNAME_REGEX.match(v) is not None

    def test_installer_username_adversarial_validation(self):
        """Linux useradd username validation rules (^[a-z_][a-z0-9_-]{0,31}$)."""
        engine = InstallerEngine()

        invalid_usernames = [
            "",
            "123numeric_start",
            "CapitalUser",
            "user with spaces",
            "root;rm",
            "user@host",
            "a" * 33,  # Max 32 chars
        ]

        for u in invalid_usernames:
            with pytest.raises(ValueError, match="Invalid username"):
                engine.start_installation(disk_device="/dev/sda", user=u)

        valid_usernames = ["serein", "_serein", "user1", "serein-user", "serein_admin"]
        for v in valid_usernames:
            assert USERNAME_REGEX.match(v) is not None

    def test_installer_disk_capacity_boundaries(self):
        """Enforce strict 20GB minimum disk space requirement."""
        engine = InstallerEngine()

        # Under 20GB must fail
        with pytest.raises(ValueError, match="below minimum required 20.0 GB"):
            engine.plan_partitions(disk_device="/dev/sda", disk_size_gb=19.9)

        with pytest.raises(ValueError):
            engine.plan_partitions(disk_device="/dev/sda", disk_size_gb=0.0)

        with pytest.raises(ValueError):
            engine.plan_partitions(disk_device="/dev/sda", disk_size_gb=-10.0)

        # 20GB and above must succeed
        plan = engine.plan_partitions(disk_device="/dev/sda", disk_size_gb=20.0)
        assert plan.disk_size_gb == 20.0
        assert len(plan.partitions) >= 2

    def test_installer_active_boot_media_lockout(self):
        """Prevent accidental self-overwrite of live USB boot media."""
        engine = InstallerEngine()

        # Mock detect_disks returning live installer on /dev/sdb
        mock_disks = [
            {"device": "/dev/sda", "size_gb": 500.0, "is_boot_media": False},
            {"device": "/dev/sdb", "size_gb": 16.0, "is_boot_media": True},
        ]
        with patch.object(engine, "detect_disks", return_value=mock_disks):
            res = engine.run_dry_run(disk_device="/dev/sdb")
            assert res["valid"] is False
            assert "Refusing to install on active boot media" in res["summary"]


# ============================================================================
# Category 6: Partition Geometry, /etc/fstab and GRUB Staging
# ============================================================================

class TestTier5InstallerPlanningAndGrubAdversarial:
    """Verify dual-mode partition math, UUID specifications, fstab, and GRUB commands."""

    def test_uefi_partition_geometry_and_invariants(self):
        """UEFI GPT: 512MB ESP (vfat), 4GB Swap, remainder ext4 Root."""
        engine = InstallerEngine()
        plan = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=100.0)

        assert plan.firmware_type == "uefi"
        assert plan.table_type == "gpt"
        assert len(plan.partitions) == 3

        p1, p2, p3 = plan.partitions
        # ESP
        assert p1.number == 1
        assert p1.device == "/dev/sda1"
        assert p1.size_mb == DEFAULT_EFI_SIZE_MB
        assert p1.fstype == "vfat"
        assert p1.mount_point == "/boot/efi"
        assert "boot" in p1.flags and "esp" in p1.flags

        # Swap
        assert p2.number == 2
        assert p2.device == "/dev/sda2"
        assert p2.size_mb == DEFAULT_SWAP_SIZE_MB
        assert p2.fstype == "swap"

        # Root
        assert p3.number == 3
        assert p3.device == "/dev/sda3"
        assert p3.fstype == "ext4"
        assert p3.mount_point == "/"
        # Sum of partition sizes equals total disk MB
        assert p1.size_mb + p2.size_mb + p3.size_mb == int(100.0 * 1024)

    def test_bios_partition_geometry_and_invariants(self):
        """Legacy BIOS MBR: Root (ext4, boot flag) and 4GB Swap."""
        engine = InstallerEngine()
        plan = engine.plan_partitions(disk_device="/dev/sda", firmware_type="bios", disk_size_gb=100.0)

        assert plan.firmware_type == "bios"
        assert plan.table_type == "msdos"
        assert len(plan.partitions) == 2

        p1, p2 = plan.partitions
        assert p1.number == 1
        assert p1.device == "/dev/sda1"
        assert p1.fstype == "ext4"
        assert p1.mount_point == "/"
        assert "boot" in p1.flags

        assert p2.number == 2
        assert p2.device == "/dev/sda2"
        assert p2.size_mb == DEFAULT_SWAP_SIZE_MB
        assert p2.fstype == "swap"

        assert p1.size_mb + p2.size_mb == int(100.0 * 1024)

    def test_nvme_and_mmc_partition_naming_rule(self):
        """Disks ending with digits (/dev/nvme0n1, /dev/mmcblk0) must insert 'p' before partition number."""
        engine = InstallerEngine()

        # NVMe
        plan_nvme = engine.plan_partitions(disk_device="/dev/nvme0n1", firmware_type="uefi", disk_size_gb=256.0)
        assert plan_nvme.partitions[0].device == "/dev/nvme0n1p1"
        assert plan_nvme.partitions[1].device == "/dev/nvme0n1p2"
        assert plan_nvme.partitions[2].device == "/dev/nvme0n1p3"

        # MMC
        plan_mmc = engine.plan_partitions(disk_device="/dev/mmcblk0", firmware_type="bios", disk_size_gb=64.0)
        assert plan_mmc.partitions[0].device == "/dev/mmcblk0p1"
        assert plan_mmc.partitions[1].device == "/dev/mmcblk0p2"

    def test_uuid_generation_formats_and_fstab_syntax(self):
        """ESP must have FAT32 XXXX-XXXX format; Root and Swap must have UUID4."""
        engine = InstallerEngine()
        plan = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=80.0)

        esp = plan.partitions[0]
        swap = plan.partitions[1]
        root = plan.partitions[2]

        fat32_regex = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")
        uuid4_regex = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

        assert fat32_regex.match(esp.uuid) is not None, f"ESP UUID not FAT32 format: {esp.uuid}"
        assert uuid4_regex.match(swap.uuid) is not None, f"Swap UUID not UUID4: {swap.uuid}"
        assert uuid4_regex.match(root.uuid) is not None, f"Root UUID not UUID4: {root.uuid}"

        fstab = plan.fstab
        assert f"UUID={root.uuid} / ext4 errors=remount-ro,defaults,noatime 0 1" in fstab
        assert f"UUID={esp.uuid} /boot/efi vfat umask=0077,defaults 0 2" in fstab
        assert f"UUID={swap.uuid} none swap sw 0 0" in fstab

    def test_dual_mode_grub_bootloader_commands(self):
        """Target-specific GRUB command strings."""
        engine = InstallerEngine()

        plan_uefi = engine.plan_partitions(disk_device="/dev/sda", firmware_type="uefi", disk_size_gb=50.0)
        assert "grub-install --target=x86_64-efi --efi-directory=/mnt/boot/efi --bootloader-id=SereinOS --recheck" == plan_uefi.bootloader_cmd

        plan_bios = engine.plan_partitions(disk_device="/dev/sda", firmware_type="bios", disk_size_gb=50.0)
        assert plan_bios.bootloader_cmd == f"grub-install --target=i386-pc --recheck {shlex.quote(plan_bios.disk)}"


# ============================================================================
# Category 7: InstallerEngine Lifecycle, Concurrency & Task Tracking
# ============================================================================

class TestTier5InstallerLifecycleAndConcurrencyAdversarial:
    """Stress testing the async installation worker, concurrency lock, and progress polling."""

    def test_installer_concurrent_task_conflict(self):
        """Simultaneous start_installation calls must be rejected with RuntimeError."""
        engine = InstallerEngine()

        # Start first installation (simulation mode)
        task1 = engine.start_installation(disk_device="/dev/sda", dry_run=True)
        assert task1 is not None

        # Immediate second installation must raise RuntimeError
        with pytest.raises(RuntimeError, match="An installation task is already in progress"):
            engine.start_installation(disk_device="/dev/sda", dry_run=True)

        # Wait for worker thread to finish
        for _ in range(50):
            p = engine.get_progress(task1)
            if p["done"]:
                break
            time.sleep(0.05)
        assert p["done"] is True
        assert p["phase"] == "complete"

    def test_installer_progress_state_transitions(self):
        """Progress transitions smoothly through: validating -> partitioning -> formatting -> copying -> configuring -> bootloader -> complete."""
        engine = InstallerEngine()
        task_id = engine.start_installation(disk_device="/dev/sda", dry_run=True)

        seen_phases = set()
        for _ in range(100):
            prog = engine.get_progress(task_id)
            seen_phases.add(prog["phase"])
            if prog["done"]:
                break
            time.sleep(0.05)

        assert prog["done"] is True
        assert prog["percent"] == 100
        assert "complete" in seen_phases
        assert any("partition" in p for p in seen_phases)

    def test_installer_failure_propagation(self):
        """Failure during dry-run pre-flight properly flags task as failed."""
        engine = InstallerEngine()
        # Mock run_dry_run returning valid=False
        with patch.object(engine, "run_dry_run", return_value={"valid": False, "error": "Disk hardware I/O error", "summary": "Failed"}):
            task_id = engine.start_installation(disk_device="/dev/sda", dry_run=True)

            for _ in range(50):
                prog = engine.get_progress(task_id)
                if prog["done"]:
                    break
                time.sleep(0.05)

            assert prog["done"] is True
            assert prog["phase"] == "failed"
            assert "Disk hardware I/O error" in prog["error"]

    def test_cli_main_arguments_and_execution_guard(self, capsys):
        """CLI arguments: --list-disks, --dry-run, and refusing --execute without -y."""
        # --list-disks
        rc_list = cli_main(["--list-disks"])
        assert rc_list == 0
        captured = capsys.readouterr()
        assert "DEVICE" in captured.out

        # --dry-run
        rc_dry = cli_main(["--dry-run", "--disk", "/dev/sda", "--json"])
        assert rc_dry == 0
        captured_dry = capsys.readouterr()
        dry_data = json.loads(captured_dry.out)
        assert dry_data["valid"] is True

        # --execute without -y MUST return 1
        rc_exec = cli_main(["--execute", "--disk", "/dev/sda"])
        assert rc_exec == 1
        captured_exec = capsys.readouterr()
        assert "Refusing to execute installation without confirmation flag" in captured_exec.out


# ============================================================================
# Category 8: Web API Hardware & Installer Endpoints Stress
# ============================================================================

class TestTier5WebAPIAdversarialEndpoints:
    """Stress testing FastAPI hardware and installer endpoints with boundary & adversarial requests."""

    def test_api_brightness_boundary_clamping(self, test_client):
        """POST /api/system/brightness boundary clamping."""
        # Negative brightness
        res1 = test_client.post("/api/system/brightness", json={"brightness_percent": -100})
        assert res1.status_code == 200
        assert res1.json()["brightness_percent"] == 0

        # Excessive brightness
        res2 = test_client.post("/api/system/brightness", json={"brightness_percent": 300})
        assert res2.status_code == 200
        assert res2.json()["brightness_percent"] == 100

        # Query GET
        res_get = test_client.get("/api/system/brightness")
        assert res_get.status_code == 200
        assert 0 <= res_get.json()["brightness_percent"] <= 100

    def test_api_volume_boundary_clamping(self, test_client):
        """POST /api/system/volume clamping and mute toggling."""
        # Negative volume
        res1 = test_client.post("/api/system/volume", json={"volume_percent": -50, "muted": True})
        assert res1.status_code == 200
        assert res1.json()["volume_percent"] == 0
        assert res1.json()["muted"] is True

        # Excessive volume
        res2 = test_client.post("/api/system/volume", json={"volume_percent": 250, "muted": False})
        assert res2.status_code == 200
        assert res2.json()["volume_percent"] == 100
        assert res2.json()["muted"] is False

    def test_api_installer_dry_run_injection_resistance(self, test_client):
        """POST /api/installer/dry-run rejecting injection without 500 error."""
        res = test_client.post("/api/installer/dry-run", json={"disk": "/dev/sda; rm -rf /"})
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is False
        assert "illegal shell characters" in data["summary"] or "not recognized" in data["error"]

    def test_api_installer_install_validation_and_conflict(self, test_client):
        """POST /api/installer/install handles 400 Bad Request and 409 Conflict."""
        # Invalid username -> 422 Unprocessable Entity (Pydantic schema validation)
        res_bad = test_client.post(
            "/api/installer/install",
            json={"disk": "/dev/sda", "user": "INVALID USER"},
        )
        assert res_bad.status_code in (400, 422)

        # Valid install starts task
        res_ok = test_client.post(
            "/api/installer/install",
            json={"disk": "/dev/sda", "user": "serein", "hostname": "serein-test"},
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["status"] == "started"
        task_id = res_ok.json()["task_id"]

        # Duplicate call while running -> 409 Conflict
        res_conflict = test_client.post(
            "/api/installer/install",
            json={"disk": "/dev/sda", "user": "serein"},
        )
        assert res_conflict.status_code == 409

        # GET /api/installer/progress
        res_prog = test_client.get(f"/api/installer/progress?task_id={task_id}")
        assert res_prog.status_code == 200
        assert "phase" in res_prog.json()


# ============================================================================
# Category 9: ISO Build Scripts & Plymouth Theme Integrity
# ============================================================================

class TestTier5IsoAndPlymouthIntegrity:
    """Static verification of build_iso.sh, install_artifacts_container.sh, and Plymouth assets."""

    def test_build_iso_shell_script_static_audit(self):
        """Verify build_iso.sh exists, contains strict error trapping, GRUB packages, and volume label."""
        iso_script = REPO_ROOT / "serein-installer" / "build_iso.sh"
        assert iso_script.exists()
        content = iso_script.read_text(encoding="utf-8")

        # Shell strictness
        assert "#!/bin/bash" in content
        assert "set -euo pipefail" in content

        # Dual-mode GRUB packaging
        assert "grub-pc-bin" in content
        assert "grub-efi-amd64-bin" in content

        # Sony VAIO firmware packages
        assert "firmware-amd-graphics" in content
        assert "firmware-atheros" in content
        assert "firmware-realtek" in content

        # Plymouth integration
        assert "plymouth" in content
        assert "serein-glow" in content

        # Kernel cmdline quiet splash & nomodeset
        assert "boot=live components quiet splash" in content
        assert "boot=live components nomodeset" in content

        # ISO volume ID
        assert 'SEREIN_OS' in content

    def test_install_artifacts_container_script_audit(self):
        """Verify install_artifacts_container.sh wrapper staging and autostart."""
        script = REPO_ROOT / "serein-installer" / "install_artifacts_container.sh"
        assert script.exists()
        content = script.read_text(encoding="utf-8")

        # Core wrappers
        assert "/usr/local/bin/serein-core" in content
        assert "/usr/local/bin/serein-voice" in content
        assert "/usr/local/bin/serein-installer" in content
        assert "/usr/local/bin/serein-desktop" in content

        # Kiosk and Plymouth
        assert "weston" in content
        assert "plymouth-set-default-theme serein-glow" in content

    def test_plymouth_theme_and_script_syntax(self):
        """Verify serein-glow.plymouth and serein-glow.script mathematical animation."""
        theme_dir = REPO_ROOT / "serein-installer" / "plymouth" / "serein-glow"
        plymouth_file = theme_dir / "serein-glow.plymouth"
        script_file = theme_dir / "serein-glow.script"
        logo_png = theme_dir / "logo.png"
        glow_png = theme_dir / "glow.png"

        assert plymouth_file.exists()
        assert script_file.exists()
        assert logo_png.exists()
        assert glow_png.exists()

        plymouth_txt = plymouth_file.read_text(encoding="utf-8")
        assert "ModuleName=script" in plymouth_txt
        assert "serein-glow.script" in plymouth_txt

        script_txt = script_file.read_text(encoding="utf-8")
        # Sinusoidal breathing glow math
        assert "Math.Sin(anim_time * pulse_speed)" in script_txt
        assert "Plymouth.SetRefreshFunction" in script_txt
        assert "Plymouth.SetBootProgressFunction" in script_txt

    def test_desktop_ui_installer_wizard_dom_elements(self):
        """Verify UI assets contain desktop shortcut, modal wizard, and progress indicators."""
        html_file = REPO_ROOT / "assets" / "ui" / "index.html"
        assert html_file.exists()
        html_txt = html_file.read_text(encoding="utf-8")

        assert 'id="modal-installer"' in html_txt
        assert 'id="desktop-installer-shortcut"' in html_txt
        assert 'id="installer-disk-select"' in html_txt
        assert 'id="installer-progress-bar"' in html_txt
        assert 'id="installer-terminal-log"' in html_txt

        js_file = REPO_ROOT / "assets" / "ui" / "app.js"
        assert js_file.exists()
        js_txt = js_file.read_text(encoding="utf-8")
        assert "installer-disk-select" in js_txt
        assert "/api/installer/disks" in js_txt
        assert "/api/installer/dry-run" in js_txt
        assert "/api/installer/install" in js_txt
