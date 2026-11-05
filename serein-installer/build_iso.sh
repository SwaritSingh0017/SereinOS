#!/bin/bash
# SereinOS ISO Builder - Cross-platform
# Builds a bootable ISO with SereinOS pre-installed
# Supports: Linux (native), Windows (via Docker/WSL2), macOS (via Docker)

set -euo pipefail
export MSYS_NO_PATHCONV=1
export PATH="/c/Users/sayam/AppData/Local/Programs/DockerDesktop/resources/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"
OUTPUT_DIR="${PROJECT_ROOT}/dist"
BUILD_VOLUME="serein-build-vol"

# Configuration
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-amd64}"
BASE_DISTRO="${BASE_DISTRO:-debian}"
BASE_VERSION="${BASE_VERSION:-bookworm}"
BUILD_MODE="${BUILD_MODE:-auto}"
ISO_NAME="SereinOS-${VERSION}-$(date +%Y%m%d)-x86_64.iso"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Cross-platform detection
detect_platform() {
    case "$(uname -s)" in
        Linux*)     PLATFORM="linux" ;;
        Darwin*)    PLATFORM="macos" ;;
        CYGWIN*|MINGW*|MSYS*) PLATFORM="windows" ;;
        *)          PLATFORM="unknown" ;;
    esac
    
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] || [[ -n "${WSL_INTEROP:-}" ]]; then
        PLATFORM="wsl2"
    fi
}

# Convert path for Docker volume mounting (-v <host_path>:<container_path>)
get_docker_host_path() {
    local path="$1"
    if [[ "$PLATFORM" == "windows" ]]; then
        if command -v cygpath >/dev/null 2>&1; then
            path=$(cygpath -m "$path")
        fi
    fi
    echo "$path"
}

# Docker detection
check_docker() {
    command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

# WSL2 detection
check_wsl2() {
    [[ "$PLATFORM" == "wsl2" ]] || [[ -n "${WSL_DISTRO_NAME:-}" ]] || [[ -n "${WSL_INTEROP:-}" ]]
}

# Auto-detect best build mode
detect_build_mode() {
    if check_docker; then
        BUILD_MODE="docker"
        log_info "Docker detected - using Docker build mode"
    elif check_wsl2; then
        BUILD_MODE="wsl2"
        log_info "WSL2 detected - using WSL2 build mode"
    elif [[ "$PLATFORM" == "linux" ]]; then
        BUILD_MODE="native"
        log_info "Native Linux detected - using native build mode"
    else
        log_error "Unsupported platform for native build. Use Docker mode."
        exit 1
    fi
}

cleanup() {
    log_info "Cleaning up..."
    if [[ "$BUILD_MODE" == "docker" ]]; then
        docker ps -a --filter "label=serein-build" -q | xargs -r docker rm -f 2>/dev/null || true
    fi
}

trap cleanup EXIT

setup_build_dirs() {
    log_info "Setting up build environment..."
    mkdir -p "${OUTPUT_DIR}"
    
    if [[ "$BUILD_MODE" == "docker" ]]; then
        log_info "Ensuring native ext4 Docker volume '${BUILD_VOLUME}' exists..."
        docker volume create "${BUILD_VOLUME}" >/dev/null 2>&1 || true
    fi
}

docker_bootstrap() {
    log_info "Bootstrapping Debian bookworm base system inside Docker volume..."
    
    # Run debootstrap inside container onto native ext4 volume
    docker run --rm --privileged \
        --label serein-build \
        -v "${BUILD_VOLUME}:/build" \
        serein-builder:latest \
        /bin/bash -c "
            set -euo pipefail
            mkdir -p /build/chroot
            mkdir -p /build/iso/live
            mkdir -p /build/iso/boot/grub/i386-pc
            mkdir -p /build/iso/boot/efi/boot
            
            if [[ ! -d /build/chroot/etc ]]; then
                echo '[INFO] Running debootstrap minimal base for Debian bookworm...'
                debootstrap --arch=\"${ARCH}\" \
                    --components=main,contrib,non-free,non-free-firmware \
                    \"${BASE_VERSION}\" /build/chroot \
                    http://deb.debian.org/debian/
            else
                echo '[INFO] Base system already bootstrapped at /build/chroot'
            fi
        "
}

docker_configure_chroot() {
    log_info "Configuring chroot and installing system packages..."
    
    docker run --rm --privileged \
        --label serein-build \
        -v "${BUILD_VOLUME}:/build" \
        -v "$(get_docker_host_path "${PROJECT_ROOT}"):/project:ro" \
        serein-builder:latest \
        /bin/bash -c "
            set -euo pipefail
            
            # Create setup script inside chroot
            cat > /build/chroot/root/setup.sh << 'SETUP_EOF'
#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo '[INFO] Updating package indices...'
apt-get update
apt-get upgrade -y

echo '[INFO] Installing Linux kernel, live-boot, and base system...'
apt-get install -y --no-install-recommends \
    linux-image-amd64 live-boot live-boot-initramfs-tools live-config live-config-systemd \
    systemd systemd-sysv polkitd \
    ca-certificates curl wget gnupg2 software-properties-common \
    network-manager network-manager-gnome

echo '[INFO] Installing Wayland display and graphics packages...'
apt-get install -y --no-install-recommends \
    wayland-protocols wayland-utils weston swaybg swaylock swayidle \
    mesa-vulkan-drivers libgl1-mesa-dri libegl1-mesa libgles2-mesa \
    mesa-va-drivers mesa-vdpau-drivers vainfo mesa-utils \
    firmware-amd-graphics firmware-atheros firmware-realtek firmware-misc-nonfree amd64-microcode \
    alsa-utils pipewire-alsa \
    brightnessctl upower acpid \
    parted gdisk fdisk dosfstools e2fsprogs squashfs-tools efibootmgr rsync \
    plymouth plymouth-themes plymouth-label \
    dbus pipewire pipewire-pulse wireplumber libinput-tools \
    fonts-noto fonts-noto-color-emoji fonts-dejavu-core \
    htop git jq neovim sudo grub-pc-bin grub-efi-amd64-bin

echo '[INFO] Installing Python & build libraries...'
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    build-essential cmake meson ninja-build pkg-config \
    libssl-dev libffi-dev libsqlite3-dev libjson-c-dev \
    libwayland-dev libwlroots-dev libgbm-dev libdrm-dev \
    libpipewire-0.3-dev libpixman-1-dev libcairo2-dev libpango1.0-dev

echo '[INFO] Configuring system services...'
systemctl enable NetworkManager 2>/dev/null || true
systemctl enable dbus 2>/dev/null || true

echo '[INFO] Creating default serein user and permissions...'
if ! id -u serein >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo,video,audio,render,input serein
    # Do NOT set a hardcoded default password. Account is locked until installer prompts user.
    passwd -l serein 2>/dev/null || true
fi

mkdir -p /etc/sudoers.d
# Restrict sudo: Require standard authentication, zero unrestricted NOPASSWD privileges
echo '%sudo ALL=(ALL:ALL) ALL' > /etc/sudoers.d/serein
chmod 0440 /etc/sudoers.d/serein

mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/override.conf << 'CONF_EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin serein --noclear %I \$TERM
CONF_EOF

systemctl disable gdm3 2>/dev/null || true
systemctl disable lightdm 2>/dev/null || true
systemctl disable sddm 2>/dev/null || true

if [ -f /etc/default/grub ]; then
    sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT=\"quiet\"/GRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash loglevel=3 systemd.show_status=0\"/' /etc/default/grub || true
    sed -i 's/GRUB_TIMEOUT=5/GRUB_TIMEOUT=0/' /etc/default/grub || true
fi

if [ -d /project/serein-installer/plymouth/serein-glow ]; then
    mkdir -p /usr/share/plymouth/themes/serein-glow
    cp -r /project/serein-installer/plymouth/serein-glow/* /usr/share/plymouth/themes/serein-glow/
fi

if [ -d /usr/share/plymouth/themes/serein-glow ]; then
    plymouth-set-default-theme serein-glow 2>/dev/null || true
fi

echo '[INFO] Cleaning up apt cache...'
apt-get autoremove -y
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /root/.cache
SETUP_EOF

            chmod +x /build/chroot/root/setup.sh
            
            mount --bind /dev /build/chroot/dev
            mount --bind /proc /build/chroot/proc
            mount --bind /sys /build/chroot/sys
            mount --bind /run /build/chroot/run
            cp /etc/resolv.conf /build/chroot/etc/resolv.conf 2>/dev/null || true
            
            chroot /build/chroot /root/setup.sh
            
            umount /build/chroot/dev 2>/dev/null || true
            umount /build/chroot/proc 2>/dev/null || true
            umount /build/chroot/sys 2>/dev/null || true
            umount /build/chroot/run 2>/dev/null || true
        "
}

docker_install_artifacts() {
    log_info "Installing SereinOS modules and artifacts..."
    
    docker run --rm --privileged \
        --label serein-build \
        -v "${BUILD_VOLUME}:/build" \
        -v "$(get_docker_host_path "${PROJECT_ROOT}"):/project:ro" \
        -v "$(get_docker_host_path "${OUTPUT_DIR}"):/output" \
        serein-builder:latest \
        /bin/bash /project/serein-installer/install_artifacts_container.sh
}

build_iso() {
    log_info "Building Dual-Mode Hybrid (Legacy BIOS + UEFI) ISO image: ${ISO_NAME}..."
    
    # 1. Kernel and initrd copy, GRUB configuration
    log_info "Staging kernel, initramfs, and GRUB configuration..."
    docker run --rm --privileged \
        --label serein-build \
        -v "${BUILD_VOLUME}:/build" \
        serein-builder:latest \
        /bin/bash -c "
            set -euo pipefail
            mkdir -p /build/iso/live
            mkdir -p /build/iso/boot/grub
            
            LATEST_VMLINUZ=\$(ls -t /build/chroot/boot/vmlinuz-* 2>/dev/null | head -1)
            LATEST_INITRD=\$(ls -t /build/chroot/boot/initrd.img-* 2>/dev/null | head -1)
            
            if [[ -z \"\$LATEST_VMLINUZ\" || -z \"\$LATEST_INITRD\" ]]; then
                echo '[ERROR] Kernel or initrd not found in /build/chroot/boot!'
                exit 1
            fi
            
            echo \"[INFO] Using kernel: \$LATEST_VMLINUZ\"
            echo \"[INFO] Using initrd: \$LATEST_INITRD\"
            cp \"\$LATEST_VMLINUZ\" /build/iso/vmlinuz
            cp \"\$LATEST_INITRD\" /build/iso/initrd.img
            
            cat > /build/iso/boot/grub/grub.cfg << 'GRUB_EOF'
set default=0
set timeout=5
set gfxmode=auto
insmod all_video
insmod png
insmod gfxterm
set gfxpayload=keep
terminal_output gfxterm

menuentry \"SereinOS Live\" --class serein --class gnu-linux --class gnu --class os {
    linux /vmlinuz boot=live components quiet splash
    initrd /initrd.img
}

menuentry \"SereinOS Live (Safe Graphics)\" --class serein --class gnu-linux --class gnu --class os {
    linux /vmlinuz boot=live components nomodeset
    initrd /initrd.img
}

menuentry \"SereinOS (Debug Console / Recovery)\" --class serein --class gnu-linux --class gnu --class os {
    linux /vmlinuz boot=live components single systemd.journald.forward_to_console=1
    initrd /initrd.img
}
GRUB_EOF
        "
        
    # 2. Build compressed squashfs filesystem
    log_info "Building fresh compressed squashfs filesystem..."
    docker run --rm --privileged \
        --label serein-build \
        -v "${BUILD_VOLUME}:/build" \
        serein-builder:latest \
        /bin/bash -c "
            rm -f /build/iso/live/filesystem.squashfs
            echo '[INFO] Creating new filesystem.squashfs from /build/chroot...'
            mksquashfs /build/chroot /build/iso/live/filesystem.squashfs \
                -comp xz -b 1M -noappend -e boot
        "

    # 3. Pack dual-mode hybrid ISO using grub-mkrescue
    log_info "Packing dual-mode hybrid Legacy BIOS + UEFI ISO using grub-mkrescue..."
    docker run --rm --privileged \
        --label serein-build \
        -v "${BUILD_VOLUME}:/build" \
        -v "$(get_docker_host_path "${OUTPUT_DIR}"):/output" \
        serein-builder:latest \
        /bin/bash -c "
            set -euo pipefail
            rm -f \"/build/${ISO_NAME}\"
            grub-mkrescue -o \"/build/${ISO_NAME}\" /build/iso -- -volid \"SEREIN_OS\"
            cp -f \"/build/${ISO_NAME}\" \"/output/${ISO_NAME}\"
        "

    log_success "ISO created successfully at $(get_docker_host_path "${OUTPUT_DIR}")/${ISO_NAME}!"
    
    # 4. Generate SHA256 checksum
    docker run --rm \
        -v "$(get_docker_host_path "${OUTPUT_DIR}"):/output" \
        alpine:latest \
        sh -c "cd /output && sha256sum ${ISO_NAME} > ${ISO_NAME}.sha256"
        
    log_success "SHA256 Checksum generated: $(cat "${OUTPUT_DIR}/${ISO_NAME}.sha256")"
}

# Main flow
main() {
    detect_platform
    log_info "Platform: $PLATFORM | Build Mode: ${BUILD_MODE}"
    
    if [[ "${BUILD_MODE}" == "auto" ]]; then
        detect_build_mode
    fi
    
    log_info "Starting SereinOS ISO build..."
    log_info "Version: ${VERSION}"
    log_info "Architecture: ${ARCH}"
    log_info "Base: ${BASE_DISTRO} ${BASE_VERSION}"
    
    setup_build_dirs
    
    if [[ "${ISO_ONLY:-false}" == "true" ]]; then
        log_info "Skipping bootstrap and artifact installation (--iso-only requested)..."
    elif [[ "${ARTIFACTS_AND_ISO:-false}" == "true" ]]; then
        log_info "Running artifact installation and ISO build..."
        docker_install_artifacts
    else
        case "${BUILD_MODE}" in
            docker)
                docker_bootstrap
                docker_configure_chroot
                docker_install_artifacts
                ;;
            *)
                docker_bootstrap
                docker_configure_chroot
                docker_install_artifacts
                ;;
        esac
    fi
    
    build_iso
    
    log_success "Build complete!"
    log_info "Bootable ISO ready at: ${OUTPUT_DIR}/${ISO_NAME}"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --version) VERSION="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --base-distro) BASE_DISTRO="$2"; shift 2 ;;
        --base-version) BASE_VERSION="$2"; shift 2 ;;
        --mode) BUILD_MODE="$2"; shift 2 ;;
        --iso-only)
            ISO_ONLY=true
            shift 1
            ;;
        --artifacts-and-iso)
            ARTIFACTS_AND_ISO=true
            shift 1
            ;;
        --clean)
            docker volume rm -f "${BUILD_VOLUME}" 2>/dev/null || true
            shift 1
            ;;
        --help)
            echo "Usage: ./build_iso.sh [--version VER] [--arch ARCH] [--mode docker] [--iso-only] [--clean]"
            exit 0
            ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

main "$@"
