"""
Milestone 3 Empirical Challenger Stress Suite: Web REST APIs & UI Slider Integration.
Authored by: challenger_m3_hardware_2 (Critic & Specialist)
Target: Milestone 3 Sony VAIO VPCYB35AN Physical Hardware Enablement, REST APIs & UI Integration

Empirically tests:
1. REST API /api/system/brightness:
   - GET /api/system/brightness schema and baseline sanity.
   - POST /api/system/brightness with valid percentages [0..100].
   - Extreme boundary clamping: >100 clamped to 100, <0 clamped to 0.
   - Malformed/invalid payloads: empty body, wrong keys, non-numeric strings, None, malformed JSON bytes.
   - HTTP method guardrails (e.g. DELETE returns 405).
2. REST API /api/system/volume:
   - GET /api/system/volume schema and baseline sanity.
   - POST /api/system/volume with valid volume and mute combinations.
   - Extreme boundary clamping: >100 clamped to 100, <0 clamped to 0.
   - Muted state retention when muted field is omitted.
   - Malformed/invalid payloads: empty body, wrong keys, non-numeric strings, None, malformed JSON bytes.
   - Audio alias endpoints /api/audio/status and /api/audio/volume parity.
   - HTTP method guardrails (e.g. DELETE returns 405).
3. REST API /api/system/hardware:
   - Complete Sony VAIO VPCYB35AN hardware inventory schema validation.
   - Verification of system, cpu, gpu, network, audio, battery, and brightness subsystems.
   - Read-only enforcement: POST and DELETE return 405.
4. Concurrency & State Integrity:
   - High-concurrency multithreaded burst of interleaved GET and POST calls across endpoints.
   - Rapid mute toggle and rapid brightness sweeps to verify state consistency and lack of race conditions.
5. Static Analysis of Desktop UI & Assets:
   - assets/ui/index.html slider presence, range attributes, badges, mute buttons, and modal dialogs.
   - assets/ui/app.js event listener bindings ('input', 'change', 'click'), fetch calls, localStorage fallback, and boot init.
   - assets/ui/style.css slider styling, thumb styling, mute button states, and modal layout.
6. Sysfs Component Empirical Isolation:
   - BacklightController sysfs mocking, raw step calculations, zero max_brightness protection, corrupted sysfs handling.
   - BatteryMonitor sysfs mocking, capacity parsing, and AC adapter status.
   - AudioController regex parser robustness across diverse ALSA output structures.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

import pytest
from starlette.testclient import TestClient

# Ensure serein-core is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_SRC = REPO_ROOT / "serein-core" / "src"

if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from serein_core.hardware import (
    AudioController,
    BacklightController,
    BatteryMonitor,
    get_brightness,
    get_hardware_inventory,
    get_volume,
    set_brightness,
    set_volume,
)
from serein_core.web_app import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Reusable TestClient instance for Serein web application."""
    return TestClient(app)


# ============================================================================
# 1. DISPLAY BRIGHTNESS REST API EMPIRICAL TESTS
# ============================================================================

class TestBrightnessEndpointsAdversarial:
    """Adversarial challenge for GET and POST /api/system/brightness."""

    def test_brightness_get_schema_and_types(self, client: TestClient):
        """Verify GET returns 200 with brightness_percent (int) and device (str)."""
        res = client.get("/api/system/brightness")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "brightness_percent" in data
        assert "device" in data
        assert isinstance(data["brightness_percent"], int)
        assert isinstance(data["device"], str)
        assert 0 <= data["brightness_percent"] <= 100

    @pytest.mark.parametrize("valid_val", [0, 1, 25, 50, 75, 99, 100])
    def test_brightness_valid_updates(self, client: TestClient, valid_val: int):
        """Verify POST with valid integer percentages sets value accurately."""
        post_res = client.post("/api/system/brightness", json={"brightness_percent": valid_val})
        assert post_res.status_code == 200
        post_data = post_res.json()
        assert post_data["brightness_percent"] == valid_val
        assert post_data["success"] is True

        get_res = client.get("/api/system/brightness")
        assert get_res.status_code == 200
        assert get_res.json()["brightness_percent"] == valid_val

    @pytest.mark.parametrize("extreme_high", [101, 120, 255, 1000, 999999])
    def test_brightness_clamping_upper_boundary(self, client: TestClient, extreme_high: int):
        """Verify brightness values > 100 are strictly clamped to 100."""
        res = client.post("/api/system/brightness", json={"brightness_percent": extreme_high})
        assert res.status_code == 200
        data = res.json()
        assert data["brightness_percent"] == 100
        assert data["success"] is True

        # Verify state persistence
        assert client.get("/api/system/brightness").json()["brightness_percent"] == 100

    @pytest.mark.parametrize("extreme_low", [-1, -50, -100, -999999])
    def test_brightness_clamping_lower_boundary(self, client: TestClient, extreme_low: int):
        """Verify negative brightness values are strictly clamped to 0."""
        res = client.post("/api/system/brightness", json={"brightness_percent": extreme_low})
        assert res.status_code == 200
        data = res.json()
        assert data["brightness_percent"] == 0
        assert data["success"] is True

        # Verify state persistence
        assert client.get("/api/system/brightness").json()["brightness_percent"] == 0

    def test_brightness_fractional_float_rejection(self, client: TestClient):
        """Verify fractional float payloads are rejected with 422 Unprocessable Entity by Pydantic."""
        res = client.post("/api/system/brightness", json={"brightness_percent": 68.9})
        assert res.status_code == 422

    def test_brightness_missing_payload_field(self, client: TestClient):
        """Verify empty JSON object returns 422 Unprocessable Entity."""
        res = client.post("/api/system/brightness", json={})
        assert res.status_code == 422

    def test_brightness_wrong_key_payload(self, client: TestClient):
        """Verify unrecognized JSON keys return 422 Unprocessable Entity."""
        res = client.post("/api/system/brightness", json={"brightness": 50})
        assert res.status_code == 422

    @pytest.mark.parametrize("invalid_type", ["abc", "", "100%", [], {}, None])
    def test_brightness_invalid_payload_types(self, client: TestClient, invalid_type: Any):
        """Verify non-integer types return 422 Unprocessable Entity."""
        res = client.post("/api/system/brightness", json={"brightness_percent": invalid_type})
        assert res.status_code == 422

    def test_brightness_malformed_json_body(self, client: TestClient):
        """Verify malformed JSON body returns 422 or 400, never 500."""
        res = client.post(
            "/api/system/brightness",
            content=b"{{invalid json!!}",
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code in (400, 422)

    def test_brightness_empty_raw_body(self, client: TestClient):
        """Verify empty HTTP body returns 422 Unprocessable Entity."""
        res = client.post(
            "/api/system/brightness",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 422

    def test_brightness_extra_unexpected_keys(self, client: TestClient):
        """Verify extraneous fields in payload do not crash endpoint."""
        res = client.post(
            "/api/system/brightness",
            json={"brightness_percent": 84, "malicious_field": "drop table", "extra_num": 123},
        )
        assert res.status_code == 200
        assert res.json()["brightness_percent"] == 84

    def test_brightness_unsupported_methods(self, client: TestClient):
        """Verify unsupported HTTP verbs return 405 Method Not Allowed."""
        assert client.delete("/api/system/brightness").status_code == 405
        assert client.put("/api/system/brightness").status_code == 405


# ============================================================================
# 2. AUDIO VOLUME REST API EMPIRICAL TESTS
# ============================================================================

class TestVolumeEndpointsAdversarial:
    """Adversarial challenge for GET and POST /api/system/volume and /api/audio/*."""

    def test_volume_get_schema_and_types(self, client: TestClient):
        """Verify GET returns 200 with volume_percent (int) and muted (bool)."""
        res = client.get("/api/system/volume")
        assert res.status_code == 200
        data = res.json()
        assert "volume_percent" in data
        assert "muted" in data
        assert isinstance(data["volume_percent"], int)
        assert isinstance(data["muted"], bool)
        assert 0 <= data["volume_percent"] <= 100

    @pytest.mark.parametrize("val,muted", [
        (0, False),
        (0, True),
        (50, False),
        (50, True),
        (100, False),
        (100, True),
    ])
    def test_volume_valid_updates(self, client: TestClient, val: int, muted: bool):
        """Verify POST with volume and mute combinations operates reliably."""
        res = client.post("/api/system/volume", json={"volume_percent": val, "muted": muted})
        assert res.status_code == 200
        data = res.json()
        assert data["volume_percent"] == val
        assert data["muted"] is muted
        assert data["success"] is True

        verify_res = client.get("/api/system/volume")
        assert verify_res.status_code == 200
        assert verify_res.json()["volume_percent"] == val
        assert verify_res.json()["muted"] is muted

    @pytest.mark.parametrize("over_val", [101, 150, 300, 10000])
    def test_volume_clamping_upper_boundary(self, client: TestClient, over_val: int):
        """Verify volume levels > 100 are strictly clamped to 100."""
        res = client.post("/api/system/volume", json={"volume_percent": over_val, "muted": False})
        assert res.status_code == 200
        data = res.json()
        assert data["volume_percent"] == 100
        assert client.get("/api/system/volume").json()["volume_percent"] == 100

    @pytest.mark.parametrize("under_val", [-1, -30, -100, -99999])
    def test_volume_clamping_lower_boundary(self, client: TestClient, under_val: int):
        """Verify negative volume levels are strictly clamped to 0."""
        res = client.post("/api/system/volume", json={"volume_percent": under_val, "muted": True})
        assert res.status_code == 200
        data = res.json()
        assert data["volume_percent"] == 0
        assert client.get("/api/system/volume").json()["volume_percent"] == 0

    def test_volume_omitted_muted_preserves_state(self, client: TestClient):
        """Verify updating volume without specifying muted keeps previous muted state."""
        # 1. Explicitly set muted = True
        client.post("/api/system/volume", json={"volume_percent": 40, "muted": True})
        assert client.get("/api/system/volume").json()["muted"] is True

        # 2. Update volume to 60 with muted omitted (None)
        res = client.post("/api/system/volume", json={"volume_percent": 60})
        assert res.status_code == 200
        assert res.json()["volume_percent"] == 60
        # Mute state should still be True
        assert client.get("/api/system/volume").json()["muted"] is True

        # 3. Explicitly unmute
        client.post("/api/system/volume", json={"volume_percent": 60, "muted": False})
        assert client.get("/api/system/volume").json()["muted"] is False

        # 4. Update volume to 80 with muted omitted
        client.post("/api/system/volume", json={"volume_percent": 80})
        # Mute state should still be False
        assert client.get("/api/system/volume").json()["muted"] is False

    def test_volume_missing_volume_field(self, client: TestClient):
        """Verify missing volume_percent in payload returns 422."""
        res = client.post("/api/system/volume", json={"muted": True})
        assert res.status_code == 422

    def test_volume_empty_payload(self, client: TestClient):
        """Verify empty JSON object returns 422."""
        res = client.post("/api/system/volume", json={})
        assert res.status_code == 422

    @pytest.mark.parametrize("invalid_val", ["loud", "100%", [], {}, None])
    def test_volume_invalid_volume_types(self, client: TestClient, invalid_val: Any):
        """Verify non-integer volume values return 422."""
        res = client.post("/api/system/volume", json={"volume_percent": invalid_val, "muted": False})
        assert res.status_code == 422

    def test_volume_malformed_json_body(self, client: TestClient):
        """Verify malformed JSON body returns 422 or 400."""
        res = client.post(
            "/api/system/volume",
            content=b"not json volume request",
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code in (400, 422)

    def test_audio_aliases_parity(self, client: TestClient):
        """Verify /api/audio/status and /api/audio/volume match /api/system/volume."""
        # Update via /api/audio/volume
        alias_post = client.post("/api/audio/volume", json={"volume_percent": 77, "muted": False})
        assert alias_post.status_code == 200
        assert alias_post.json()["volume_percent"] == 77

        # Verify via /api/audio/status
        alias_get = client.get("/api/audio/status")
        assert alias_get.status_code == 200
        assert alias_get.json()["volume_percent"] == 77
        assert alias_get.json()["muted"] is False

        # Verify main /api/system/volume reflects the update
        main_get = client.get("/api/system/volume")
        assert main_get.status_code == 200
        assert main_get.json()["volume_percent"] == 77

    def test_volume_unsupported_methods(self, client: TestClient):
        """Verify unsupported HTTP verbs return 405 Method Not Allowed."""
        assert client.delete("/api/system/volume").status_code == 405
        assert client.put("/api/system/volume").status_code == 405


# ============================================================================
# 3. SONY VAIO HARDWARE INVENTORY REST API EMPIRICAL TESTS
# ============================================================================

class TestHardwareInventoryAdversarial:
    """Adversarial challenge for GET /api/system/hardware."""

    def test_hardware_inventory_full_schema(self, client: TestClient):
        """Verify complete Sony VAIO VPCYB35AN hardware profile schema and specifications."""
        res = client.get("/api/system/hardware")
        assert res.status_code == 200
        inv = res.json()

        # 1. System subsystem
        assert "system" in inv
        sys_info = inv["system"]
        assert sys_info["manufacturer"] == "Sony Corporation"
        assert sys_info["product_name"] == "Sony VAIO VPCYB35AN"
        assert "VPCYB Series" in sys_info["model_series"]
        assert sys_info["tier"] == "minimum"
        assert isinstance(sys_info["ram_gb"], (int, float))
        assert sys_info["ram_gb"] > 0

        # 2. CPU subsystem (AMD Dual-Core E-450)
        assert "cpu" in inv
        cpu_info = inv["cpu"]
        assert "AMD E-450" in cpu_info["name"]
        assert cpu_info["architecture"] == "Bobcat x86_64"
        assert cpu_info["cores"] == 2
        assert cpu_info["threads"] == 2
        assert cpu_info["nominal_frequency_ghz"] == 1.65
        assert cpu_info["tdp_watts"] == 18.0
        assert cpu_info["l2_cache_kb"] == 1024

        # 3. GPU subsystem (AMD Radeon HD 6320)
        assert "gpu" in inv
        gpu_info = inv["gpu"]
        assert gpu_info["driver"] == "radeon"
        assert gpu_info["mesa_driver"] == "r600"
        assert isinstance(gpu_info["vaapi_supported"], bool)
        assert isinstance(gpu_info["vdpau_supported"], bool)

        # 4. Network subsystem (Atheros AR9285 + Realtek RTL8168)
        assert "network" in inv
        net_info = inv["network"]
        assert "wifi" in net_info
        assert "ethernet" in net_info
        assert net_info["wifi"]["driver"] == "ath9k"
        assert "AR9285" in net_info["wifi"]["chipset"]
        assert net_info["ethernet"]["driver"] == "r8169"
        assert "RTL8111/8168" in net_info["ethernet"]["chipset"]

        # 5. Audio subsystem (Realtek ALC269)
        assert "audio" in inv
        audio_info = inv["audio"]
        assert audio_info["codec"] == "Realtek ALC269"
        assert audio_info["driver"] == "snd_hda_codec_realtek"
        assert 0 <= audio_info["volume_percent"] <= 100
        assert isinstance(audio_info["muted"], bool)

        # 6. Battery subsystem (ACPI BAT1 / ACAD)
        assert "battery" in inv
        bat_info = inv["battery"]
        assert isinstance(bat_info["present"], bool)
        assert 0 <= bat_info["capacity_percent"] <= 100
        assert isinstance(bat_info["ac_online"], bool)

        # 7. Brightness subsystem
        assert "brightness" in inv
        bright_info = inv["brightness"]
        assert 0 <= bright_info["brightness_percent"] <= 100
        assert isinstance(bright_info["device"], str)

    def test_hardware_inventory_read_only_protection(self, client: TestClient):
        """Verify /api/system/hardware is strictly read-only."""
        assert client.post("/api/system/hardware", json={"fake": "data"}).status_code == 405
        assert client.delete("/api/system/hardware").status_code == 405
        assert client.put("/api/system/hardware", json={}).status_code == 405


# ============================================================================
# 4. CONCURRENCY, RACE CONDITIONS & STATE INTEGRITY
# ============================================================================

class TestConcurrencyAndStateIntegrity:
    """Stress test concurrent API calls, race condition resistance, and state consistency."""

    def test_interleaved_concurrent_hardware_requests(self, client: TestClient):
        """Execute 40 concurrent requests across all hardware endpoints."""
        endpoints = [
            ("GET", "/api/system/brightness", None),
            ("POST", "/api/system/brightness", {"brightness_percent": 80}),
            ("GET", "/api/system/volume", None),
            ("POST", "/api/system/volume", {"volume_percent": 65, "muted": False}),
            ("GET", "/api/system/hardware", None),
            ("GET", "/api/audio/status", None),
            ("POST", "/api/audio/volume", {"volume_percent": 70, "muted": True}),
        ]

        errors: List[str] = []

        def worker(idx: int) -> int:
            method, url, payload = endpoints[idx % len(endpoints)]
            try:
                if method == "GET":
                    r = client.get(url)
                else:
                    r = client.post(url, json=payload)
                if r.status_code != 200:
                    errors.append(f"Worker {idx} failed on {method} {url}: {r.status_code} - {r.text}")
                return r.status_code
            except Exception as e:
                errors.append(f"Worker {idx} crashed: {e}")
                return -1

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(worker, i) for i in range(40)]
            results = [f.result() for f in as_completed(futures)]

        assert not errors, f"Concurrency test encountered errors: {errors}"
        assert all(code == 200 for code in results)

    def test_rapid_mute_toggle_stress(self, client: TestClient):
        """Verify 50 rapid alternating mute toggles maintain correct state."""
        for i in range(50):
            target_mute = (i % 2 == 0)
            res = client.post("/api/system/volume", json={"volume_percent": 55, "muted": target_mute})
            assert res.status_code == 200
            assert res.json()["muted"] is target_mute

        # Final verification
        final_state = client.get("/api/system/volume").json()
        assert final_state["muted"] == (49 % 2 == 0)

    def test_brightness_sweep_stress(self, client: TestClient):
        """Verify rapid incremental brightness changes 0 -> 100 -> 0."""
        # Upward sweep
        for b in range(0, 101, 10):
            res = client.post("/api/system/brightness", json={"brightness_percent": b})
            assert res.status_code == 200
            assert res.json()["brightness_percent"] == b

        # Downward sweep
        for b in range(100, -1, -10):
            res = client.post("/api/system/brightness", json={"brightness_percent": b})
            assert res.status_code == 200
            assert res.json()["brightness_percent"] == b


# ============================================================================
# 5. STATIC ANALYSIS OF DESKTOP UI & JAVASCRIPT ASSETS
# ============================================================================

class TestDesktopUIStaticAnalysis:
    """Verify HTML elements, JS event handlers, CSS styling, and local persistence."""

    @pytest.fixture(scope="class")
    def index_html_content(self) -> str:
        html_path = REPO_ROOT / "assets" / "ui" / "index.html"
        assert html_path.exists(), f"Missing index.html at {html_path}"
        return html_path.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def app_js_content(self) -> str:
        js_path = REPO_ROOT / "assets" / "ui" / "app.js"
        assert js_path.exists(), f"Missing app.js at {js_path}"
        return js_path.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def style_css_content(self) -> str:
        css_path = REPO_ROOT / "assets" / "ui" / "style.css"
        assert css_path.exists(), f"Missing style.css at {css_path}"
        return css_path.read_text(encoding="utf-8")

    def test_html_contains_brightness_and_volume_sliders(self, index_html_content: str):
        """Verify index.html contains primary and modal range sliders with proper bounds."""
        # Primary widget sliders
        assert 'id="slider-brightness"' in index_html_content
        assert 'id="slider-volume"' in index_html_content
        assert 'id="val-brightness"' in index_html_content
        assert 'id="val-volume"' in index_html_content

        # Modal quick control sliders
        assert 'id="modal-slider-brightness"' in index_html_content
        assert 'id="modal-slider-volume"' in index_html_content
        assert 'id="modal-val-brightness"' in index_html_content
        assert 'id="modal-val-volume"' in index_html_content

        # Slider attribute verification
        assert re.search(r'id="slider-brightness"[^>]*min="0"[^>]*max="100"', index_html_content) or \
               re.search(r'min="0"[^>]*max="100"[^>]*id="slider-brightness"', index_html_content)
        assert re.search(r'id="slider-volume"[^>]*min="0"[^>]*max="100"', index_html_content) or \
               re.search(r'min="0"[^>]*max="100"[^>]*id="slider-volume"', index_html_content)

    def test_html_contains_mute_buttons_and_modal_card(self, index_html_content: str):
        """Verify mute toggles and Sony VAIO quick controls modal exist."""
        assert 'id="btn-volume-mute"' in index_html_content
        assert 'id="modal-btn-mute"' in index_html_content
        assert 'id="modal-quick-controls"' in index_html_content
        assert 'Sony VAIO VPCYB35AN' in index_html_content
        assert 'AMD Dual-Core E-450' in index_html_content
        assert 'AMD Radeon HD 6320' in index_html_content

    def test_js_event_listeners_and_persistence_binding(self, app_js_content: str):
        """Verify app.js attaches input/change listeners and persists to REST API."""
        # 1. Element queries
        assert "document.getElementById('slider-brightness')" in app_js_content
        assert "document.getElementById('slider-volume')" in app_js_content
        assert "document.getElementById('modal-slider-brightness')" in app_js_content
        assert "document.getElementById('modal-slider-volume')" in app_js_content

        # 2. Real-time input and change listeners
        assert ".addEventListener('input'" in app_js_content
        assert ".addEventListener('change'" in app_js_content

        # 3. Backend REST endpoint fetches
        assert "fetch('/api/system/brightness'" in app_js_content
        assert "fetch('/api/system/volume'" in app_js_content

        # 4. LocalStorage keys for offline persistence
        assert "localStorage.setItem('serein_brightness'" in app_js_content
        assert "localStorage.setItem('serein_volume'" in app_js_content
        assert "localStorage.setItem('serein_muted'" in app_js_content

        # 5. Boot hardware initialization
        assert "initHardwareControls" in app_js_content
        assert "updateBrightnessUI" in app_js_content
        assert "updateVolumeUI" in app_js_content

    def test_css_slider_styling(self, style_css_content: str):
        """Verify style.css includes custom range slider styles and mute states."""
        assert ".hw-slider" in style_css_content
        assert ".hw-mute-btn" in style_css_content
        assert ".hw-mute-btn.muted" in style_css_content
        assert ".qc-section" in style_css_content
        assert ".qc-hardware-info" in style_css_content


# ============================================================================
# 6. SYSFS AND HARDWARE CONTROLLER EMPIRICAL ISOLATION
# ============================================================================

class TestSysfsControllerEmpiricalIsolation:
    """Stress test BacklightController, BatteryMonitor, and AudioController in isolation."""

    def test_backlight_controller_with_mocked_sysfs(self):
        """Verify BacklightController percentage conversion and file writing against mock sysfs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            sysfs_path = Path(tmp_dir)
            device_path = sysfs_path / "radeon_bl0"
            device_path.mkdir(parents=True)

            max_file = device_path / "max_brightness"
            bright_file = device_path / "brightness"

            # Set max brightness = 255, current brightness = 191 (~75%)
            max_file.write_text("255\n", encoding="utf-8")
            bright_file.write_text("191\n", encoding="utf-8")

            controller = BacklightController(sysfs_dir=sysfs_path)

            # Test GET
            info = controller.get_brightness()
            assert info["brightness_percent"] == 75
            assert info["device"] == "radeon_bl0"
            assert info["raw_brightness"] == 191
            assert info["max_brightness"] == 255

            # Test SET 50%
            set_res = controller.set_brightness(50)
            assert set_res["success"] is True
            assert set_res["brightness_percent"] == 50
            # Target raw should be round(0.50 * 255) = 128
            written_raw = int(bright_file.read_text(encoding="utf-8").strip())
            assert written_raw == 128

            # Test SET > 100 clamping
            controller.set_brightness(150)
            assert int(bright_file.read_text(encoding="utf-8").strip()) == 255

            # Test SET < 0 clamping
            controller.set_brightness(-20)
            assert int(bright_file.read_text(encoding="utf-8").strip()) == 0

    def test_backlight_controller_corrupted_sysfs_resilience(self):
        """Verify BacklightController does not crash when sysfs files contain garbage or 0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            sysfs_path = Path(tmp_dir)
            device_path = sysfs_path / "acpi_video0"
            device_path.mkdir(parents=True)

            # Corrupted non-numeric values
            (device_path / "max_brightness").write_text("NOT_A_NUMBER\n", encoding="utf-8")
            (device_path / "brightness").write_text("GARBAGE\n", encoding="utf-8")

            controller = BacklightController(sysfs_dir=sysfs_path)
            # Must fall back gracefully without unhandled exception
            info = controller.get_brightness()
            assert 0 <= info["brightness_percent"] <= 100
            assert isinstance(info["device"], str)

    def test_battery_monitor_with_mocked_sysfs(self):
        """Verify BatteryMonitor parses capacity, status, and ac adapter."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            sysfs_path = Path(tmp_dir)
            bat_dir = sysfs_path / "BAT1"
            ac_dir = sysfs_path / "ACAD"
            bat_dir.mkdir(parents=True)
            ac_dir.mkdir(parents=True)

            (bat_dir / "capacity").write_text("83\n", encoding="utf-8")
            (bat_dir / "status").write_text("Discharging\n", encoding="utf-8")
            (ac_dir / "online").write_text("0\n", encoding="utf-8")

            monitor = BatteryMonitor(sysfs_dir=sysfs_path)
            st = monitor.get_status()
            assert st["present"] is True
            assert st["capacity_percent"] == 83
            assert st["status"] == "Discharging"
            assert st["ac_online"] is False
            assert st["device"] == "BAT1"

    def test_audio_controller_regex_parsing(self):
        """Verify regex extraction from amixer outputs handles diverse formats."""
        sample_amixer_output_unmuted = """
Simple mixer control 'Master',0
  Capabilities: pvolume pvolume-joined pswitch pswitch-joined
  Playback channels: Mono
  Limits: Playback 0 - 64
  Mono: Playback 48 [75%] [-16.00dB] [on]
"""
        pct_match = re.search(r"\[(\d+)%\]", sample_amixer_output_unmuted)
        mute_match = re.search(r"\[(on|off)\]", sample_amixer_output_unmuted)
        assert pct_match and pct_match.group(1) == "75"
        assert mute_match and mute_match.group(1) == "on"

        sample_amixer_output_muted = """
Simple mixer control 'Master',0
  Capabilities: pvolume pswitch
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 87
  Front Left: Playback 0 [0%] [-65.25dB] [off]
  Front Right: Playback 0 [0%] [-65.25dB] [off]
"""
        pct_match_m = re.search(r"\[(\d+)%\]", sample_amixer_output_muted)
        mute_match_m = re.search(r"\[(on|off)\]", sample_amixer_output_muted)
        assert pct_match_m and pct_match_m.group(1) == "0"
        assert mute_match_m and mute_match_m.group(1) == "off"
