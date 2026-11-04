from pathlib import Path
from unittest.mock import MagicMock
import pytest
from starlette.testclient import TestClient

from serein_core.hardware import (
    HardwareDetector,
    HardwareProfile,
    BacklightController,
    BatteryMonitor,
    AudioController,
    NetworkHardwareMonitor,
    SonyVaioHardwareManager,
    get_brightness,
    set_brightness,
    get_volume,
    set_volume,
    get_battery_status,
    get_hardware_inventory,
)
from serein_core.model_manager import LocalModelManager
from serein_core.web_app import app


def profile(*, ram=16, threads=8, vram=0, cuda=False, rocm=False, tier="standard"):
    return HardwareProfile(
        os_name="TestOS",
        architecture="x86_64",
        cpu_name="Test CPU",
        cpu_cores=max(1, threads // 2),
        cpu_threads=threads,
        ram_gb=ram,
        gpu_name="Test GPU" if vram else None,
        gpu_vram_gb=vram,
        storage_free_gb=100,
        has_avx=True,
        has_avx2=True,
        has_cuda=cuda,
        has_rocm=rocm,
        tier=tier,
    )


# ============================================================================
# Core Hardware Tier & Model Plan Tests (Preserved)
# ============================================================================

def test_minimum_hardware_selects_tiny_model():
    hardware = profile(ram=4, threads=2, tier="minimum")
    manager = LocalModelManager(hardware)
    plan = manager.build_plan()

    assert hardware.tier == "minimum"
    assert plan.model_id == "serein-local-tiny"
    assert plan.concurrency == 1
    assert plan.enable_vision is False
    assert plan.enable_complex_agents is False
    assert plan.max_context_tokens == 4096


def test_standard_hardware_selects_medium_model():
    hardware = profile(ram=16, threads=8, tier="standard")
    plan = LocalModelManager(hardware).build_plan()

    assert plan.hardware_tier == "standard"
    assert plan.model_id == "serein-local-medium"
    assert plan.max_context_tokens == 16384
    assert plan.max_output_tokens == 2048
    assert plan.concurrency == 2


def test_power_hardware_selects_power_model():
    hardware = profile(
        ram=64, threads=16, vram=12, cuda=True, tier="power"
    )
    plan = LocalModelManager(hardware).build_plan()

    assert plan.hardware_tier == "power"
    assert plan.model_id == "serein-local-power"
    assert plan.enable_vision is True
    assert plan.enable_complex_agents is True
    assert plan.gpu_layers == -1
    assert plan.concurrency == 4


def test_gpu_model_is_not_selected_without_enough_vram():
    hardware = profile(
        ram=64, threads=16, vram=4, cuda=True, tier="power"
    )
    plan = LocalModelManager(hardware).build_plan()

    assert plan.model_id == "serein-local-medium"
    assert plan.enable_vision is False
    assert plan.gpu_layers == 0


def test_classifier_public_api_matches_tiers():
    assert HardwareDetector.classify(4, 2, 0, False, False) == "minimum"
    assert HardwareDetector.classify(16, 8, 0, False, False) == "standard"
    assert HardwareDetector.classify(32, 12, 8, True, False) == "power"


# ============================================================================
# Backlight & Brightness Control Tests
# ============================================================================

def test_backlight_controller_with_sysfs_files(tmp_path):
    """Test reading brightness from mock /sys/class/backlight/radeon_bl0 directory."""
    dev_dir = tmp_path / "radeon_bl0"
    dev_dir.mkdir(parents=True)
    (dev_dir / "max_brightness").write_text("255\n")
    (dev_dir / "brightness").write_text("191\n")

    controller = BacklightController(sysfs_dir=tmp_path)
    info = controller.get_brightness()
    assert info["brightness_percent"] == 75
    assert info["device"] == "radeon_bl0"
    assert info["max_brightness"] == 255
    assert info["raw_brightness"] == 191


def test_backlight_controller_set_brightness(tmp_path):
    """Test setting brightness writes scaled raw value to sysfs."""
    dev_dir = tmp_path / "radeon_bl0"
    dev_dir.mkdir(parents=True)
    (dev_dir / "max_brightness").write_text("255\n")
    (dev_dir / "brightness").write_text("255\n")

    controller = BacklightController(sysfs_dir=tmp_path)
    res = controller.set_brightness(50)
    assert res["brightness_percent"] == 50
    assert res["success"] is True

    # 50% of 255 is round(127.5) = 128
    written_val = int((dev_dir / "brightness").read_text().strip())
    assert written_val in (127, 128)


def test_backlight_controller_clamping(tmp_path):
    """Test brightness values are strictly clamped between 0 and 100%."""
    dev_dir = tmp_path / "acpi_video0"
    dev_dir.mkdir(parents=True)
    (dev_dir / "max_brightness").write_text("100\n")
    (dev_dir / "brightness").write_text("50\n")

    controller = BacklightController(sysfs_dir=tmp_path)

    res_low = controller.set_brightness(-25)
    assert res_low["brightness_percent"] == 0
    assert int((dev_dir / "brightness").read_text().strip()) == 0

    res_high = controller.set_brightness(140)
    assert res_high["brightness_percent"] == 100
    assert int((dev_dir / "brightness").read_text().strip()) == 100


def test_backlight_controller_fallback_when_sysfs_missing(tmp_path):
    """Test graceful in-memory fallback when sysfs is absent."""
    missing_dir = tmp_path / "nonexistent_sysfs"
    controller = BacklightController(sysfs_dir=missing_dir)

    info = controller.get_brightness()
    assert 0 <= info["brightness_percent"] <= 100
    assert "device" in info

    res = controller.set_brightness(65)
    assert res["brightness_percent"] == 65
    assert res["success"] is True

    info_updated = controller.get_brightness()
    assert info_updated["brightness_percent"] == 65


# ============================================================================
# Battery & Power Supply Tests
# ============================================================================

def test_battery_monitor_with_sysfs(tmp_path):
    """Test reading battery capacity and status from mock /sys/class/power_supply/BAT1."""
    bat_dir = tmp_path / "BAT1"
    bat_dir.mkdir(parents=True)
    (bat_dir / "capacity").write_text("88\n")
    (bat_dir / "status").write_text("Charging\n")

    ac_dir = tmp_path / "ACAD"
    ac_dir.mkdir(parents=True)
    (ac_dir / "online").write_text("1\n")

    monitor = BatteryMonitor(sysfs_dir=tmp_path)
    st = monitor.get_status()
    assert st["present"] is True
    assert st["capacity_percent"] == 88
    assert st["status"] == "Charging"
    assert st["ac_online"] is True
    assert st["device"] == "BAT1"


def test_battery_monitor_ac_offline(tmp_path):
    """Test battery discharging with AC adapter offline."""
    bat_dir = tmp_path / "BAT0"
    bat_dir.mkdir(parents=True)
    (bat_dir / "capacity").write_text("42\n")
    (bat_dir / "status").write_text("Discharging\n")

    ac_dir = tmp_path / "AC"
    ac_dir.mkdir(parents=True)
    (ac_dir / "online").write_text("0\n")

    monitor = BatteryMonitor(sysfs_dir=tmp_path)
    st = monitor.get_status()
    assert st["capacity_percent"] == 42
    assert st["status"] == "Discharging"
    assert st["ac_online"] is False


def test_battery_monitor_fallback_when_sysfs_missing(tmp_path):
    """Test battery monitor fallback when sysfs is absent."""
    missing_dir = tmp_path / "no_power_supply"
    monitor = BatteryMonitor(sysfs_dir=missing_dir)
    st = monitor.get_status()
    assert st["present"] is True
    assert 0 <= st["capacity_percent"] <= 100
    assert isinstance(st["status"], str)
    assert isinstance(st["ac_online"], bool)


# ============================================================================
# ALSA / Audio Controller Tests
# ============================================================================

def test_audio_controller_fallback_in_memory():
    """Test audio controller in-memory state preservation without amixer."""
    audio = AudioController(amixer_bin="/nonexistent/amixer")
    vol = audio.get_volume()
    assert vol["volume_percent"] == 75
    assert vol["muted"] is False

    res = audio.set_volume(85, muted=True)
    assert res["volume_percent"] == 85
    assert res["muted"] is True
    assert res["success"] is True

    vol_updated = audio.get_volume()
    assert vol_updated["volume_percent"] == 85
    assert vol_updated["muted"] is True


def test_audio_controller_amixer_parsing(monkeypatch):
    """Test parsing ALSA amixer command output for volume and mute state."""
    audio = AudioController(amixer_bin="/usr/bin/amixer")

    mock_stdout = (
        "Simple mixer control 'Master',0\n"
        "  Capabilities: pvolume pswitch\n"
        "  Playback channels: Front Left - Front Right\n"
        "  Limits: Playback 0 - 64\n"
        "  Front Left: Playback 48 [75%] [-16.00dB] [on]\n"
        "  Front Right: Playback 48 [75%] [-16.00dB] [on]\n"
    )

    def mock_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        res.stdout = mock_stdout
        return res

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    vol = audio.get_volume()
    assert vol["volume_percent"] == 75
    assert vol["muted"] is False


def test_audio_controller_amixer_mute_parsing(monkeypatch):
    """Test parsing ALSA amixer output when muted [off]."""
    audio = AudioController(amixer_bin="/usr/bin/amixer")

    mock_stdout = (
        "Simple mixer control 'Master',0\n"
        "  Front Left: Playback 0 [0%] [-64.00dB] [off]\n"
        "  Front Right: Playback 0 [0%] [-64.00dB] [off]\n"
    )

    def mock_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        res.stdout = mock_stdout
        return res

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    vol = audio.get_volume()
    assert vol["volume_percent"] == 0
    assert vol["muted"] is True


# ============================================================================
# Network Hardware Monitor Tests
# ============================================================================

def test_network_hardware_monitor_with_sysfs(tmp_path):
    """Test network hardware monitor extracts wifi and ethernet interface info."""
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

    monitor = NetworkHardwareMonitor(sysfs_dir=tmp_path)
    status = monitor.get_status()

    assert "wifi" in status
    assert status["wifi"]["interface"] == "wlan0"
    assert status["wifi"]["connected"] is True
    assert status["wifi"]["driver"] == "ath9k"

    assert "ethernet" in status
    assert status["ethernet"]["interface"] == "eth0"
    assert status["ethernet"]["carrier"] is False
    assert status["ethernet"]["speed_mbps"] == 1000
    assert status["ethernet"]["driver"] == "r8169"


# ============================================================================
# Sony VAIO VPCYB35AN Hardware Inventory Tests
# ============================================================================

def test_vaio_inventory_structure():
    """Test Sony VAIO hardware inventory returns all required subsystems."""
    inv = get_hardware_inventory()
    assert "system" in inv
    assert "cpu" in inv
    assert "gpu" in inv
    assert "network" in inv
    assert "audio" in inv
    assert "battery" in inv
    assert "brightness" in inv


def test_vaio_inventory_target_specifications():
    """Test inventory reports Sony VAIO VPCYB35AN target specifications."""
    inv = get_hardware_inventory()

    # System & Platform
    assert inv["system"]["manufacturer"] == "Sony Corporation"
    assert "VPCYB" in inv["system"]["model_series"]
    assert inv["system"]["tier"] == "minimum"

    # CPU: AMD Dual-Core E-450 APU
    assert "AMD E-450" in inv["cpu"]["name"]
    assert inv["cpu"]["cores"] == 2
    assert inv["cpu"]["threads"] == 2
    assert inv["cpu"]["nominal_frequency_ghz"] == 1.65

    # GPU: AMD Radeon HD 6320
    assert inv["gpu"]["name"] == "AMD Radeon HD 6320 Graphics"
    assert inv["gpu"]["vendor_id"] == "0x1002"
    assert inv["gpu"]["driver"] == "radeon"
    assert inv["gpu"]["mesa_driver"] == "r600"
    assert inv["gpu"]["vaapi_supported"] is True
    assert inv["gpu"]["vdpau_supported"] is True

    # Audio: Realtek ALC269
    assert inv["audio"]["codec"] == "Realtek ALC269"
    assert inv["audio"]["driver"] == "snd_hda_codec_realtek"

    # Network: Atheros AR9285 & Realtek RTL8111/8168
    assert inv["network"]["wifi"]["driver"] == "ath9k"
    assert "Atheros AR9285" in inv["network"]["wifi"]["chipset"]
    assert inv["network"]["ethernet"]["driver"] == "r8169"
    assert "Realtek" in inv["network"]["ethernet"]["chipset"]


# ============================================================================
# Web API Endpoints Integration Tests
# ============================================================================

def test_web_api_brightness_endpoints():
    """Test GET and POST /api/system/brightness endpoints."""
    client = TestClient(app)

    # GET brightness
    get_res = client.get("/api/system/brightness")
    assert get_res.status_code == 200
    data = get_res.json()
    assert "brightness_percent" in data
    assert "device" in data
    assert 0 <= data["brightness_percent"] <= 100

    # POST brightness
    post_res = client.post("/api/system/brightness", json={"brightness_percent": 82})
    assert post_res.status_code == 200
    post_data = post_res.json()
    assert post_data["brightness_percent"] == 82
    assert post_data["success"] is True

    # Verify GET reflects update
    verify_res = client.get("/api/system/brightness")
    assert verify_res.json()["brightness_percent"] == 82


def test_web_api_volume_endpoints():
    """Test GET and POST /api/system/volume endpoints."""
    client = TestClient(app)

    # GET volume
    get_res = client.get("/api/system/volume")
    assert get_res.status_code == 200
    data = get_res.json()
    assert "volume_percent" in data
    assert "muted" in data
    assert isinstance(data["muted"], bool)

    # POST volume
    post_res = client.post("/api/system/volume", json={"volume_percent": 65, "muted": True})
    assert post_res.status_code == 200
    post_data = post_res.json()
    assert post_data["volume_percent"] == 65
    assert post_data["muted"] is True
    assert post_data["success"] is True

    # Verify GET reflects update
    verify_res = client.get("/api/system/volume")
    assert verify_res.json()["volume_percent"] == 65
    assert verify_res.json()["muted"] is True


def test_web_api_hardware_inventory_endpoint():
    """Test GET /api/system/hardware returns complete inventory."""
    client = TestClient(app)
    res = client.get("/api/system/hardware")
    assert res.status_code == 200
    inv = res.json()
    assert inv["system"]["manufacturer"] == "Sony Corporation"
    assert "AMD E-450" in inv["cpu"]["name"]
    assert inv["gpu"]["driver"] == "radeon"
    assert inv["audio"]["codec"] == "Realtek ALC269"
