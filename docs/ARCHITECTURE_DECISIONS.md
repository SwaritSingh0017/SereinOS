# SereinOS — Architecture Decision Records (ADRs)

**Document Version:** 1.0  
**Status:** Approved Architectural Decisions  
**Scope:** Definitive architectural resolutions, rationales, trade-off analyses, and rejected alternatives.

---

## ADR-001: Retirement of Monolithic Prototype (`src/`) in Favor of `serein-core`

### Status
**APPROVED**

### Context
The repository currently maintains two parallel codebases:
1. `src/`: A monolithic, synchronous prototype with hardcoded paths (`data/database/serein.db`), blocking `sqlite3.connect()`, and no standard packaging.
2. `serein-core/`: A modern, modular Python package with pyproject.toml, async event bus, FastAPI web layer, hardware tier classification, and automated SQLite migrations.

### Decision
`serein-core` is the sole canonical implementation. The legacy `src/` directory, duplicate `serein_installer/`, and loose root scripts (`main.py`, `check_db.py`, `update_schema.py`, `SereinOS_cleanup.ps1`) are slated for retirement. All active persona definitions and prompts are consolidated into `serein-core`.

### Rationale
- Eliminates dual-maintenance overhead and divergence.
- Eliminates circular import hazards and broken relative imports.
- Establishes a clean, pip-installable package structure.

### Rejected Alternatives
- *Maintaining `src/` as a backward-compatibility layer:* Rejected because it confuses developers, causes conflicting test suites, and doubles code review surface.

---

## ADR-002: Selection of `labwc` as the Canonical Wayland Compositor

### Status
**APPROVED**

### Context
SereinOS requires a lightweight, rock-solid Wayland display server capable of running on low-spec hardware (AMD Radeon HD 6320 integrated GPU on the Sony VAIO) with support for kiosk mode, layer-shell overlays, and input management.

### Decision
**`labwc` (Lab Wayland Compositor)** is selected as the authoritative Wayland compositor for SereinOS.

### Rationale
- **Lightweight & High-Performance:** `labwc` is built on `wlroots` and implements Openbox-like configuration simplicity with minimal CPU/RAM overhead (< 30 MB idle RAM).
- **Native Wayland Standards:** Implements `wlr-layer-shell-unstable-v1`, `xdg-shell`, fractional scaling, and foreign-toplevel protocols out of the box.
- **Hardware Compatibility:** Runs flawlessly on generic Linux DRM/KMS drivers without requiring Vulkan, making it fully compatible with the TeraScale 2 architecture of the Radeon HD 6320.
- **Maintenance Sustainability:** Maintained by an active upstream community; eliminates the burden of writing and debugging a custom C compositor from scratch.

### Rejected Alternatives
- *Custom C wlroots Compositor (`serein-compositor/`):* Rejected due to immense long-term maintenance overhead, potential C memory corruption bugs, and duplicated effort.
- *Weston:* Rejected because Weston lacks rich layer-shell protocol extensions and flexible multi-window stacking required for companion desktop overlays.
- *Sway:* Rejected because tiling window management is poorly suited for a friendly consumer companion operating system.

---

## ADR-003: Systemd User Units (`systemd --user`) vs. System Units

### Status
**APPROVED**

### Context
SereinOS must decide whether its services (`serein-core`, `serein-compositor`, `serein-shell`) run as global system units (`/etc/systemd/system/`) or user session units (`/usr/lib/systemd/user/` or `~/.config/systemd/user/`).

### Decision
All core SereinOS desktop services run strictly as **systemd user units** under `serein.target`.

### Rationale
1. **User Session Context:** Serein is an interactive companion desktop session. Wayland compositors require user seat privileges, graphics render nodes (`/dev/dri/renderD128`), input devices (`/dev/input/`), and `XDG_RUNTIME_DIR`.
2. **Audio Daemon Integration:** Modern Linux audio (PipeWire and WirePlumber) runs exclusively in the user session. A system-level unit cannot cleanly bind to a user's PipeWire audio graph without complex IPC hacks.
3. **Least Privilege & Security:** Running under the `serein` user account ensures the AI daemon has zero root privileges. Compromising the AI daemon does not yield root access.

### Rejected Alternatives
- *System-level systemd units running as root:* Rejected due to severe security risks (compromise yields total root takeover) and incompatibility with PipeWire user sockets.

---

## ADR-004: In-Process Threaded Voice Pipeline vs. Independent Daemon

### Status
**APPROVED**

### Context
Voice interaction requires low-latency audio capture, Voice Activity Detection (VAD), wake-word detection, and TTS audio synthesis with viseme extraction.

### Decision
The voice pipeline runs as a **dedicated background worker thread inside `serein-core`**, communicating via internal queues and the async EventBus.

### Rationale
- **Zero-Copy Performance:** Audio chunks, PCM waveforms, and 60fps viseme blendshape frames are passed directly in memory between the audio worker and the performance engine. On an AMD E-450, inter-process serialization over UNIX sockets or pipes causes buffer underruns and audio stuttering.
- **Single Process Simplicity:** Avoids managing process lifecycles and race conditions between separate voice and core daemons.
- **Fault Isolation:** Audio driver errors or device unplugs are caught within the voice worker loop and do not crash the core daemon.

### Rejected Alternatives
- *Standalone `serein-voice` IPC daemon:* Rejected due to unnecessary socket latency, IPC serialization overhead, and CPU load on low-spec hardware.

---

## 5. ADR-005: 100% Offline Rule-Based Heuristic Local Brain for Sony VAIO E-450

### Status
**APPROVED**

### Context
The Sony VAIO target features an AMD E-450 processor (dual-core 1.65 GHz, no AVX) and an integrated Radeon HD 6320 GPU. Running modern neural LLMs (even 1B-3B quantized models) on this processor yields unbearable latency (20-60 seconds per response) and thermal throttling.

### Decision
On the `minimum` hardware tier, SereinOS relies on a **100% offline, deterministic rule-based companion brain** (`LocalBrainEngine` in `local_brain.py`).

### Rationale
- **Instantaneous Latency:** Heuristic matching, regex intent parsing, slang normalization, and template generation complete in < 5ms.
- **Low Memory & Zero AVX:** Consumes < 40 MB RAM and executes standard x86_64 instructions without AVX.
- **Full Offline Companion Capability:** Serein can answer questions about time, system health, battery, volume, reminders, identity, and personal banter reliably without internet access.
- **Graceful Upgrade:** Systems with GPUs (HP Victus RTX 3050) automatically upgrade to local neural GGUF models.

### Rejected Alternatives
- *Forcing a 1B GGUF model via llama.cpp on AMD E-450:* Rejected because token generation speeds of 0.8 tokens/second create an unacceptable user experience.
- *Mandatory Cloud API:* Rejected because it violates the fundamental local-first, offline-capable vision of SereinOS.

---

## ADR-006: Server-Tracked Single-Use Confirmation Challenges vs. Stateless HMAC

### Status
**APPROVED**

### Context
Destructive actions require user confirmation. A stateless HMAC token alone does not prevent replay attacks or race conditions if an attacker intercepts the token.

### Decision
Implement **server-tracked, single-use, session-bound confirmation challenges** stored in SQLite and memory.

### Rationale
- **Replay Protection:** Challenges are marked `consumed` in an atomic database transaction. Any subsequent attempt with the same challenge ID is rejected immediately.
- **Parameter Binding:** The challenge stores `SHA256(canonical_json(params))`. If parameters are modified between prompt and execution, verification fails.
- **Strict Expiration:** Challenges expire unconditionally after 300 seconds.
- **Session Scoping:** Challenges are bound to the specific authenticated UI session that requested them.

### Rejected Alternatives
- *Stateless HMAC tokens:* Rejected because stateless tokens can be replayed repeatedly within their validity window.

---

## ADR-007: Freedesktop Trash Redirection for File Deletions

### Status
**APPROVED**

### Context
`command_bus.py` previously executed `shutil.rmtree()` and `Path.unlink()` directly on user files, creating an irreversible data loss vulnerability if triggered accidentally or maliciously.

### Decision
All file deletions in user document directories (`~/Downloads`, `~/Documents`, `~/Desktop`, etc.) must **move files to the Freedesktop Trash** (`~/.local/share/Trash`) via `gio trash` or `send2trash`.

### Rationale
- **100% Reversibility:** Accidental deletions can be restored from the desktop Trash folder.
- **Safe Companion Behavior:** A helpful companion should never permanently erase user memories or files without a secondary, explicit "Purge Trash" action.
- **POSIX Safety:** Standardizes file management across Linux desktop environments.

### Rejected Alternatives
- *Immediate unlinking (`unlink`/`rmtree`):* Rejected due to extreme data loss risks.

---

## ADR-008: Socket-Level IP Pinning & CIDR Validation for SSRF / DNS Rebinding

### Status
**APPROVED**

### Context
`network_request` in `command_bus.py` allowed arbitrary HTTP requests, creating a Server-Side Request Forgery vulnerability. Furthermore, basic hostname checking is vulnerable to DNS Rebinding attacks.

### Decision
Enforce **DNS resolution with socket-level IP pinning and CIDR block checking**.

### Rationale
- The destination domain is resolved once. The resulting IP is validated against private/loopback/link-local CIDR blocks (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`).
- If valid, the connection is made directly to the **pinned IP address**, passing the domain name in the HTTP `Host` header and TLS SNI.
- Prevents attackers from changing DNS records between validation and socket connection (DNS rebinding).

### Rejected Alternatives
- *Regex matching on URLs:* Rejected because domain strings can obscure target IPs, and DNS records can be mutated dynamically.

---

## ADR-009: Strict Application Whitelisting for Process Spawning

### Status
**APPROVED**

### Context
`command_bus.py:163` passed arbitrary application strings into `asyncio.create_subprocess_exec(app, *args)`, allowing arbitrary binary execution.

### Decision
`launch_app` permits launching only applications defined in a strict, pre-configured **Application Whitelist** mapped to verified `.desktop` binaries.

### Rationale
- Prevents command injection and execution of system binaries (`rm`, `dd`, `nc`, `curl`).
- Standardizes desktop application launching to verified desktop apps (`foot`, `chromium`, `thunar`).

---

## ADR-010: Removal of Heavy PyTorch / Transformer Dependencies from Base Package

### Status
**APPROVED**

### Context
`serein-core/pyproject.toml` declared mandatory dependencies on `torch>=2.1.0`, `transformers>=4.36.0`, `sentence-transformers>=2.2.2`, `accelerate>=0.25.0`, and `easyocr>=1.7.1`. Importing PyTorch on the Sony VAIO AMD E-450 crashes immediately with `Illegal instruction` due to missing AVX.

### Decision
Move all heavy ML packages into `[project.optional-dependencies]` (e.g. `serein-core[ai-power]`, `serein-core[vision]`). The base `serein-core` package requires only standard Python libraries, `pydantic`, `fastapi`, `uvicorn`, `aiohttp`, `psutil`, `croniter`, and `sqlcipher3`.

### Rationale
- Enables clean installation and error-free execution on the Sony VAIO AMD E-450.
- Keeps base installation size under 200 MB rather than > 4 GB of PyTorch wheels.
