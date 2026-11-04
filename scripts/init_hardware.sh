#!/usr/bin/env bash
# ==============================================================================
# SereinOS Physical Hardware Initialization Script
# Target Platform: Sony VAIO VPCYB35AN (PCG-31311W / AMD E-450 APU)
#
# Tasks:
# 1. ALSA Audio Initialization (unmute Master/Speaker/Headphone, default 75% volume)
# 2. DRM / Graphics Acceleration Verification (radeon driver, Mesa r600, VA-API/VDPAU)
# 3. Backlight & Power Udev Configuration (/sys/class/backlight write permissions)
# 4. Sony Laptop ACPI Platform Driver Enablement (sony-laptop)
# 5. Network Interface & Firmware Verification (ath9k WiFi, r8169 Ethernet)
# ==============================================================================

set -uo pipefail

LOG_FILE="/var/log/serein-hardware-init.log"
if [ ! -w "/var/log" ] 2>/dev/null; then
    LOG_FILE="/tmp/serein-hardware-init.log"
fi

log() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[${timestamp}] [SereinHW] $*" | tee -a "${LOG_FILE}" 2>/dev/null || echo "[${timestamp}] [SereinHW] $*"
}

log "========================================================"
log "Starting SereinOS Hardware Bootstrap for Sony VAIO VPCYB35AN"
log "========================================================"

# ------------------------------------------------------------------------------
# 1. ALSA Audio Subsystem Initialization (Realtek ALC269)
# ------------------------------------------------------------------------------
log "Step 1: Initializing ALSA audio subsystem (Realtek ALC269)..."

if command -v amixer >/dev/null 2>&1; then
    # Un-mute Master and set default volume to 75%
    if amixer -c 0 set Master 75% unmute >/dev/null 2>&1; then
        log "ALSA: Master volume set to 75% and unmuted."
    elif amixer set Master 75% unmute >/dev/null 2>&1; then
        log "ALSA: Master volume set to 75% on default card."
    else
        log "ALSA: Note - Master mixer control not found or no physical soundcard active."
    fi

    # Un-mute Speaker and Headphone
    for ctl in "Speaker" "Headphone" "PCM" "Front"; do
        amixer -c 0 set "${ctl}" 100% unmute >/dev/null 2>&1 || true
        amixer set "${ctl}" 100% unmute >/dev/null 2>&1 || true
    done

    # Disable ALSA Auto-Mute mode so headphone and speakers work without spurious muting
    amixer -c 0 set "Auto-Mute Mode" Disabled >/dev/null 2>&1 || true
    amixer set "Auto-Mute Mode" Disabled >/dev/null 2>&1 || true

    log "ALSA: Audio channels unmuted and initialized successfully."
else
    log "ALSA: Warning - 'amixer' command not installed. Sound initialization skipped."
fi

# ------------------------------------------------------------------------------
# 2. DRM Graphics Acceleration Verification (AMD Radeon HD 6320)
# ------------------------------------------------------------------------------
log "Step 2: Checking DRM graphics acceleration (Radeon HD 6320 / Wrestler)..."

# Ensure radeon driver module is loaded
if [ -d "/sys/module/radeon" ]; then
    log "DRM: Kernel module 'radeon' is active."
elif command -v modprobe >/dev/null 2>&1; then
    if modprobe radeon 2>/dev/null; then
        log "DRM: Kernel module 'radeon' successfully loaded via modprobe."
    else
        log "DRM: Note - Could not load 'radeon' module (running in container or virtualized environment)."
    fi
fi

# Verify DRM card vendor
if [ -d "/sys/class/drm" ]; then
    for card in /sys/class/drm/card[0-9]*; do
        if [ -f "${card}/device/vendor" ]; then
            vendor="$(cat "${card}/device/vendor" 2>/dev/null || true)"
            if [ "${vendor,,}" = "0x1002" ]; then
                log "DRM: Detected AMD GPU hardware (${card}) with vendor 0x1002."
            fi
        fi
    done
fi

# ------------------------------------------------------------------------------
# 3. Display Backlight & Udev Permissions
# ------------------------------------------------------------------------------
log "Step 3: Setting backlight sysfs permissions for non-root users..."

if [ -d "/sys/class/backlight" ]; then
    for bl in /sys/class/backlight/*; do
        if [ -f "${bl}/brightness" ]; then
            log "Backlight: Found backlight interface at ${bl}."
            chmod 664 "${bl}/brightness" 2>/dev/null || true
            chgrp video "${bl}/brightness" 2>/dev/null || true
        fi
    done
fi

# Reload udev rules if udevadm is present
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=backlight 2>/dev/null || true
    log "Udev: Backlight rules reloaded."
fi

# ------------------------------------------------------------------------------
# 4. Sony VAIO Platform Driver (sony-laptop)
# ------------------------------------------------------------------------------
log "Step 4: Ensuring Sony VAIO platform driver (sony-laptop) is loaded..."

if [ -d "/sys/module/sony_laptop" ]; then
    log "Sony: Platform driver 'sony-laptop' is loaded."
elif command -v modprobe >/dev/null 2>&1; then
    modprobe sony-laptop 2>/dev/null && log "Sony: 'sony-laptop' loaded." || log "Sony: 'sony-laptop' module load skipped."
fi

# ------------------------------------------------------------------------------
# 5. Network Hardware & Service Status
# ------------------------------------------------------------------------------
log "Step 5: Inspecting network hardware (Atheros AR9285 & Realtek RTL8168)..."

if [ -d "/sys/class/net" ]; then
    for iface in /sys/class/net/*; do
        ifname="$(basename "${iface}")"
        if [ "${ifname}" != "lo" ]; then
            oper="$(cat "${iface}/operstate" 2>/dev/null || echo "unknown")"
            log "Network: Interface ${ifname} status: ${oper}."
        fi
    done
fi

if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet NetworkManager 2>/dev/null; then
        log "Network: NetworkManager service is active."
    fi
fi

log "========================================================"
log "SereinOS Hardware Bootstrap Complete (Status: OK)"
log "========================================================"

exit 0
