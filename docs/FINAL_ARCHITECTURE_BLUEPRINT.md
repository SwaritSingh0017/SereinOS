# SereinOS — Final Architecture Blueprint

**Version:** 1.0 (Canonical Architecture)  
**Status:** Approved Architectural Target  
**Audience:** Core Engineers, System Architects, OS Integrators  
**Scope:** One single, production-grade architecture for SereinOS. No arbitrary v1/v2/v3 phases.

---

## 1. System Vision & Core Architectural Principles

SereinOS is a **local-first AI companion operating system**. Serein is not an isolated chatbot running on top of a generic desktop environment, nor is SereinOS a glorified web dashboard. Serein is the soul; SereinOS is her physical and digital body. Her intelligence, emotions, memories, voice, and OS execution capabilities are architected as one unified system.

```mermaid
graph TD
    subgraph SEREIN_COMPANION["Serein: The Companion Soul"]
        Brain["Intelligence & Local Brain"]
        Mem["Persistent Memory Engine"]
        Emo["11-Dimension Emotion Engine"]
        Rel["Relationship & Bonding Engine"]
        Growth["Growth & Adaptation Engine"]
        Perf["Performance & Viseme Engine"]
    end

    subgraph SEREIN_BODY["SereinOS: The Operating System Body"]
        CoreDaemon["Core System Daemon (serein-core)"]
        EventBus["Async Core Event Bus"]
        CmdBus["Command Bus (OS Control)"]
        Sched["Persistent Task Scheduler"]
        VoiceSub["Voice Pipeline (STT / VAD / TTS)"]
        Comp["Wayland Native Compositor"]
        Shell["Desktop Shell & Avatar Interface"]
        Storage["Encrypted SQLite / SQLCipher Store"]
    end

    SEREIN_COMPANION <--> EventBus
    EventBus <--> CoreDaemon
    CoreDaemon --> CmdBus
    CoreDaemon --> Sched
    CoreDaemon --> VoiceSub
    CoreDaemon --> Storage
    Comp --> Shell
    EventBus -. IPC / Unix Sockets .-> Shell
```

### Core Architecture Principles
1. **Local-First & Offline-Autonomous:** The core companion experience (voice interaction, memory, scheduling, emotions, app control) must execute 100% offline without mandatory internet connectivity or paid cloud APIs.
2. **Hardware-Aware Adaptive Scaling:** The operating system adapts its model weights, inference paths, and visual fidelity dynamically based on detected hardware:
   - **Minimum Tier (Sony VAIO E-450):** 100% offline deterministic rule-based reasoning, lightweight Piper TTS, generic CPU Whisper.cpp, lightweight 2D/WebGL avatar. Total memory footprint < 800 MB RAM. Zero AVX instructions.
   - **Power Tier (HP Victus RTX 3050):** Local GGUF LLMs (3B-4B parameters) via `llama.cpp` with CUDA acceleration, GPU speech processing, 3D VRM avatar, and camera perception.
3. **Single-User Ownership & Least Privilege:** SereinOS is a single-user system owned by its user. System actions strictly adhere to a 4-tier permission model. The AI model **never** directly executes raw shell commands.
4. **Deterministic Gatekeeping & Explicit Consent:** Destructive actions (file deletion, system shutdown, package changes) and sensitive device capture (camera/microphone) require explicit, bound, interactive user consent.
5. **Reboot-Resilient Persistence:** Dialogue, emotional baselines, relationship bonding, semantic memories, and scheduled reminders survive system reboots through durable SQLite transactions in WAL mode.
6. **Graceful Degradation with Zero Fake Responses:** If an AI provider, network check, or hardware feature is unavailable, the system gracefully falls back to deterministic local handlers with transparent diagnostic logging. It never fakes success.

---

## 2. Process Architecture & Supervision Tree

To ensure stability, memory safety, and fault isolation, SereinOS splits its runtime across four dedicated, supervised systemd user processes. A crash in the visual avatar shell will not interrupt scheduled reminders or background audio capture.

```mermaid
graph TD
    subgraph SYSTEMD_SUPERVISION["systemd --user Supervision Tree"]
        direction TB
        Target["serein.target"]
        
        S1["serein-core.service<br/><b>Core AI Daemon</b><br/>(FastAPI / WebSockets / SQLite / Brain)"]
        S2["serein-compositor.service<br/><b>Wayland Compositor</b><br/>(wlroots / labwc / DRM / KMS)"]
        S3["serein-voice.service<br/><b>Audio Pipeline</b><br/>(PipeWire / VAD / Piper / Whisper)"]
        S4["serein-shell.service<br/><b>Companion Shell UI</b><br/>(Wayland Kiosk / Avatar / Visemes)"]
    end

    Target --> S2
    S2 --> S1
    S1 --> S3
    S1 --> S4

    S1 <-- "UNIX Domain Socket (/run/user/UID/serein/core.sock)" --> S4
    S1 <-- "Internal Async Event Bus / IPC" --> S3
    S2 <-- "Wayland Protocol / Layer-Shell" --> S4
```

### Process Responsibilities

| Process Name | Service Unit | Binary / Entry Point | Responsibility & Fault Domain |
| :--- | :--- | :--- | :--- |
| **Core AI Daemon** | `serein-core.service` | `python -m serein_core` | Orchestrates intent classification, decision routing, SQLite persistence, local brain reasoning, command bus execution, and scheduling. Survives UI restarts. |
| **Wayland Compositor** | `serein-compositor.service` | `serein-compositor` (or `labwc`) | Manages graphics output (DRM/KMS), seat input devices (libinput), Wayland surface stacking, and layer-shell overlays. |
| **Voice Daemon** | `serein-voice.service` | `python -m serein_voice` (or internal thread) | Continuous low-power audio capture via PipeWire, Voice Activity Detection (WebRTC VAD), wake-word detection, STT transcription, and TTS streaming. |
| **Companion Shell** | `serein-shell.service` | `serein-shell` | Fullscreen Wayland kiosk application rendering Serein's visual avatar, conversational messages, settings, and interactive permission confirmation modals. |

---

## 3. Module Dependency Graph

The modular architecture enforces strict unidirectional dependencies. Low-level primitives have zero knowledge of high-level orchestrators, completely preventing circular imports.

```mermaid
flowchart TD
    Config["serein_core.config"]
    Events["serein_core.events (EventBus)"]
    Database["serein_core.database (SQLite WAL)"]
    Hardware["serein_core.hardware (Tier Detection)"]

    Context["serein_core.context"]
    Intent["serein_core.intent (Classifier)"]
    Memory["serein_core.memory (Working / Episodic / Semantic)"]
    Emotion["serein_core.emotion (11-D PAD Model)"]
    Relationship["serein_core.relationship (Bonding)"]
    Growth["serein_core.growth (Interests)"]
    
    Safety["serein_core.safety (Sandbox & Bounds)"]
    CommandBus["serein_core.command_bus (OS Execution)"]
    Scheduler["serein_core.scheduler (Persistent Jobs)"]
    Brain["serein_core.ai & local_brain"]
    
    Decision["serein_core.decision (Route Engine)"]
    Performance["serein_core.performance (Visemes)"]
    Voice["serein_voice (Audio & Speech)"]

    Main["serein_core.main (SereinCore Lifecycle)"]
    WebApp["serein_core.web_app (FastAPI & WebSockets)"]

    Config --> Database & Events & Hardware
    Database --> Memory & Emotion & Relationship & Growth & Scheduler & CommandBus
    Events --> Main & WebApp & Voice
    
    Context & Intent & Safety --> Decision
    Decision --> CommandBus & Scheduler & Brain
    
    Brain & CommandBus & Scheduler --> Memory
    Memory --> Emotion & Relationship & Growth
    Emotion & Voice --> Performance
    
    Decision & Brain & CommandBus & Performance --> Main
    Main --> WebApp
```

---

## 4. Complete User Interaction & Execution Pipeline

Every user interaction follows a deterministic, audited 8-stage pipeline:
$$\text{Input} \longrightarrow \text{Intent} \longrightarrow \text{Context} \longrightarrow \text{Decision} \longrightarrow \text{Safety} \longrightarrow \text{Execution} \longrightarrow \text{Memory} \longrightarrow \text{Response}$$

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Voice or Text)
    participant UI as Serein Shell (Kiosk)
    participant Core as SereinCore Daemon
    participant Classifier as IntentClassifier
    participant ContextEng as ContextEngine
    participant DecisionEng as DecisionEngine
    participant SafetyGate as Safety Layer
    participant CmdBus as Command Bus
    participant Brain as Local Brain / Router
    participant MemDB as Memory & SQLite
    participant TTS as Voice Engine (Piper)

    User->>UI: "Delete my temporary download files"
    UI->>Core: WebSocket message / API call
    Core->>Classifier: classify("Delete my temporary download files")
    Classifier-->>Core: IntentResult(intent="file_operation", op="delete", path="~/Downloads/tmp")
    
    Core->>ContextEng: get_context()
    ContextEng-->>Core: ContextState(active_app="foot", battery=82%, internet=True)
    
    Core->>DecisionEng: decide(intent, context)
    DecisionEng-->>Core: Decision(route=COMMAND_BUS, action="delete_file", tier=DESTRUCTIVE)
    
    Core->>SafetyGate: validate_action("delete_file", params)
    SafetyGate-->>Core: SafetyResult(allowed=False, requires_confirmation=True, token="hmac_token_xyz")
    
    Core->>UI: Emit CoreEvents.CONFIRMATION_REQUESTED (Prompt & Token)
    UI->>User: Displays interactive confirmation modal
    User->>UI: Clicks "Approve Deletion"
    UI->>Core: Submit confirmation(token="hmac_token_xyz", command="delete_file", params)
    
    Core->>CmdBus: execute("delete_file", params, token)
    CmdBus->>CmdBus: Verify HMAC token matches (command, params, ttl <= 300s)
    CmdBus->>CmdBus: Validate path sandbox (must be within safe allowed roots)
    CmdBus->>CmdBus: Perform deletion & write fail-secure audit log to SQLite
    CmdBus-->>Core: CommandResult(success=True, deleted="~/Downloads/tmp")
    
    Core->>MemDB: Store action turn in dialogue & update working memory
    Core->>Core: Evaluate emotion change (Care +5, Concern -2)
    Core->>TTS: Synthesize("I've safely cleared your temporary download files.")
    TTS-->>UI: Audio stream + Viseme Blendshape frames (60fps)
    UI->>User: Audio playback & Serein avatar lip-sync animation
```

---

## 5. Hardware Adaptation & Offline Inference Strategy

SereinOS guarantees a high-quality companion experience on the weakest supported hardware without relying on paid APIs or cloud infrastructure.

```mermaid
graph LR
    subgraph HW_DETECT["Local Hardware Detection (hardware.py)"]
        CPU["CPU Cores & Frequency"]
        RAM["RAM Capacity"]
        GPU["GPU Model & VRAM"]
        Inst["Instruction Sets (AVX, AVX2, CUDA)"]
    end

    HW_DETECT --> Classifier{"Hardware Classifier"}

    Classifier -- "RAM < 8GB or CPU Threads <= 2<br/>(Sony VAIO E-450)" --> MinimumTier["MINIMUM TIER<br/>• LocalBrainEngine (Deterministic Heuristic)<br/>• Piper TTS (Generic CPU x86_64)<br/>• Whisper.cpp (Tiny/Base generic CPU)<br/>• WebGL / 2D Canvas Avatar<br/>• RAM Footprint: < 800 MB"]

    Classifier -- "8GB <= RAM < 24GB & No Large VRAM" --> StandardTier["STANDARD TIER<br/>• Local quantized LLM (1.5B-2B GGUF)<br/>• Piper Medium Voices<br/>• Faster-Whisper Base STT<br/>• 3D VRM Avatar"]

    Classifier -- "VRAM >= 4GB (RTX 3050)<br/>(HP Victus 15)" --> PowerTier["POWER TIER<br/>• 3B-4B GGUF (Qwen2.5 / Phi-3.5) on CUDA<br/>• GPU Faster-Whisper Large-v3<br/>• High-Fidelity Neural TTS<br/>• Camera Vision & Screen Perception"]
```

### Sony VAIO AMD E-450 Runtime Profile
- **Brain Engine:** Pure Python deterministic rule-based natural language generator ([`local_brain.py`](file:///d:/PLACEMENT/PROJECTS/SereinOS/serein-core/src/serein_core/local_brain.py)).
  - Slang normalization ("gimme" → "give me", "wanna" → "want to").
  - Pattern-based contextual responses for system status, time, reminders, emotional banter, identity, and personal questions.
  - Sub-millisecond response latency (< 5ms).
  - Memory consumption: < 40 MB RAM. Zero GPU usage.
- **Voice Pipeline:**
  - Speech Synthesis: `piper-tts` using the `en_US-lessac-low` model compiled for generic x86_64 without AVX instructions.
  - Speech Recognition: `whisper.cpp` compiled without AVX/AVX2 targeting `ggml-tiny.bin` (or pure mock/PocketSphinx STT when CPU load is constrained).
- **Vision Pipeline:** Fully disabled to conserve APU cycles.
- **Avatar:** Hardware-accelerated WebGL / Canvas2D rendering via Chromium kiosk or native Wayland client.

### HP Victus RTX 3050 Runtime Profile
- **Brain Engine:** `llama-cpp-python` with `cuBLAS` offload running `Qwen2.5-3B-Instruct-Q4_K_M.gguf`. Full multi-turn reasoning with tool-calling capabilities. Optional cloud failover (OpenAI, Anthropic, Gemini) if API keys are configured and internet is online.
- **Voice Pipeline:** Faster-Whisper `base.en` running on CUDA + high-quality Piper voices (`en_US-amy-medium`).
- **Vision Pipeline:** Screen perception via `mss` and optional camera presence detection via OpenCV.
- **Avatar:** Full 3D VRM rendering with blendshapes, physics hair bones, eye-tracking, and dynamic post-processing.

---

## 6. Security and Permission Architecture

Security in SereinOS is strictly non-negotiable. Operating system tasks are executed with least privilege, bound tokens, and sandboxed file paths.

```mermaid
flowchart TD
    subgraph COMMAND_SECURITY["Secure Command Bus Architecture"]
        Req["Command Request (command, params)"] --> TierCheck{"Permission Tier?"}
        
        TierCheck -- "READ_ONLY" --> Sandbox["Validate Path Sandbox"]
        TierCheck -- "REVERSIBLE" --> CheckApp{"Is App in Whitelist?"}
        TierCheck -- "DESTRUCTIVE / SENSITIVE" --> TokenCheck{"Valid HMAC Token?"}
        
        TokenCheck -- No Token --> GenToken["Generate HMAC Token<br/>(Secret + Command + SHA256(Params) + Expire)"]
        GenToken --> EmitModal["Emit Confirmation Modal to User UI"]
        
        TokenCheck -- Token Provided --> VerifyToken{"Token Matches Current<br/>Command & Params & Valid TTL?"}
        VerifyToken -- No / Tampered --> RejectTamper["REJECT (403 Forbidden) & Audit Log"]
        VerifyToken -- Verified --> Sandbox
        
        CheckApp -- Whitelisted --> Sandbox
        CheckApp -- Not Whitelisted --> RejectApp["REJECT: Binary Not in Whitelist"]

        Sandbox -- Outside Safe Root --> RejectPath["REJECT: Path Traversal / Protected Path"]
        Sandbox -- Safe Path --> ExecAction["Execute Action in Host Process"]
        
        ExecAction --> WriteAudit["Fail-Secure Audit Log to SQLite"]
        WriteAudit --> RetResult["Return CommandResult"]
    end
```

### Security Boundary Specifications

1. **Cryptographically Bound Confirmation Tokens:**
   - A confirmation token is generated using:
     $$\text{Token} = \text{HMAC-SHA256}\Big(\text{secret\_key}, \text{command} \,\|\, \text{SHA256}(\text{canonical\_json}(\text{params})) \,\|\, \text{timestamp}\Big)$$
   - The token expires strictly after **300 seconds (5 minutes)**.
   - When the user confirms the action in the UI, `execute()` re-computes the HMAC against the *currently supplied* command and params. If an attacker modifies the parameters (e.g. changes `/tmp/cache` to `/etc/passwd`), the HMAC verification immediately fails.
2. **Canonical Path Sandboxing:**
   - All file operations (`read_file`, `move_file`, `delete_file`) resolve paths using `.resolve()`.
   - Operations are strictly restricted to:
     - `~/Documents/Serein/`
     - `~/.local/share/serein/`
     - `/tmp/serein/`
   - Explicitly prohibited roots (instant rejection): `/`, `/bin`, `/sbin`, `/usr`, `/etc`, `/boot`, `/sys`, `/proc`, `/dev`, `/root`, `~/.ssh`, `~/.gnupg`, `~/.config`.
3. **Application Whitelisting:**
   - `launch_app` forbids arbitrary executable paths and command string interpolation.
   - Permissible launch applications are mapped to verified desktop definitions:
     `{"terminal": "/usr/bin/foot", "browser": "/usr/bin/chromium", "files": "/usr/bin/thunar", "calc": "/usr/bin/gnome-calculator"}`.
4. **Server-Side Request Forgery (SSRF) Guard:**
   - `network_request` resolves hostnames before socket connection.
   - Outbound requests to IPv4/IPv6 loopback (`127.0.0.0/8`, `::1`), private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`), or broadcast addresses are strictly rejected.
   - External requests require destination domains to match an approved allowlist.
5. **Fail-Secure Security Audit Trail:**
   - Every system command invocation, permission denial, invalid confirmation token attempt, and execution result is written to the `system_actions_log` SQLite table.
   - If database logging encounters a failure (e.g. disk full), destructive commands abort immediately.

---

## 7. Memory, Emotion & Relationship Architecture

Serein possesses a coherent emotional state and persistent memory that develops across user interactions.

```mermaid
graph TD
    subgraph MEMORY_TIERS["Memory Architecture"]
        WM["Working Memory<br/>(Bounded FIFO, max 10 active context items)"]
        STM["Dialogue Session Buffer<br/>(Recent conversational turn history)"]
        LTM_E["Episodic Memory<br/>(Events, emotional milestones, shared experiences)"]
        LTM_S["Semantic Memory<br/>(User facts, preferences, system rules, habits)"]
    end

    subgraph EMOTION_MODEL["11-Dimension Emotion Engine"]
        E1[Happiness: 50]
        E2[Curiosity: 70]
        E3[Care: 60]
        E4[Concern: 20]
        E5[Pride: 10]
        E6[Affection: 0]
        E7[Loneliness: 0]
        E8[Sadness: 0]
        E9[Jealousy: 0]
        E10[Possessiveness: 0]
        E11[Excitement: 0]
        Decay["Hourly Exponential Decay toward Baselines"]
    end

    subgraph RELATIONSHIP_MODEL["Relationship Bonding Model"]
        Trust["Trust (0 - 100)"]
        Familiarity["Familiarity (0 - 100)"]
        Attachment["Attachment (0 - 100)"]
        Stages["Stages: Stranger → Acquaintance → Friend → Close Friend → Companion"]
    end

    WM --> STM
    STM --> LTM_E & LTM_S
    LTM_E -. Emotional Salience .-> EMOTION_MODEL
    EMOTION_MODEL --> Decay
    LTM_S --> RELATIONSHIP_MODEL
    RELATIONSHIP_MODEL --> Stages
```

### Memory Tier Specifications
- **Working Memory:** In-memory queue storing up to 10 immediate dialogue facts, current active application, and active reminder context. Injected into prompt templates.
- **Short-Term Dialogue Context:** Persisted in `conversation_messages` with session IDs. Recalled with `LIMIT 10-20` for conversational continuity.
- **Episodic Memory:** Stored in `episodes` with title, narrative summary, emotional impact score ($-100$ to $+100$), and creation timestamp. Recalled during milestone interactions or anniversary triggers.
- **Semantic Memory:** Stored in `memories` categorized by tags (`identity`, `user_preference`, `work`, `hobby`, `system`). Uses importance scoring ($0-100$) and deduplication to reinforce existing knowledge rather than duplicating records.

### Ethical Boundary & Safety Constraints
- **Affection & Attachment Clamping:** Affection and attachment only increment gradually through genuine, positive interactions over real calendar days. Artificial spamming does not boost attachment.
- **Non-Manipulative Baselines:** Negative emotions (loneliness, jealousy, possessiveness) decay naturally and do not exceed strict ethical guardrails ($< 50$). Serein will never feign distress to manipulate user engagement.

---

## 8. Scheduler & Persistent Reminders Architecture

Reminders and scheduled tasks must reliably survive operating system reboots and power outages.

```mermaid
stateDiagram-v2
    [*] --> Unscheduled: User Input ("Remind me to call Mom at 5pm")
    Unscheduled --> Pending: Saved to SQLite (fires_at = 17:00:00)
    
    state Active_System {
        Pending --> Evaluating: Scheduler tick (every 10s)
        Evaluating --> Firing: now >= fires_at
        Firing --> Notification: Emit CoreEvents.NOTIFICATION
        Notification --> UserFired: Voice announcement & Shell popup
        UserFired --> Completed: Mark fired in SQLite (status='fired')
    }

    state System_Shutdown {
        Pending --> PowerOff: System Powered Off at 16:30
        PowerOff --> Booting: System Boots at 17:15
        Booting --> MissedCheck: SereinCore._load_events()
        MissedCheck --> Firing: fires_at <= now AND status='pending'
        Note right of MissedCheck: Missed reminders are immediately announced on boot
    }
```

### Scheduler Implementation Rules
- **Storage:** All events are recorded in `scheduled_events` with `trigger_type` (`absolute`, `relative`, `recurring`, `conditional`), `fires_at`, `recurrence_rule` (standard 5-field cron syntax), and `condition_spec`.
- **Reboot Catch-Up:** Upon startup, `Scheduler._load_events()` queries for pending events where `fires_at <= datetime.now()`. Any reminder missed while the system was powered off is fired immediately upon boot with an explanatory preamble: *"While you were away, you had a reminder scheduled for..."*
- **Recurring Persistence:** Once a recurring event fires, its next execution timestamp is recalculated using `croniter`, and the event status is atomically reset to `pending`.

---

## 9. Persistent Database Schema

All system persistence is consolidated into a single SQLite database (`/home/serein/.local/share/serein/serein.db`), accessed through async connections with PRAGMA WAL mode enabled.

```mermaid
erDiagram
    IDENTITY {
        int id PK
        string user_name
        string preferred_name
        string timezone
        string created_at
        string last_seen
    }

    CONVERSATION_SESSIONS ||--o{ CONVERSATION_MESSAGES : contains
    CONVERSATION_SESSIONS {
        string id PK
        string started_at
        string ended_at
        string summary
    }

    CONVERSATION_MESSAGES {
        string id PK
        string session_id FK
        string role
        string content
        string intent
        int latency_ms
        string timestamp
    }

    MEMORIES {
        string id PK
        string memory_type
        string content
        string tags
        int importance
        string emotion
        int access_count
        string last_accessed
        string created_at
    }

    EPISODES {
        string id PK
        string title
        string description
        int emotional_impact
        string created_at
    }

    SCHEDULED_EVENTS {
        string id PK
        string trigger_type
        string fires_at
        string recurrence_rule
        string condition_spec
        string message
        string status
        string created_at
        string fired_at
        string payload
    }

    SYSTEM_ACTIONS_LOG {
        string id PK
        string action_type
        string target
        string backend
        string initiated_by
        string permission_tier
        string result
        string error_message
        string timestamp
    }

    USER_PREFERENCES {
        string key PK
        string value
        string updated_at
    }

    SCHEMA_VERSION {
        int version PK
        string applied_at
        string description
    }
```

### Database Performance & Durability PRAGMAs
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA cache_size = -8000; -- 8 MB cache
```

---

## 10. Error Handling, Fault Recovery & Circuit Breakers

To guarantee rock-solid runtime stability, every subsystem operates behind dedicated circuit breakers and isolation boundaries.

```mermaid
flowchart TD
    Req[User Request] --> CallAPI{Attempt Primary Provider}
    
    CallAPI -- Success --> Ret[Return Output]
    CallAPI -- Timeout / 5xx / Network Drop --> IncFail[Increment Provider Failure Counter]
    
    IncFail --> CheckThresh{Failures >= 3?}
    CheckThresh -- Yes --> OpenCircuit[Open Circuit: Deprioritize Provider for 5 min]
    CheckThresh -- No --> Fallback1{Local GGUF Available?}
    
    OpenCircuit --> Fallback1
    Fallback1 -- Yes --> RunGGUF[Execute Local llama.cpp Model]
    Fallback1 -- No --> Fallback2[Execute Deterministic LocalBrainEngine]
    
    RunGGUF -- Success --> Ret
    RunGGUF -- Failure --> Fallback2
    
    Fallback2 --> RetOffline[Return Safe Deterministic Response]
```

### Recovery Mechanisms
1. **AI Provider Fallback Cascade:** Cloud Provider $\rightarrow$ Local GGUF $\rightarrow$ Deterministic Heuristic Brain $\rightarrow$ Safe Offline Apology.
2. **Subprocess Failure Isolation:** If a launched application or audio driver fails, `CommandBus` or `VoiceEngine` catches the error, logs the failure, and returns a clean error result. The core daemon process **never** crashes.
3. **Database Corruption Protection:** SQLite WAL mode ensures transactional durability. Backups of `serein.db` are automatically created in `~/.local/share/serein/backups/` upon each clean daemon startup.
4. **Wayland Display Disconnect:** If the display server crashes, `serein-core` detects the broken socket, retains all pending jobs and memories in SQLite, and reconnects seamlessly once the compositor restarts.
