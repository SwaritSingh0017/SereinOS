# SereinOS - Personal Setup Guide
## Complete Steps to Build ISO and Install on Your Laptop

---

## 📋 Table of Contents
1. [Prerequisites](#prerequisites)
2. [Development Environment Setup](#development-environment-setup)
3. [Building All Components](#building-all-components)
4. [Creating the Bootable ISO](#creating-the-bootable-iso)
5. [Writing ISO to USB](#writing-iso-to-usb)
6. [Installing on Target Laptop](#installing-on-target-laptop)
7. [Post-Install Configuration](#post-install-configuration)
8. [Troubleshooting](#troubleshooting)

---

## 🔧 Prerequisites

### Host Machine Requirements (Build Machine)
- **OS**: Linux (Ubuntu 22.04+, Debian 12, Fedora 38+) **or** Windows 10/11 with WSL2 **or** macOS with Docker
- **RAM**: 16 GB minimum (32 GB recommended)
- **Disk**: 50 GB free space for build artifacts
- **CPU**: 4+ cores, x86_64 architecture
- **Internet**: Required for downloading packages

### Target Laptop Requirements
- **Architecture**: x86_64 (AMD64) - UEFI required
- **RAM**: 8 GB minimum (16 GB recommended)
- **Storage**: 32 GB minimum (64 GB+ recommended)
- **Graphics**: Intel/AMD integrated or NVIDIA (proprietary drivers auto-installed)
- **WiFi**: Linux-compatible (Intel, Realtek, Atheros)
- **Secure Boot**: Must be **DISABLED** in BIOS

---

## 🛠 Development Environment Setup

### Option A: Native Linux (Recommended - Fastest)

```bash
# 1. Install build dependencies (Ubuntu/Debian)
sudo apt update && sudo apt install -y \
    git curl wget \
    build-essential cmake meson ninja-build pkg-config \
    python3 python3-pip python3-venv python3-dev \
    libwlroots-dev libwayland-dev libwayland-client0 \
    libinput-dev libseat-dev libpipewire-0.3-dev \
    libgbm-dev libdrm-dev libpixman-1-dev libcairo2-dev libpango1.0-dev \
    libjson-c-dev libsystemd-dev \
    debootstrap grub-efi-amd64 shim-signed \
    mtools xorriso squashfs-tools dosfstools e2fsprogs \
    docker.io qemu-system-x86 qemu-utils \
    plymouth plymouth-themes

# 2. Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# 3. Install Unity 2022.3 LTS (for avatar)
# Download from: https://unity.com/releases/editor/whats-new/2022.3.20
# Or via Unity Hub
```

### Option B: Windows with WSL2

```powershell
# 1. Enable WSL2
wsl --install -d Ubuntu-22.04

# 2. Inside Ubuntu WSL2, run the Linux setup above
# 3. Enable systemd in WSL2:
echo -e "[boot]\nsystemd=true" | sudo tee /etc/wsl.conf
wsl --shutdown  # Restart WSL
```

### Option C: macOS (Docker Only)

```bash
# 1. Install Docker Desktop
brew install --cask docker

# 2. Allocate 16GB+ RAM to Docker Desktop
# 3. Build will run entirely in Docker containers
```

---

## 📦 Building All Components

### 1. Clone and Prepare Repository

```bash
# Navigate to your projects directory
cd ~/projects

# Clone (if not already)
git clone https://github.com/your-org/SereinOS.git
cd SereinOS

# Make scripts executable
chmod +x scripts/*.sh serein-installer/build_iso.sh serein-avatar/build_avatar.sh
```

### 2. Build Wayland Compositor

```bash
cd serein-compositor

# Configure with meson
meson setup build --prefix=/usr/local \
    -Dwerror=false \
    -Db_lto=true \
    -Db_pie=true

# Compile
ninja -C build

# Verify binary exists
ls -la build/serein-compositor
# Should output: serein-compositor (executable, ~2-5 MB)

cd ..
```

### 3. Build Python Package (serein-core)

```bash
cd serein-core

# Create virtual environment (optional but recommended)
python3 -m venv venv
source venv/bin/activate

# Install build dependencies
pip install --upgrade pip setuptools wheel build

# Build wheel and sdist
python -m build

# Verify dist/
ls -la dist/
# Should show: serein_core-0.1.0-py3-none-any.whl and .tar.gz

# Install in development mode with all extras
pip install -e .[dev,voice,vision]

cd ..
```

### 4. Setup VRM Model (Avatar)

```bash
# Run VRM setup script (downloads Alicia Solid model)
./scripts/setup_vrm.sh setup

# Verify model exists
ls -la /usr/local/share/serein/models/avatar.vrm
# Should be ~15-25 MB VRM file
```

### 5. Install Voice Binaries

```bash
# This installs: Piper TTS, whisper.cpp, espeak-ng, openWakeWord
sudo ./scripts/install_voice.sh install

# Verify installations
which piper whisper-cli espeak-ng
# All should return paths
```

### 6. Build Unity Avatar (Optional - Requires Unity 2022.3+)

```bash
# Only if you have Unity installed and licensed
cd serein-avatar

# Make build script executable
chmod +x build_avatar.sh

# Run build (requires UNITY_LICENSE_* env vars or Unity Hub login)
./build_avatar.sh

# Verify
ls -la Build/serein-avatar
# Should be Linux executable

cd ..
```

### 7. Run Master Build Script (Builds Everything)

```bash
# This runs all steps above automatically
./scripts/build_all.sh all

# Or run individual steps:
./scripts/build_all.sh check       # Check prerequisites only
./scripts/build_all.sh compositor  # Build compositor only
./scripts/build_all.sh python      # Build Python package only
./scripts/build_all.sh voice       # Install voice binaries only
./scripts/build_all.sh vrm         # Setup VRM model only
./scripts/build_all.sh avatar      # Build Unity avatar only
./scripts/build_all.sh test        # Run tests only
./scripts/build_all.sh iso         # Build ISO only (after all above)
```

---

## 💿 Creating the Bootable ISO

### Option 1: Docker Mode (Easiest - Works on Any OS with Docker)

```bash
cd serein-installer

# Build with Docker (recommended for Windows/macOS/Linux)
VERSION=0.1.0 BUILD_MODE=docker ./build_iso.sh

# Or with explicit flags
./build_iso.sh --mode docker --version 0.1.0 --arch amd64
```

### Option 2: Native Linux Mode (Fastest on Linux)

```bash
cd serein-installer

# Requires sudo and all build deps installed
sudo ./build_iso.sh --mode native --version 0.1.0
```

### Option 3: WSL2 Mode (Windows with WSL2)

```bash
cd serein-installer

# Run inside WSL2 Ubuntu
./build_iso.sh --mode wsl2 --version 0.1.0
```

### Expected Output

```
[INFO] Platform: linux | Build Mode: docker
[INFO] Starting SereinOS ISO build...
[INFO] Version: 0.1.0
[INFO] Architecture: amd64
[INFO] Base: debian bookworm
...
[SUCCESS] ISO created: /path/to/SereinOS/dist/SereinOS-0.1.0-20260812-x86_64.iso
[INFO] SHA256: a1b2c3d4...  SereinOS-0.1.0-20260812-x86_64.iso.sha256
```

### Verify ISO

```bash
# Check ISO exists and is valid
ls -la dist/SereinOS-*.iso

# Verify checksum
sha256sum -c dist/SereinOS-*.iso.sha256

# Quick test with qemu (optional)
qemu-system-x86_64 -cdrom dist/SereinOS-*.iso -m 2G -boot d
```

---

## 🔥 Writing ISO to USB

### Linux/macOS (dd)

```bash
# 1. Identify USB device (CAREFUL - this destroys data!)
lsblk
# Look for your USB: /dev/sdX (e.g., /dev/sdb)

# 2. Unmount any partitions
sudo umount /dev/sdX*

# 3. Write ISO (REPLACE /dev/sdX WITH YOUR ACTUAL DEVICE)
sudo dd if=dist/SereinOS-0.1.0-20260812-x86_64.iso \
    of=/dev/sdX \
    bs=4M status=progress conv=fsync

# 4. Sync and eject
sync
sudo eject /dev/sdX
```

### Windows (Rufus - Recommended)

1. Download **Rufus**: https://rufus.ie/
2. Insert USB drive (8 GB+)
3. Open Rufus:
   - **Device**: Select your USB
   - **Boot selection**: Click SELECT → choose `SereinOS-*.iso`
   - **Partition scheme**: GPT
   - **Target system**: UEFI (non-CSM)
   - **File system**: FAT32 (default)
4. Click **START**
5. Select **Write in DD Image mode** when prompted
6. Wait for completion

### Windows (Ventoy - Alternative)

1. Install Ventoy to USB: https://www.ventoy.net/
2. Copy ISO to Ventoy USB
3. Boot from USB → Select ISO from Ventoy menu

---

## 💻 Installing on Target Laptop

### 1. Prepare Target Laptop

```bash
# In BIOS/UEFI (press F2, F12, Del, or Esc at boot):
# 1. Disable Secure Boot
# 2. Set Boot Mode: UEFI Only (disable CSM/Legacy)
# 3. Disable Fast Boot
# 4. Enable USB Boot
# 5. Set USB as first boot priority
# 6. Save & Exit
```

### 2. Boot from USB

1. Insert USB drive
2. Power on laptop
3. Press boot menu key (F12, F8, Esc) → Select USB
4. GRUB menu appears:
   - **SereinOS** (default) - Normal boot
   - **SereinOS (Safe Mode)** - Nomodeset for graphics issues
   - **SereinOS (Recovery)** - Single user mode

### 3. Live Environment

- Auto-logs in as user `serein` (password: `srein`)
- Desktop: Wayland + Serein Compositor
- Serein Core starts automatically
- Avatar launches on display

### 4. Install to Disk (Optional - Persistent Install)

```bash
# From live environment terminal:
sudo serein-installer/install-to-disk.sh

# Or manually:
# 1. Partition disk (GPT + EFI + root)
sudo cfdisk /dev/nvme0n1  # or /dev/sda
#   - 512M EFI System Partition (/boot/efi)
#   - Rest: Linux filesystem (/)

# 2. Format
sudo mkfs.fat -F32 /dev/nvme0n1p1
sudo mkfs.ext4 /dev/nvme0n1p2

# 3. Mount and copy
sudo mount /dev/nvme0n1p2 /mnt
sudo mkdir -p /mnt/boot/efi
sudo mount /dev/nvme0n1p1 /mnt/boot/efi

# Copy live system (using unsquashfs or rsync from live)
# This is complex - use the install script above
```

---

## ⚙️ Post-Install Configuration

### 1. First Boot Setup

```bash
# After reboot into installed system:

# 1. Change default passwords
passwd  # Change 'srein' to strong password
sudo passwd root  # Set root password

# 2. Configure WiFi
nmcli device wifi list
nmcli device wifi connect "YOUR_SSID" password "YOUR_PASSWORD"

# 3. Verify Serein services
systemctl status serein-core
systemctl --user status serein-core-user

# 4. Check avatar connection
systemctl status serein-avatar

# 5. Test voice
serein-cli voice test "Hello Serein"

# 6. Test vision
serein-cli vision capture
```

### 2. Configure AI Providers

```bash
# Edit config
sudo nano /etc/serein/config.yaml

# Add your API keys:
ai:
  providers:
    nvidia:
      api_key: "your_nvidia_api_key"
      enabled: true
    openrouter:
      api_key: "your_openrouter_key"
      enabled: true
    ollama:
      endpoint: "http://localhost:11434"
      enabled: true
```

### 3. Enable/Disable Services

```bash
# Core services (always on)
sudo systemctl enable serein-core
systemctl --user enable serein-core-user

# Optional services
sudo systemctl enable serein-web      # Web dashboard on :8080
sudo systemctl enable serein-voice    # Voice pipeline
sudo systemctl enable serein-vision   # Screen/camera capture

# Start now
sudo systemctl start serein-web
systemctl --user start serein-core-user
```

### 4. Web Dashboard

```bash
# Access at http://localhost:8080 or http://<laptop-ip>:8080
# Features:
# - Chat with Serein
# - System monitoring
# - Memory browser
# - Configuration editor
# - Command terminal
```

---

## 🔍 Troubleshooting

### Boot Issues

| Problem | Solution |
|---------|----------|
| Black screen on boot | Try "Safe Mode" in GRUB (nomodeset) |
| "No bootable device" | Verify USB written correctly, try different USB port |
| Secure Boot error | Disable Secure Boot in BIOS |
| GRUB not showing | BIOS → Boot → UEFI USB first, disable Fast Boot |

### Graphics/Compositor Issues

```bash
# Check compositor logs
journalctl -u serein-compositor -f

# Test Wayland
WAYLAND_DISPLAY=wayland-0 weston-terminal

# Reset compositor config
sudo rm /etc/serein/compositor/config.yaml
sudo systemctl restart serein-compositor
```

### Serein Core Not Starting

```bash
# Check logs
journalctl -u serein-core -f
# User service:
journalctl --user -u serein-core-user -f

# Common fixes:
# 1. Database permissions
sudo chown -R serein:serein /var/lib/serein
# 2. Config validation
serein-core --validate-config
# 3. Reinstall Python deps
pip install -e /usr/local/lib/serein[dev,voice,vision]
```

### Voice Not Working

```bash
# Test TTS
piper --model /usr/local/share/serein/models/piper/en_US-lessac-medium.onnx \
    --output_file test.wav "Hello world"
aplay test.wav

# Test STT
whisper-cli -m /usr/local/share/serein/models/ggml-base.en.bin \
    -f test.wav

# Check audio
pactl info
speaker-test -t wav -c 2
```

### Avatar Not Connecting

```bash
# Check avatar service
systemctl status serein-avatar

# Verify port 8765
netstat -tlnp | grep 8765

# Test manually
cd /usr/local/bin
./serein-avatar --host 127.0.0.1 --port 8765

# Compositor logs for bridge
journalctl -u serein-compositor | grep -i avatar
```

### Network/Internet Issues

```bash
# Check NetworkManager
nmcli device status
nmcli connection show

# Restart networking
sudo systemctl restart NetworkManager

# DNS issues
cat /etc/resolv.conf
# Should show nameserver (e.g., 1.1.1.1 or 8.8.8.8)
```

---

## 📝 Quick Reference Commands

```bash
# Service management
sudo systemctl [start|stop|restart|status] serein-core
systemctl --user [start|stop|restart|status] serein-core-user

# View logs
journalctl -u serein-core -f           # System service
journalctl --user -u serein-core-user -f  # User service
journalctl -u serein-compositor -f     # Compositor
journalctl -u serein-avatar -f         # Avatar
journalctl -u serein-web -f            # Web dashboard

# CLI commands
serein-cli status                      # System status
serein-cli chat "Hello"               # Quick chat
serein-cli remind "Take a break" --in-minutes 30
serein-cli events list
serein-cli test-db

# Configuration
sudo nano /etc/serein/config.yaml      # Main config
systemctl --user edit serein-core-user # User service override

# Update Serein
cd /path/to/SereinOS
git pull
./scripts/build_all.sh all
# Rebuild ISO and reinstall
```

---

## 🔐 Security Checklist

- [ ] Changed default password (`srein`)
- [ ] Set root password
- [ ] Configured firewall (`ufw enable`)
- [ ] Disabled SSH password auth (use keys only)
- [ ] Reviewed `/etc/serein/permissions.yaml`
- [ ] Enabled automatic security updates
- [ ] Backed up encryption key for database

---

## 📚 Additional Resources

- **Architecture Docs**: `docs/ARCHITECTURE.md`
- **Database Schema**: `docs/DATABASE_SCHEMA.md`
- **Emotion Engine**: `docs/EMOTION_ENGINE.md`
- **Memory System**: `docs/MEMORY_SYSTEM.md`
- **Relationship Engine**: `docs/RELATIONSHIP_ENGINE.md`
- **Growth Engine**: `docs/GROWTH_ENGINE.md`
- **Vision System**: `docs/VISION.md`

---

## 🆘 Getting Help

1. Check logs first (`journalctl -u serein-core -f`)
2. Run `serein-cli status` for system health
3. Try safe mode boot (GRUB → Safe Mode)
4. Check GitHub Issues: https://github.com/serein-os/serein/issues
5. Discord: https://discord.gg/serein-os (if available)

---

**Last Updated**: 2026-08-12
**Version**: 0.1.0
**Author**: Your SereinOS Setup Guide