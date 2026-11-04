"""
Milestone 3 Empirical Challenger Stress Suite: Sony VAIO Hardware & Sysfs Controls
Authored by: challenger_m3_hardware_1
Target: Milestone 3 Sony VAIO VPCYB35AN Physical Hardware Enablement

Empirical verification of:
1. Sysfs brightness controller under simulated filesystems (missing files, permission errors, extreme values -100, 500, non-integer).
2. Battery monitor under simulated /sys/class/power_supply (discharging, charging, full, AC offline, missing battery, corrupted data).
3. Audio controller under simulated ALSA outputs, errors, timeouts, and non-ALSA headless environments.
4. Hardware inventory correctness matching Sony VAIO VPCYB35AN target specs (Bobcat E-450, Radeon HD 6320, AR9285, RTL8168, ALC269).
5. Web API boundary, validation, and multi-threaded concurrency stress.
6. Static integrity verification of init_hardware.sh, udev backlight rules, logind power conf, and Xorg radeon conf.
"""
from __future__ import annotations

import concurrent.futures
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from starlette.testclient import TestClient

# Ensure serein-core/src is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
CORE_SRC = ROOT_DIR / "serein-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from serein_core.hardware import (
    AudioController,
    BacklightController,
    BatteryMonitor,
    HardwareDetector,
    NetworkHardwareMonitor,
    SonyVaioHardwareManager,
    get_battery_status,
    get_brightness,
    get_hardware_inventory,
    get_network_hardware_status,
    get_volume,
    set_brightness,
    set_volume,
)
from serein_core.web_app import app


# ============================================================================
# Category 1: BacklightController Adversarial & Edge Case Stress
# ============================================================================

class TestBacklightControllerStress:
    """Stress testing the display backlight sysfs controller under adversarial scenarios."""

    def test_backlight_missing_files_in_device_dir(self, tmp_path):
        """Device folder exists, but brightness or max_brightness files are absent."""
        # Scenario A: empty device folder
        dev_empty = tmp_path / "radeon_bl0"
        dev_empty.mkdir()
        ctrl_empty = BacklightController(sysfs_dir=tmp_path)
        info = ctrl_empty.get_brightness()
        assert 0 <= info["brightness_percent"] <= 100
        assert info["max_brightness"] == 255

        # Scenario B: only max_brightness exists, brightness is missing
        dev_no_bright = tmp_path / "acpi_video0"
        dev_no_bright.mkdir()
        (dev_no_bright / "max_brightness").write_text("100\n")
        # Ensure priority points to acpi_video0 by removing empty radeon_bl0
        dev_empty.rmdir()
        ctrl_no_bright = BacklightController(sysfs_dir=tmp_path)
        info_b = ctrl_no_bright.get_brightness()
        assert 0 <= info_b["brightness_percent"] <= 100

        # Scenario C: only brightness exists, max_brightness is missing
        (dev_no_bright / "max_brightness").unlink()
        (dev_no_bright / "brightness").write_text("50\n")
        ctrl_no_max = BacklightController(sysfs_dir=tmp_path)
        info_c = ctrl_no_max.get_brightness()
        assert 0 <= info_c["brightness_percent"] <= 100

    def test_backlight_corrupted_sysfs_content(self, tmp_path):
        """Sysfs files contain corrupted, non-integer, zero, or negative content."""
        dev = tmp_path / "radeon_bl0"
        dev.mkdir()
        ctrl = BacklightController(sysfs_dir=tmp_path)

        # Division by zero protection: max_brightness == 0
        (dev / "max_brightness").write_text("0\n")
        (dev / "brightness").write_text("10\n")
        info = ctrl.get_brightness()
        # Should not raise ZeroDivisionError; fallback returned
        assert 0 <= info["brightness_percent"] <= 100

        # Corrupted non-numeric strings
        (dev / "max_brightness").write_text("invalid_max\n")
        (dev / "brightness").write_text("NaN\n")
        info_corrupt = ctrl.get_brightness()
        assert 0 <= info_corrupt["brightness_percent"] <= 100

        # Empty files
        (dev / "max_brightness").write_text("")
        (dev / "brightness").write_text("")
        info_empty = ctrl.get_brightness()
        assert 0 <= info_empty["brightness_percent"] <= 100

    def test_backlight_device_priority_resolution(self, tmp_path):
        """Verify device selection preference: radeon_bl0 > radeon_bl > acpi_video0 > intel_backlight."""
        # Create multiple devices
        for name in ["intel_backlight", "acpi_video0", "radeon_bl", "radeon_bl0", "other_bl"]:
            p = tmp_path / name
            p.mkdir()
            (p / "brightness").write_text("150\n")
            (p / "max_brightness").write_text("255\n")

        ctrl = BacklightController(sysfs_dir=tmp_path)
        info = ctrl.get_brightness()
        assert info["device"] == "radeon_bl0"

        # Remove radeon_bl0 -> should pick radeon_bl
        (tmp_path / "radeon_bl0" / "brightness").unlink()
        ctrl2 = BacklightController(sysfs_dir=tmp_path)
        assert ctrl2.get_brightness()["device"] == "radeon_bl"

        # Remove radeon_bl -> should pick acpi_video0
        (tmp_path / "radeon_bl" / "brightness").unlink()
        ctrl3 = BacklightController(sysfs_dir=tmp_path)
        assert ctrl3.get_brightness()["device"] == "acpi_video0"

    def test_backlight_extreme_values_and_clamping(self, tmp_path):
        """Test extreme input percentages: negative, zero, over 100, float inputs."""
        dev = tmp_path / "radeon_bl0"
        dev.mkdir()
        (dev / "max_brightness").write_text("200\n")
        (dev / "brightness").write_text("100\n")
        ctrl = BacklightController(sysfs_dir=tmp_path)

        # Underflow
        res_neg = ctrl.set_brightness(-500)
        assert res_neg["brightness_percent"] == 0
        assert int((dev / "brightness").read_text().strip()) == 0

        # Overflow
        res_over = ctrl.set_brightness(9999)
        assert res_over["brightness_percent"] == 100
        assert int((dev / "brightness").read_text().strip()) == 200

        # Exact boundaries
        res_zero = ctrl.set_brightness(0)
        assert res_zero["brightness_percent"] == 0
        assert int((dev / "brightness").read_text().strip()) == 0

        res_hundred = ctrl.set_brightness(100)
        assert res_hundred["brightness_percent"] == 100
        assert int((dev / "brightness").read_text().strip()) == 200

        # Float conversion
        res_float = ctrl.set_brightness(42.7)
        assert res_float["brightness_percent"] == 42
        assert res_float["success"] is True

    def test_backlight_permission_error_simulation(self, tmp_path, monkeypatch):
        """Simulate PermissionError when writing to /sys/class/backlight without privileges."""
        dev = tmp_path / "radeon_bl0"
        dev.mkdir()
        (dev / "max_brightness").write_text("255\n")
        (dev / "brightness").write_text("128\n")
        ctrl = BacklightController(sysfs_dir=tmp_path)

        def mock_write_text(self, data, *args, **kwargs):
            if "brightness" in self.name:
                raise PermissionError("[Errno 13] Permission denied: '/sys/class/backlight/radeon_bl0/brightness'")
            return super(Path, self).write_text(data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", mock_write_text)

        res = ctrl.set_brightness(80)
        assert res["success"] is False
        assert "error" in res
        assert "Permission denied" in res["error"]
        assert res["brightness_percent"] == 80

    def test_backlight_rapid_state_churn(self, tmp_path):
        """Rapidly toggle brightness back and forth between 0 and 100 to ensure state integrity."""
        dev = tmp_path / "radeon_bl0"
        dev.mkdir()
        (dev / "max_brightness").write_text("255\n")
        (dev / "brightness").write_text("128\n")
        ctrl = BacklightController(sysfs_dir=tmp_path)

        for i in range(50):
            target = 0 if i % 2 == 0 else 100
            res = ctrl.set_brightness(target)
            assert res["brightness_percent"] == target
            assert res["success"] is True

            info = ctrl.get_brightness()
            assert info["brightness_percent"] == target


# ============================================================================
# Category 2: BatteryMonitor Adversarial & Edge Case Stress
# ============================================================================

class TestBatteryMonitorStress:
    """Stress testing battery and power supply monitoring under simulated edge cases."""

    def test_battery_discharging_state(self, tmp_path):
        """Battery discharging with AC disconnected."""
        bat = tmp_path / "BAT1"
        bat.mkdir()
        (bat / "capacity").write_text("37\n")
        (bat / "status").write_text("Discharging\n")

        ac = tmp_path / "ACAD"
        ac.mkdir()
        (ac / "online").write_text("0\n")

        monitor = BatteryMonitor(sysfs_dir=tmp_path)
        st = monitor.get_status()
        assert st["present"] is True
        assert st["capacity_percent"] == 37
        assert st["status"] == "Discharging"
        assert st["ac_online"] is False
        assert st["device"] == "BAT1"

    def test_battery_charging_and_full_states(self, tmp_path):
        """Battery charging and full states with AC adapter connected."""
        bat = tmp_path / "BAT0"
        bat.mkdir()
        ac = tmp_path / "ACAD"
        ac.mkdir()
        (ac / "online").write_text("1\n")

        monitor = BatteryMonitor(sysfs_dir=tmp_path)

        # Charging
        (bat / "capacity").write_text("65\n")
        (bat / "status").write_text("Charging\n")
        st_charge = monitor.get_status()
        assert st_charge["capacity_percent"] == 65
        assert st_charge["status"] == "Charging"
        assert st_charge["ac_online"] is True

        # Full
        (bat / "capacity").write_text("100\n")
        (bat / "status").write_text("Full\n")
        st_full = monitor.get_status()
        assert st_full["capacity_percent"] == 100
        assert st_full["status"] == "Full"
        assert st_full["ac_online"] is True

    def test_battery_missing_subsystems(self, tmp_path):
        """Test behavior when either battery or AC adapter directory is absent."""
        # Only AC present, no battery
        ac = tmp_path / "ACAD"
        ac.mkdir()
        (ac / "online").write_text("1\n")

        monitor_no_bat = BatteryMonitor(sysfs_dir=tmp_path)
        st_no_bat = monitor_no_bat.get_status()
        assert st_no_bat["present"] is True
        assert 0 <= st_no_bat["capacity_percent"] <= 100
        assert st_no_bat["ac_online"] is True

        # Only battery present, no ACAD
        (ac / "online").unlink()
        ac.rmdir()
        bat = tmp_path / "BAT1"
        bat.mkdir()
        (bat / "capacity").write_text("55\n")
        (bat / "status").write_text("Discharging\n")

        monitor_no_ac = BatteryMonitor(sysfs_dir=tmp_path)
        st_no_ac = monitor_no_ac.get_status()
        assert st_no_ac["capacity_percent"] == 55
        assert st_no_ac["status"] == "Discharging"

    def test_battery_corrupted_sysfs_data(self, tmp_path):
        """Handle corrupted capacity values, extreme numbers, and missing status files."""
        bat = tmp_path / "BAT1"
        bat.mkdir()
        ac = tmp_path / "AC"
        ac.mkdir()
        (ac / "online").write_text("1\n")

        monitor = BatteryMonitor(sysfs_dir=tmp_path)

        # Out-of-bounds capacity: 180% -> clamped to 100
        (bat / "capacity").write_text("180\n")
        (bat / "status").write_text("Charging\n")
        st_over = monitor.get_status()
        assert st_over["capacity_percent"] == 100

        # Out-of-bounds capacity: -20% -> clamped to 0
        (bat / "capacity").write_text("-20\n")
        st_under = monitor.get_status()
        assert st_under["capacity_percent"] == 0

        # Status file missing -> inferred as Charging when AC is online
        (bat / "capacity").write_text("70\n")
        (bat / "status").unlink()
        st_infer_charge = monitor.get_status()
        assert st_infer_charge["status"] == "Charging"

        # Inferred as Discharging when AC is offline
        (ac / "online").write_text("0\n")
        st_infer_disch = monitor.get_status()
        assert st_infer_disch["status"] == "Discharging"

        # Corrupted non-numeric capacity
        (bat / "capacity").write_text("INVALID_CAPACITY\n")
        st_corrupt = monitor.get_status()
        assert 0 <= st_corrupt["capacity_percent"] <= 100

    def test_battery_adp_adapter_naming(self, tmp_path):
        """Some laptops label the AC adapter as ADP1 instead of ACAD."""
        adp = tmp_path / "ADP1"
        adp.mkdir()
        (adp / "online").write_text("1\n")

        bat = tmp_path / "BAT1"
        bat.mkdir()
        (bat / "capacity").write_text("90\n")
        (bat / "status").write_text("Charging\n")

        monitor = BatteryMonitor(sysfs_dir=tmp_path)
        st = monitor.get_status()
        assert st["ac_online"] is True
        assert st["capacity_percent"] == 90


# ============================================================================
# Category 3: AudioController Adversarial & Edge Case Stress
# ============================================================================

class TestAudioControllerStress:
    """Stress testing the ALSA audio volume and mute controller."""

    def test_audio_controller_headless_in_memory_persistence(self):
        """Non-ALSA / headless environment uses reliable in-memory persistence."""
        audio = AudioController(amixer_bin="/nonexistent/path/to/amixer")
        assert audio.get_volume()["volume_percent"] == 75
        assert audio.get_volume()["muted"] is False

        # Set volume
        res1 = audio.set_volume(40, muted=True)
        assert res1["volume_percent"] == 40
        assert res1["muted"] is True
        assert res1["success"] is True

        v1 = audio.get_volume()
        assert v1["volume_percent"] == 40
        assert v1["muted"] is True

        # Toggle mute only
        res2 = audio.set_mute(False)
        assert res2["volume_percent"] == 40
        assert res2["muted"] is False

        v2 = audio.get_volume()
        assert v2["volume_percent"] == 40
        assert v2["muted"] is False

    def test_audio_controller_amixer_parsing_variations(self, monkeypatch):
        """Test various realistic amixer stdout patterns."""
        audio = AudioController(amixer_bin="/usr/bin/amixer")

        # Case 1: Standard stereo unmuted 60%
        stdout_stereo = (
            "Simple mixer control 'Master',0\n"
            "  Capabilities: pvolume pswitch\n"
            "  Playback channels: Front Left - Front Right\n"
            "  Limits: Playback 0 - 64\n"
            "  Mono:\n"
            "  Front Left: Playback 38 [60%] [-26.00dB] [on]\n"
            "  Front Right: Playback 38 [60%] [-26.00dB] [on]\n"
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0, stdout=stdout_stereo))
        v1 = audio.get_volume()
        assert v1["volume_percent"] == 60
        assert v1["muted"] is False

        # Case 2: Muted with non-zero volume [off]
        stdout_muted_non_zero = (
            "Simple mixer control 'Master',0\n"
            "  Capabilities: pvolume pswitch\n"
            "  Playback channels: Front Left - Front Right\n"
            "  Front Left: Playback 51 [80%] [-13.00dB] [off]\n"
            "  Front Right: Playback 51 [80%] [-13.00dB] [off]\n"
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0, stdout=stdout_muted_non_zero))
        v2 = audio.get_volume()
        assert v2["volume_percent"] == 80
        assert v2["muted"] is True

        # Case 3: 100% volume
        stdout_max = (
            "Simple mixer control 'Master',0\n"
            "  Front Left: Playback 64 [100%] [0.00dB] [on]\n"
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0, stdout=stdout_max))
        v3 = audio.get_volume()
        assert v3["volume_percent"] == 100
        assert v3["muted"] is False

        # Case 4: 0% volume unmuted
        stdout_zero = (
            "Simple mixer control 'Master',0\n"
            "  Front Left: Playback 0 [0%] [-64.00dB] [on]\n"
        )
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: MagicMock(returncode=0, stdout=stdout_zero))
        v4 = audio.get_volume()
        assert v4["volume_percent"] == 0
        assert v4["muted"] is False

    def test_audio_controller_amixer_error_and_timeout_resilience(self, monkeypatch):
        """ amixer command failure or timeout gracefully falls back to in-memory state."""
        audio = AudioController(amixer_bin="/usr/bin/amixer")
        audio._volume_percent = 55
        audio._muted = False

        # amixer returns non-zero error code
        err_mock = MagicMock(returncode=1, stderr="amixer: Mixer attach default error: No such file or directory")
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: err_mock)
        v = audio.get_volume()
        assert v["volume_percent"] == 55
        assert v["muted"] is False

        # amixer times out
        def timeout_run(*a, **kw):
            import subprocess
            raise subprocess.TimeoutExpired(cmd=["amixer"], timeout=2)

        monkeypatch.setattr("subprocess.run", timeout_run)
        v_timeout = audio.get_volume()
        assert v_timeout["volume_percent"] == 55

        # set_volume with amixer exception still preserves state and returns success
        res_set = audio.set_volume(90, muted=True)
        assert res_set["volume_percent"] == 90
        assert res_set["muted"] is True
        assert res_set["success"] is True

    def test_audio_controller_extreme_clamping(self):
        """Volume percentages are strictly clamped to [0, 100]."""
        audio = AudioController(amixer_bin=None)

        res_neg = audio.set_volume(-50)
        assert res_neg["volume_percent"] == 0

        res_over = audio.set_volume(300)
        assert res_over["volume_percent"] == 100

        res_zero = audio.set_volume(0)
        assert res_zero["volume_percent"] == 0

        res_hundred = audio.set_volume(100)
        assert res_hundred["volume_percent"] == 100

    def test_audio_controller_secondary_channels_dispatched(self, monkeypatch):
        """Verify secondary channels (Speaker, Headphone, PCM) on Realtek ALC269 are updated."""
        audio = AudioController(amixer_bin="/usr/bin/amixer")
        executed_commands = []

        def mock_run(cmd, *args, **kwargs):
            executed_commands.append(cmd)
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr("subprocess.run", mock_run)
        audio.set_volume(70, muted=False)

        # Ensure Master, Speaker, Headphone, PCM were all targeted
        controls_called = [cmd[4] for cmd in executed_commands if len(cmd) > 4 and cmd[3] == "set"]
        assert "Master" in controls_called
        assert "Speaker" in controls_called
        assert "Headphone" in controls_called
        assert "PCM" in controls_called


# ============================================================================
# Category 4: NetworkHardwareMonitor & Inventory Specs Stress
# ============================================================================

class TestHardwareInventorySpecsStress:
    """Validate Sony VAIO VPCYB35AN hardware inventory correctness and network monitor."""

    def test_network_monitor_sysfs_parsing_and_resilience(self, tmp_path):
        """Inspect network interfaces with up/down states, carrier, and speed."""
        wlan = tmp_path / "wlan0"
        wlan.mkdir()
        (wlan / "wireless").mkdir()
        (wlan / "operstate").write_text("up\n")
        (wlan / "carrier").write_text("1\n")

        eth = tmp_path / "eth0"
        eth.mkdir()
        (eth / "operstate").write_text("down\n")
        (eth / "carrier").write_text("0\n")
        (eth / "speed").write_text("1000\n")

        # Add loopback to ensure it is ignored
        lo = tmp_path / "lo"
        lo.mkdir()
        (lo / "operstate").write_text("up\n")

        monitor = NetworkHardwareMonitor(sysfs_dir=tmp_path)
        status = monitor.get_status()

        assert status["wifi"]["interface"] == "wlan0"
        assert status["wifi"]["connected"] is True
        assert status["wifi"]["driver"] == "ath9k"

        assert status["ethernet"]["interface"] == "eth0"
        assert status["ethernet"]["carrier"] is False
        assert status["ethernet"]["speed_mbps"] == 1000
        assert status["ethernet"]["driver"] == "r8169"

    def test_network_monitor_missing_sysfs(self, tmp_path):
        """Monitor handles nonexistent network sysfs directory gracefully."""
        monitor = NetworkHardwareMonitor(sysfs_dir=tmp_path / "no_net")
        status = monitor.get_status()
        assert "wifi" in status
        assert "ethernet" in status
        assert status["wifi"]["driver"] == "ath9k"
        assert status["ethernet"]["driver"] == "r8169"

    def test_sony_vaio_full_inventory_specifications(self):
        """Empirically verify all subsystem specifications for Sony VAIO VPCYB35AN."""
        inv = get_hardware_inventory()

        # 1. System Platform
        sys_info = inv["system"]
        assert sys_info["manufacturer"] == "Sony Corporation"
        assert sys_info["product_name"] == "Sony VAIO VPCYB35AN"
        assert "VPCYB Series (PCG-31311W)" in sys_info["model_series"]
        assert sys_info["tier"] == "minimum"
        assert sys_info["ram_gb"] > 0

        # 2. CPU: AMD Dual-Core E-450 APU
        cpu = inv["cpu"]
        assert "AMD E-450" in cpu["name"]
        assert cpu["architecture"] == "Bobcat x86_64"
        assert cpu["cores"] == 2
        assert cpu["threads"] == 2
        assert cpu["nominal_frequency_ghz"] == 1.65
        assert cpu["l2_cache_kb"] == 1024
        assert cpu["tdp_watts"] == 18.0

        # 3. GPU: AMD Radeon HD 6320
        gpu = inv["gpu"]
        assert gpu["name"] == "AMD Radeon HD 6320 Graphics"
        assert gpu["vendor_id"] == "0x1002"
        assert gpu["device_id"] == "0x9806"
        assert gpu["driver"] == "radeon"
        assert gpu["mesa_driver"] == "r600"
        assert gpu["vaapi_supported"] is True
        assert gpu["vdpau_supported"] is True

        # 4. Audio: Realtek ALC269
        audio = inv["audio"]
        assert audio["codec"] == "Realtek ALC269"
        assert audio["controller"] == "AMD Hudson Azalia / HDA Intel"
        assert audio["driver"] == "snd_hda_codec_realtek"
        assert 0 <= audio["volume_percent"] <= 100
        assert isinstance(audio["muted"], bool)

        # 5. Network: AR9285 & RTL8168
        net = inv["network"]
        assert net["wifi"]["driver"] == "ath9k"
        assert "Atheros AR9285" in net["wifi"]["chipset"]
        assert net["ethernet"]["driver"] == "r8169"
        assert "Realtek RTL8111/8168" in net["ethernet"]["chipset"]

        # 6. Battery & Brightness present
        assert "battery" in inv
        assert inv["battery"]["present"] is True
        assert "brightness" in inv
        assert 0 <= inv["brightness"]["brightness_percent"] <= 100


# ============================================================================
# Category 5: Web API Endpoints Adversarial & Concurrency Stress
# ============================================================================

class TestWebAPIExtremeStress:
    """Stress testing the FastAPI endpoints with boundary, invalid, and concurrent requests."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_web_api_brightness_boundary_and_validation(self, client):
        """GET & POST /api/system/brightness boundary values and input validation."""
        # Read initial
        get_res = client.get("/api/system/brightness")
        assert get_res.status_code == 200
        assert "brightness_percent" in get_res.json()

        # Set 0%
        res_0 = client.post("/api/system/brightness", json={"brightness_percent": 0})
        assert res_0.status_code == 200
        assert res_0.json()["brightness_percent"] == 0

        # Set 100%
        res_100 = client.post("/api/system/brightness", json={"brightness_percent": 100})
        assert res_100.status_code == 200
        assert res_100.json()["brightness_percent"] == 100

        # Underflow clamped to 0
        res_under = client.post("/api/system/brightness", json={"brightness_percent": -150})
        assert res_under.status_code == 200
        assert res_under.json()["brightness_percent"] == 0

        # Overflow clamped to 100
        res_over = client.post("/api/system/brightness", json={"brightness_percent": 500})
        assert res_over.status_code == 200
        assert res_over.json()["brightness_percent"] == 100

        # Validation error: string instead of int -> 422
        res_bad_type = client.post("/api/system/brightness", json={"brightness_percent": "not_an_int"})
        assert res_bad_type.status_code == 422

        # Validation error: missing field -> 422
        res_missing = client.post("/api/system/brightness", json={})
        assert res_missing.status_code == 422

    def test_web_api_volume_boundary_and_validation(self, client):
        """GET & POST /api/system/volume boundary values, mute toggling, and aliases."""
        # Read initial
        get_res = client.get("/api/system/volume")
        assert get_res.status_code == 200
        assert "volume_percent" in get_res.json()

        # Set volume with mute=True
        res_mute = client.post("/api/system/volume", json={"volume_percent": 25, "muted": True})
        assert res_mute.status_code == 200
        assert res_mute.json()["volume_percent"] == 25
        assert res_mute.json()["muted"] is True

        # Unmute
        res_unmute = client.post("/api/system/volume", json={"volume_percent": 80, "muted": False})
        assert res_unmute.status_code == 200
        assert res_unmute.json()["volume_percent"] == 80
        assert res_unmute.json()["muted"] is False

        # Clamping
        res_clamp = client.post("/api/system/volume", json={"volume_percent": -40})
        assert res_clamp.status_code == 200
        assert res_clamp.json()["volume_percent"] == 0

        # Audio aliases
        alias_get = client.get("/api/audio/status")
        assert alias_get.status_code == 200
        assert "volume_percent" in alias_get.json()

        alias_post = client.post("/api/audio/volume", json={"volume_percent": 60, "muted": False})
        assert alias_post.status_code == 200
        assert alias_post.json()["volume_percent"] == 60

        # Validation error
        res_bad = client.post("/api/system/volume", json={"volume_percent": "abc"})
        assert res_bad.status_code == 422

    def test_web_api_hardware_endpoint(self, client):
        """GET /api/system/hardware returns complete Sony VAIO inventory."""
        res = client.get("/api/system/hardware")
        assert res.status_code == 200
        data = res.json()
        assert data["system"]["manufacturer"] == "Sony Corporation"
        assert "Bobcat" in data["cpu"]["architecture"]
        assert data["gpu"]["driver"] == "radeon"

    def test_web_api_concurrent_requests_stress(self, client):
        """Perform 50 concurrent requests alternating between brightness and volume to check thread safety."""
        def call_endpoint(idx: int):
            if idx % 2 == 0:
                pct = (idx * 2) % 100
                res = client.post("/api/system/brightness", json={"brightness_percent": pct})
                return res.status_code == 200 and res.json()["brightness_percent"] == pct
            else:
                pct = (idx * 3) % 100
                res = client.post("/api/system/volume", json={"volume_percent": pct, "muted": idx % 4 == 0})
                return res.status_code == 200 and res.json()["volume_percent"] == pct

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(call_endpoint, i) for i in range(50)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 50
        assert all(results) is True


# ============================================================================
# Category 6: Configuration Files & Scripts Static Verification
# ============================================================================

class TestHardwareConfigIntegrity:
    """Empirically verify presence, permissions, and syntax of Sony VAIO configuration files."""

    def test_init_hardware_script_integrity(self):
        """scripts/init_hardware.sh exists, contains bash shebang, and required setup steps."""
        script_path = ROOT_DIR / "scripts" / "init_hardware.sh"
        assert script_path.exists(), f"Missing script: {script_path}"

        content = script_path.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash")

        # Verify key hardware initialization phases
        assert "amixer -c 0 set Master 75% unmute" in content
        assert "Auto-Mute Mode" in content
        assert "Speaker" in content and "Headphone" in content and "PCM" in content
        assert "radeon" in content
        assert "/sys/class/backlight" in content
        assert "chmod 664" in content
        assert "chgrp video" in content
        assert "sony-laptop" in content
        assert "NetworkManager" in content
        assert "exit 0" in content

    def test_udev_backlight_rules_integrity(self):
        """etc/udev/rules.d/90-backlight.rules configures video group access."""
        rule_path = ROOT_DIR / "etc" / "udev" / "rules.d" / "90-backlight.rules"
        assert rule_path.exists(), f"Missing rule file: {rule_path}"

        content = rule_path.read_text(encoding="utf-8")
        assert 'SUBSYSTEM=="backlight"' in content
        assert "chmod 664" in content
        assert "chgrp video" in content

    def test_systemd_logind_power_conf_integrity(self):
        """etc/systemd/logind.conf.d/serein-power.conf configures lid switch and power keys."""
        conf_path = ROOT_DIR / "etc" / "systemd" / "logind.conf.d" / "serein-power.conf"
        assert conf_path.exists(), f"Missing conf file: {conf_path}"

        content = conf_path.read_text(encoding="utf-8")
        assert "[Login]" in content
        assert "HandleLidSwitch=suspend" in content
        assert "HandlePowerKey=poweroff" in content
        assert "LidSwitchIgnoreInhibited=yes" in content

    def test_modules_load_sony_laptop_conf_integrity(self):
        """etc/modules-load.d/sony-laptop.conf auto-loads the sony-laptop kernel driver."""
        conf_path = ROOT_DIR / "etc" / "modules-load.d" / "sony-laptop.conf"
        assert conf_path.exists(), f"Missing module conf: {conf_path}"

        content = conf_path.read_text(encoding="utf-8")
        assert "sony-laptop" in content

    def test_xorg_radeon_conf_integrity(self):
        """etc/X11/xorg.conf.d/20-radeon.conf enables Glamor acceleration and TearFree."""
        conf_path = ROOT_DIR / "etc" / "X11" / "xorg.conf.d" / "20-radeon.conf"
        assert conf_path.exists(), f"Missing xorg conf: {conf_path}"

        content = conf_path.read_text(encoding="utf-8")
        assert 'Section "Device"' in content
        assert 'Driver      "radeon"' in content
        assert 'Option      "AccelMethod" "glamor"' in content
        assert 'Option      "TearFree" "on"' in content
        assert 'Option      "DRI" "3"' in content
        assert 'EndSection' in content
