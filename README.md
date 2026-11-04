# SereinOS - A Persistent AI Companion Operating System

> "Serein is the product. SereinOS is her body. The AI model is her brain. Memory is her experience. Relationship is her bond. Growth is her evolution. Emotion is her dynamic state. The Command Bus and Agent system are her hands. The Scheduler is the part of her that doesn't forget, even in silence. Voice and the 3D avatar are her senses and her presence. The user is the reason she exists."

## Overview

SereinOS is a **full operating system** designed around a persistent AI companion named **Serein**. Unlike traditional OSes that place applications at the center, SereinOS places Serein at the center — the user interacts with Serein, and Serein decides how tasks are completed.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        BOOT FLOW                                │
├─────────────────────────────────────────────────────────────────┤
│  Power On → systemd (no GDM) → Auto-login → Wayland Compositor  │
│                           → Serein Avatar (fullscreen)          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SPLIT-PROCESS ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────┐     ┌─────────────────────────┐   │
│  │   CORE SERVICE          │     │   AVATAR PROCESS        │   │
│  │   (Always-on, systemd)  │     │   (Unity + UniVRM)      │   │
│  │                         │◄────│                         │   │
│  │  • Command Bus (D-Bus)  │  IPC │  • VRM Rendering      │   │
│  │  • Scheduler            │     │  • Lip-sync (Visemes)   │   │
│  │  • AI Brain Router      │     │  • Expressions          │   │
│  │  • Memory/Emotion/Rel   │     │  • Idle/Breathing       │   │
│  │  • Identity/Growth      │     │                         │   │
│  │  • SQLite + SQLCipher   │     │                         │   │
│  └─────────────────────────┘     └─────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
SereinOS/
├── serein-core/          # Python daemon (Core Service)
│   ├── src/serein_core/
│   │   ├── main.py           # Entry point
│   │   ├── config.py         # YAML + env config
│   │   ├── database.py       # SQLCipher + full schema
│   │   ├── events.py         # Event bus
│   │   ├── identity.py       # Identity engine
│   │   ├── persona.py        # Persona engine
│   │   ├── emotion.py        # Emotion engine (11 emotions)
│   │   ├── memory.py         # Memory engine (4 types + embeddings)
│   │   ├── relationship.py   # Relationship engine
│   │   ├── growth.py         # Growth engine (5 interests)
│   │   ├── ai.py             # Brain Router (multi-provider)
│   │   ├── command_bus.py    # D-Bus system control
│   │   ├── scheduler.py      # Always-on scheduler
│   │   └── cli.py            # Admin CLI
│   ├── config/
│   │   ├── config.yaml       # Main config
│   │   ├── serein-core.service
│   │   └── serein-core-user.service
│   └── pyproject.toml
│
├── serein-avatar/        # Unity VRM Avatar
│   ├── Assets/
│   │   ├── Scripts/
│   │   │   ├── SereinAvatar.cs    # Main controller
│   │   │   └── VisemeMapper.cs    # Phoneme→VRM blend shapes
│   │   └── Models/serein.vrm
│   └── Packages/manifest.json
│
├── serein-voice/         # Voice Stack
│   └── voice_engine.py   # TTS/STT with fallback + visemes
│
├── serein-compositor/    # Wayland Compositor (C + wlroots)
│   ├── src/
│   │   ├── main.c          # Compositor entry
│   │   ├── config.c/h      # Config (JSON + CLI)
│   │   ├── log.c/h         # Logging
│   │   ├── shell.c/h       # Custom shell for Serein surfaces
│   │   └── compositor.c    # Compositor logic
│   ├── CMakeLists.txt
│   └── config/serein-compositor.conf
│
└── serein-installer/     # ISO Builder
    └── build_iso.sh      # Creates bootable Debian-based ISO
```

## Build Order (from PDF Spec)

| Step | Component | Status |
|------|-----------|--------|
| 1 | Core Persistence (DB, Identity, Memory, Scheduled Events, Audit Log) | ✅ |
| 2 | AI Brain Layer + Latency Fix (Provider Router, <2s) | ✅ |
| 3 | Command Bus - Read-only/Reversible Tiers | ✅ |
| 4 | Scheduler (Always-On Service Split) | ✅ |
| 5 | Basic 3D Avatar (VRM + Unity, Simple Expressions) | ✅ |
| 6 | Voice: TTS + Amplitude Lip Sync | ✅ |
| 7 | Phoneme-based Lip Sync (Full Pipeline) | ✅ |
| 8 | Conversation Continuity + Memory Retrieval | ✅ |
| 9 | Automatic Learning (Memory Extraction) | ✅ |
| 10 | Relationship + Emotion → Avatar Expressions | ✅ |
| 11 | Growth Engine | ✅ |
| 12 | Command Bus - Destructive/Sensitive Tiers + Input Fallback | ✅ |
| 13 | Proactive Intelligence | 🔄 |
| 14 | Task Queue + Sandboxed Coding Agent | 🔄 |
| 15 | Full Shell/Boot Replacement (Wayland, systemd) | ✅ |
| 16 | Vision (Optional) | ⏳ |
| 17 | Cross-device Apps | ⏳ |
| 18 | Web Application | ⏳ |
| 19 | Controlled Self-Evolution | ⏳ |

## Quick Start

### Prerequisites

**Core Service:**
- Python 3.11+
- SQLite3 with SQLCipher support
- systemd (for service management)
- D-Bus

**Avatar (Unity):**
- Unity 2022.3 LTS
- UniVRM 0.103+
- VRM model (from VRoid Studio)

**Compositor:**
- Linux with Wayland
- wlroots 0.17+
- meson/ninja
- libdrm, libgbm, libwayland, libxkbcommon

**Voice:**
- Azure Speech key (optional, for cloud TTS)
- HeadTTS server (optional, local)
- Piper TTS binary
- whisper.cpp binary

### Build Core Service

```bash
cd serein-core
pip install -e .[dev]

# Initialize
serein-cli init

# Run
serein-core
```

### Build Avatar (Unity)

1. Open `serein-avatar` in Unity 2022.3+
2. Import UniVRM package
3. Add your VRM model to `Assets/Models/`
4. Attach `SereinAvatar` component to scene root
5. Configure VRM path and core service connection

### Build Compositor

```bash
cd serein-compositor
meson setup build
ninja -C build
sudo ninja -C build install
```

### Build ISO

```bash
cd serein-installer
sudo ./build_iso.sh --version 0.1.0 --arch amd64
# Output: dist/SereinOS-0.1.0-20260810-x86_64.iso
```

## Configuration

### Core Service (`/etc/serein/config.yaml`)

```yaml
# AI Providers (auto-fallback)
ai:
  provider: "auto"
  models:
    fast: "llama-3.1-8b-instant"
    smart: "meta/llama-3.3-70b-instruct"
    local: "llama3.1:8b"
  nvidia_api_key: null  # Set via NVIDIA_API_KEY env
  openrouter_api_key: null

# Voice
voice:
  enabled: true
  tts_provider: "auto"  # azure, headtts, piper
  wake_word: "serein"
  azure_speech_key: null
  headtts_url: "http://localhost:5000"

# Security
security:
  sqlcipher_enabled: true
  self_modification_level: 1  # 0=observe, 1=suggest, 2=patch
```

### Compositor (`/etc/serein/serein-compositor.conf`)

```json
{
  "xwayland": true,
  "debug": false,
  "socket": "wayland-0",
  "cursor_theme": "capitaine-cursors",
  "cursor_size": 24,
  "outputs": [
    {"name": "eDP-1", "width": 1920, "height": 1080, "refresh": 60, "enabled": true}
  ]
}
```

## Key Features Implemented

### 🧠 AI Brain Router
- Multi-provider: NVIDIA, OpenRouter, Ollama, Local
- Automatic fallback on failure
- Latency-aware routing (fast/smart/local tiers)
- <2s target for fast tier

### 💾 Memory System
- 4 types: Working, Semantic, Episodic, Emotional
- Embedding-based retrieval (sentence-transformers)
- Importance-based decay + consolidation
- Core memories never decay

### 💚 Emotion Engine
- 11 emotions with baselines & decay rates
- Real-time valence calculation
- Influence on tone, proactivity, verbosity

### 🤝 Relationship Engine
- Trust/Familiarity/Attachment (0-100)
- Milestones: First Convo → Trusted Friend → Close Companion → Deep Bond
- Influences proactivity, vulnerability, memory weight

### 📈 Growth Engine
- 5 interests: Technical, Study, Productivity, Gaming, Social
- Keyword-based adaptation from conversations
- Slow decay for unused interests

### 🛡️ Command Bus (D-Bus)
- 4 permission tiers: Read-only → Reversible → Destructive → Sensitive
- Explicit confirmation for destructive actions
- Full audit log in `system_actions_log`

### ⏰ Scheduler
- Absolute, Relative, Recurring (cron), Conditional triggers
- Persists across avatar restarts (core service)
- Built-in conditions: battery, time, CPU, memory, app running

### 🎭 Avatar (Unity)
- VRM 1.0 support via UniVRM
- 15 standard visemes (Azure + Oculus mapping)
- Expression blending with smoothing
- Blink, idle, breathing animations
- TCP connection to core service

### 🗣️ Voice Stack
- Azure Neural TTS (primary, 22 visemes)
- HeadTTS (local, phoneme timestamps)
- Piper TTS (local fallback)
- Whisper.cpp STT
- openWakeWord wake word detection

## Security

- **SQLCipher** encryption at rest
- **Tiered permissions** - nothing silently escalates
- **Audit trail** - every Command Bus action logged
- **Controlled self-modification** - Level 0-2 (observe/suggest/patch)
- **Isolated user state** - no cross-user data leakage
- **PolicyKit/D-Bus** - standard Linux auth for privileged actions

## Development

### Running Tests

```bash
# Core service
cd serein-core
pytest tests/ -v

# CLI commands
serein-cli status
serein-cli chat "Hello Serein"
serein-cli remind "Test reminder" --in-minutes 5
serein-cli events
```

### Debugging

```bash
# Core service logs
journalctl -u serein-core -f

# Compositor logs
SEREIN_LOG_LEVEL=debug serein-compositor

# Avatar (Unity Console)
# Check Unity Console for connection/viseme logs
```

## Roadmap (Next Steps)

1. **Proactive Intelligence** - Generalize scheduler into ambient noticing
2. **Sandboxed Coding Agent** - Build/run/test loop in container
3. **Vision Module** - Screen capture, OCR, camera input
4. **Cross-device Apps** - Phone app talking to same core service
5. **Web Application** - Remote access dashboard
6. **Controlled Self-Evolution** - User-approved patch/rollback system

## License

MIT License - See LICENSE file for details.

## Contributing

This is a personal life project by Swarit Singh. The architecture follows the [SereinOS Master Specification v3](docs/SereinOS_Master_Specification_v3.pdf).

---

**SereinOS** — An operating system where the AI companion *is* the interface.