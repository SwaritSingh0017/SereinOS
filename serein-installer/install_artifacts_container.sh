#!/bin/bash
# SereinOS Artifacts & Kiosk Desktop Installer (Runs inside serein-builder container)
set -euo pipefail

echo "[INFO] Cleaning any stale mounts and mounting virtual filesystems for chroot..."
umount -l /build/chroot/dev 2>/dev/null || true
umount -l /build/chroot/proc 2>/dev/null || true
umount -l /build/chroot/sys 2>/dev/null || true
umount -l /build/chroot/run 2>/dev/null || true

mount --bind /dev /build/chroot/dev
mount --bind /proc /build/chroot/proc
mount --bind /sys /build/chroot/sys
mount --bind /run /build/chroot/run
cp /etc/resolv.conf /build/chroot/etc/resolv.conf 2>/dev/null || true

cleanup_mounts() {
    umount -l /build/chroot/dev 2>/dev/null || true
    umount -l /build/chroot/proc 2>/dev/null || true
    umount -l /build/chroot/sys 2>/dev/null || true
    umount -l /build/chroot/run 2>/dev/null || true
}
trap cleanup_mounts EXIT

# Create required directories
mkdir -p /build/chroot/usr/local/bin
mkdir -p /build/chroot/usr/local/lib/serein
mkdir -p /build/chroot/usr/local/share/serein/models
mkdir -p /build/chroot/usr/local/share/serein/ui
mkdir -p /build/chroot/etc/serein
mkdir -p /build/chroot/var/lib/serein
mkdir -p /build/chroot/var/log/serein
mkdir -p /build/chroot/etc/systemd/system
mkdir -p /build/chroot/etc/systemd/user
mkdir -p /build/chroot/home/serein/.config

# 1. Install Desktop UI Assets
echo "[INFO] Installing Serein Desktop UI assets to /usr/local/share/serein/ui/..."
if [ -d /project/assets/ui ]; then
    cp -r /project/assets/ui/* /build/chroot/usr/local/share/serein/ui/
fi

# 2. Install Serein Core
echo "[INFO] Installing serein-core..."
if [ -d /project/serein-core/src/serein_core ]; then
    cp -r /project/serein-core/src/serein_core /build/chroot/usr/local/lib/serein/
fi
if [ -d /project/serein-core/dist ]; then
    cp -r /project/serein-core/dist/* /build/chroot/usr/local/lib/serein/ 2>/dev/null || true
fi

cat > /build/chroot/usr/local/bin/serein-core << 'CORE_EOF'
#!/bin/bash
cd /usr/local/lib/serein
exec python3 -m serein_core "$@"
CORE_EOF
chmod +x /build/chroot/usr/local/bin/serein-core

# 3. Install Serein Voice
echo "[INFO] Installing serein-voice..."
mkdir -p /build/chroot/usr/local/lib/serein/serein-voice
if [ -d /project/serein-voice ]; then
    cp -r /project/serein-voice/* /build/chroot/usr/local/lib/serein/serein-voice/
fi
cat > /build/chroot/usr/local/bin/serein-voice << 'VOICE_EOF'
#!/bin/bash
exec python3 /usr/local/lib/serein/serein-voice/voice_engine.py "$@"
VOICE_EOF
chmod +x /build/chroot/usr/local/bin/serein-voice

# 3b. Install Serein Installer Modules & CLI Wrapper
echo "[INFO] Installing serein-installer modules to /usr/local/lib/serein/..."
if [ -d /project/serein-installer ]; then
    cp -r /project/serein-installer /build/chroot/usr/local/lib/serein/
fi
if [ -d /project/serein_installer ]; then
    cp -r /project/serein_installer /build/chroot/usr/local/lib/serein/
fi

cat > /build/chroot/usr/local/bin/serein-installer << 'INSTALLER_EOF'
#!/bin/bash
export PYTHONPATH=/usr/local/lib/serein:${PYTHONPATH:-}
exec python3 -m serein_installer.installer_engine "$@"
INSTALLER_EOF
chmod +x /build/chroot/usr/local/bin/serein-installer

# 4. Install Avatar Models
echo "[INFO] Installing avatar models..."
if [ -f /project/serein-avatar/Assets/Models/avatar.vrm ]; then
    cp /project/serein-avatar/Assets/Models/avatar.vrm /build/chroot/usr/local/share/serein/models/
fi
if [ -f /project/serein-avatar/Assets/Models/serein.vrm ]; then
    cp /project/serein-avatar/Assets/Models/serein.vrm /build/chroot/usr/local/share/serein/models/
fi
if [ -f /project/serein-avatar/Build/serein-avatar ]; then
    cp /project/serein-avatar/Build/serein-avatar /build/chroot/usr/local/bin/
    chmod +x /build/chroot/usr/local/bin/serein-avatar
fi

# 4b. Python module path configuration (.pth)
mkdir -p /build/chroot/usr/local/lib/python3.11/dist-packages
cat > /build/chroot/usr/local/lib/python3.11/dist-packages/serein.pth << 'PTH_EOF'
/usr/local/lib/serein
/usr/local/lib/serein/serein-voice
PTH_EOF

# 5. Serein Web Daemon
cat > /build/chroot/usr/local/bin/serein-web << 'WEB_EOF'
#!/bin/bash
export PYTHONPATH=/usr/local/lib/serein:/usr/local/lib/serein/serein-voice:${PYTHONPATH:-}
exec python3 -m uvicorn serein_core.web_app:app --host 127.0.0.1 --port 8080
WEB_EOF
chmod +x /build/chroot/usr/local/bin/serein-web

# 6. Serein Desktop Kiosk Launcher
cat > /build/chroot/usr/local/bin/serein-desktop << 'DESK_EOF'
#!/bin/bash
export PYTHONPATH=/usr/local/lib/serein:/usr/local/lib/serein/serein-voice:${PYTHONPATH:-}

# Ensure Serein Web API is running
if ! curl -s http://127.0.0.1:8080/health >/dev/null 2>&1; then
    python3 -m uvicorn serein_core.web_app:app --host 127.0.0.1 --port 8080 >/tmp/serein-web.log 2>&1 &
fi

# Wait for Serein Web API to be ready
for i in $(seq 1 60); do
    if curl -s http://127.0.0.1:8080/health >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# Launch Chromium in native Wayland standalone app kiosk mode (NO browser chrome, tabs, or address bar)
exec chromium \
    --ozone-platform=wayland \
    --enable-features=UseOzonePlatform \
    --app=http://127.0.0.1:8080 \
    --start-fullscreen \
    --kiosk \
    --window-position=0,0 \
    --hide-scrollbars \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --disable-pinch \
    --force-device-scale-factor=1.0 \
    --overscroll-history-navigation=0 \
    --disable-features=Translate,OptimizationHints \
    --autoplay-policy=no-user-gesture-required \
    --user-data-dir=/home/serein/.config/chromium-kiosk
DESK_EOF
chmod +x /build/chroot/usr/local/bin/serein-desktop

# 7. Serein Compositor Script
cat > /build/chroot/usr/local/bin/serein-compositor << 'COMP_EOF'
#!/bin/bash
exec weston --config=/home/serein/.config/weston.ini
COMP_EOF
chmod +x /build/chroot/usr/local/bin/serein-compositor

# 8. Weston Configuration for Kiosk Desktop Shell
cat > /build/chroot/home/serein/.config/weston.ini << 'WESTON_EOF'
[core]
idle-time=0
require-input=false

[shell]
locking=false
panel-position=none
background-color=0x0a0c14ff
animation=none
close-animation=none

[autolaunch]
path=/usr/local/bin/serein-desktop
WESTON_EOF

mkdir -p /build/chroot/etc/xdg/weston
cp /build/chroot/home/serein/.config/weston.ini /build/chroot/etc/xdg/weston/weston.ini

# 9. Autostart GUI on tty1 autologin
cat > /build/chroot/home/serein/.bash_profile << 'PROFILE_EOF'
if [ -z "$WAYLAND_DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    mkdir -p "$XDG_RUNTIME_DIR" && chmod 0700 "$XDG_RUNTIME_DIR"
    exec weston --config=/home/serein/.config/weston.ini
fi
PROFILE_EOF

chroot /build/chroot chown -R serein:serein /home/serein

# 10. Copy configuration and systemd units
if [ -f /project/serein-core/config/config.yaml ]; then
    cp /project/serein-core/config/config.yaml /build/chroot/etc/serein/
fi

cp /project/deploy/systemd/*.service /build/chroot/etc/systemd/system/ 2>/dev/null || true
cp /project/deploy/systemd/*.target /build/chroot/etc/systemd/system/ 2>/dev/null || true
cp /project/deploy/systemd/serein-core-user.service /build/chroot/etc/systemd/user/ 2>/dev/null || true

# 11. Python Dependencies inside chroot
echo "[INFO] Ensuring Python runtime dependencies in chroot..."
chroot /build/chroot pip3 install --break-system-packages --no-cache-dir \
    pydantic pydantic-settings python-dotenv pyyaml requests aiohttp aiofiles websockets numpy \
    loguru psutil cryptography croniter click rich fastapi uvicorn dbus-next

# 12. Enable systemd services
chroot /build/chroot systemctl enable serein-core.service 2>/dev/null || true
chroot /build/chroot systemctl enable serein-voice.service 2>/dev/null || true
chroot /build/chroot systemctl enable serein-web.service 2>/dev/null || true

# 12b. Install Plymouth Serein Glow Boot Splash Theme
echo "[INFO] Installing Plymouth serein-glow boot splash theme..."
mkdir -p /build/chroot/usr/share/plymouth/themes/serein-glow
if [ -d /project/serein-installer/plymouth/serein-glow ]; then
    cp -r /project/serein-installer/plymouth/serein-glow/* /build/chroot/usr/share/plymouth/themes/serein-glow/
    chroot /build/chroot plymouth-set-default-theme serein-glow 2>/dev/null || true
fi

# 13. Update initramfs for live-boot
echo "[INFO] Updating initramfs for live-boot..."
chroot /build/chroot update-initramfs -u -k all 2>/dev/null || true

echo "[SUCCESS] Artifacts, kiosk desktop shell, and autostart installed successfully!"
