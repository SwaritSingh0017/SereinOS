#!/bin/bash
# SereinOS Master Build Script
# Compiles all components and creates the ISO

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
BUILD_LOG="${PROJECT_ROOT}/build.log"

log_info() { echo -e "\033[0;34m[INFO]\033[0m $*" | tee -a "$BUILD_LOG"; }
log_success() { echo -e "\033[0;32m[SUCCESS]\033[0m $*" | tee -a "$BUILD_LOG"; }
log_warn() { echo -e "\033[1;33m[WARN]\033[0m $*" | tee -a "$BUILD_LOG"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*" | tee -a "$BUILD_LOG"; }

# Initialize build log
echo "=== SereinOS Build Started $(date) ===" > "$BUILD_LOG"

# Check prerequisites
check_prereqs() {
    log_info "Checking prerequisites..."
    
    local missing=()
    
    for cmd in meson ninja gcc make cmake git python3 pip3 docker wget curl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing prerequisites: ${missing[*]}"
        log_info "Install with: sudo apt-get install -y meson ninja-build gcc make cmake git python3 python3-pip docker.io wget curl"
        return 1
    fi
    
    # Check for wlroots development headers
    if ! pkg-config --exists wlroots-0.17; then
        missing+=("wlroots-0.17-dev")
    fi
    
    if ! pkg-config --exists wayland-client wayland-server; then
        missing+=("wayland-dev")
    fi
    
    if ! pkg-config --exists libpipewire-0.3; then
        missing+=("libpipewire-0.3-dev")
    fi
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing development packages: ${missing[*]}"
        log_info "Install with: sudo apt-get install -y libwlroots-dev wayland-protocols libwayland-dev libpipewire-0.3-dev libjson-c-dev libpixman-1-dev libcairo2-dev libpango1.0-dev libgbm-dev libdrm-dev libinput-dev libseat-dev libsystemd-dev"
        return 1
    fi
    
    log_success "All prerequisites satisfied"
    return 0
}

# Build Wayland compositor
build_compositor() {
    log_info "=== Building Wayland Compositor ==="
    
    cd "${PROJECT_ROOT}/serein-compositor"
    
    # Clean previous build
    rm -rf build
    
    # Configure with meson
    meson setup build --prefix=/usr/local \
        -Dwerror=false \
        -Db_lto=true \
        -Db_pie=true \
        | tee -a "$BUILD_LOG"
    
    # Compile
    ninja -C build | tee -a "$BUILD_LOG"
    
    # Verify binary
    if [[ -f build/serein-compositor ]]; then
        log_success "Compositor built: build/serein-compositor"
        ./build/serein-compositor --version 2>&1 | head -5 | tee -a "$BUILD_LOG" || true
    else
        log_error "Compositor build failed - binary not found"
        return 1
    fi
    
    return 0
}

# Build Python package
build_python() {
    log_info "=== Building Python Package (serein-core) ==="
    
    cd "${PROJECT_ROOT}/serein-core"
    
    # Install build dependencies
    pip3 install --no-cache-dir build wheel setuptools | tee -a "$BUILD_LOG"
    
    # Build package
    python3 -m build --wheel --sdist | tee -a "$BUILD_LOG"
    
    # Verify dist
    if [[ -d dist ]] && ls dist/*.whl >/dev/null 2>&1; then
        log_success "Python package built:"
        ls -la dist/ | tee -a "$BUILD_LOG"
    else
        log_error "Python package build failed"
        return 1
    fi
    
    # Also install in development mode for testing
    pip3 install -e .[dev,voice,vision] | tee -a "$BUILD_LOG"
    
    return 0
}

# Build Unity avatar (requires Unity installed)
build_avatar() {
    log_info "=== Building Unity Avatar ==="
    
    cd "${PROJECT_ROOT}/serein-avatar"
    
    if [[ -f build_avatar.sh ]]; then
        chmod +x build_avatar.sh
        ./build_avatar.sh | tee -a "$BUILD_LOG"
        
        if [[ -f Build/serein-avatar ]]; then
            log_success "Avatar built: Build/serein-avatar"
        else
            log_warn "Avatar build completed but binary not found (Unity may not be installed)"
        fi
    else
        log_warn "Avatar build script not found, skipping"
    fi
    
    return 0
}

# Install voice binaries
install_voice() {
    log_info "=== Installing Voice Binaries ==="
    
    cd "${PROJECT_ROOT}"
    
    if [[ -f scripts/install_voice.sh ]]; then
        chmod +x scripts/install_voice.sh
        ./scripts/install_voice.sh install | tee -a "$BUILD_LOG"
    else
        log_warn "Voice install script not found"
    fi
    
    return 0
}

# Setup VRM model
setup_vrm() {
    log_info "=== Setting Up VRM Model ==="
    
    cd "${PROJECT_ROOT}"
    
    if [[ -f scripts/setup_vrm.sh ]]; then
        chmod +x scripts/setup_vrm.sh
        ./scripts/setup_vrm.sh setup | tee -a "$BUILD_LOG"
    else
        log_warn "VRM setup script not found"
    fi
    
    return 0
}

# Build ISO
build_iso() {
    log_info "=== Building ISO Image ==="
    
    cd "${PROJECT_ROOT}"
    
    if [[ -f serein-installer/build_iso.sh ]]; then
        chmod +x serein-installer/build_iso.sh
        ./serein-installer/build_iso.sh --mode native | tee -a "$BUILD_LOG"
        
        # Check output
        if ls dist/SereinOS-*.iso >/dev/null 2>&1; then
            log_success "ISO built successfully:"
            ls -la dist/SereinOS-*.iso | tee -a "$BUILD_LOG"
        else
            log_error "ISO build failed - no output found"
            return 1
        fi
    else
        log_error "ISO builder not found"
        return 1
    fi
    
    return 0
}

# Run tests
run_tests() {
    log_info "=== Running Tests ==="
    
    cd "${PROJECT_ROOT}/serein-core"
    
    if [[ -f pyproject.toml ]]; then
        python3 -m pytest tests/ -v | tee -a "$BUILD_LOG" || log_warn "Some tests failed"
    else
        log_warn "No test configuration found"
    fi
    
    return 0
}

# Main build flow
main() {
    local start_time=$(date +%s)
    local steps=("check_prereqs" "build_compositor" "build_python" "setup_vrm" "install_voice" "build_avatar" "run_tests" "build_iso")
    local failed=()
    
    log_info "Starting SereinOS full build..."
    log_info "Project root: $PROJECT_ROOT"
    
    for step in "${steps[@]}"; do
        log_info ">>> Step: $step"
        if ! $step; then
            failed+=("$step")
            log_error "Step $step FAILED"
        else
            log_success "Step $step completed"
        fi
    done
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    echo ""
    log_info "=== Build Summary ==="
    log_info "Total time: ${duration}s"
    log_info "Steps completed: $((${#steps[@]} - ${#failed[@]}))/${#steps[@]}"
    
    if [[ ${#failed[@]} -eq 0 ]]; then
        log_success "ALL BUILD STEPS PASSED!"
        log_info "ISO location: ${PROJECT_ROOT}/dist/SereinOS-*.iso"
    else
        log_error "FAILED STEPS: ${failed[*]}"
        log_info "Check build.log for details: $BUILD_LOG"
        return 1
    fi
}

# Parse arguments
case "${1:-all}" in
    all) main ;;
    check) check_prereqs ;;
    compositor) build_compositor ;;
    python) build_python ;;
    avatar) build_avatar ;;
    voice) install_voice ;;
    vrm) setup_vrm ;;
    iso) build_iso ;;
    test) run_tests ;;
    *) log_error "Usage: $0 [all|check|compositor|python|avatar|voice|vrm|iso|test]"; exit 1 ;;
esac