#!/bin/bash
# Voice Binary Detection and Installation Script for SereinOS
# Detects and installs required voice binaries (TTS/STT)

set -euo pipefail

log_info() { echo -e "\033[0;34m[INFO]\033[0m $*"; }
log_success() { echo -e "\033[0;32m[SUCCESS]\033[0m $*"; }
log_warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $*"; }

BIN_DIR="/usr/local/bin"
LIB_DIR="/usr/local/lib/serein"

create_dirs() {
    sudo mkdir -p "$BIN_DIR" "$LIB_DIR"
}

# Check if command exists
has_cmd() { command -v "$1" >/dev/null 2>&1; }

# Install Azure Speech SDK (for Linux)
install_azure_speech() {
    if has_cmd dotnet; then
        log_info "Installing Azure Speech SDK via NuGet..."
        # Azure Speech SDK is typically used via .NET or Python
        pip3 install --no-cache-dir azure-cognitiveservices-speech || true
    else
        log_warn "dotnet not found, skipping Azure Speech SDK"
    fi
}

# Install Piper TTS
install_piper() {
    if has_cmd piper; then
        log_success "Piper TTS already installed"
        return 0
    fi
    
    log_info "Installing Piper TTS..."
    
    # Try to install from GitHub releases
    local arch=$(uname -m)
    local piper_url=""
    
    case "$arch" in
        x86_64) piper_url="https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz" ;;
        aarch64) piper_url="https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz" ;;
        armv7l) piper_url="https://github.com/rhasspy/piper/releases/latest/download/piper_linux_armv7l.tar.gz" ;;
        *) log_warn "Unsupported architecture for Piper: $arch"; return 1 ;;
    esac
    
    cd /tmp
    wget -q "$piper_url" -O piper.tar.gz || { log_error "Failed to download Piper"; return 1; }
    tar -xzf piper.tar.gz
    sudo cp piper/piper "$BIN_DIR/"
    sudo cp piper/*.so "$LIB_DIR/" 2>/dev/null || true
    rm -rf piper piper.tar.gz
    
    log_success "Piper TTS installed"
}

# Install whisper.cpp
install_whisper() {
    if has_cmd whisper-cli; then
        log_success "whisper.cpp already installed"
        return 0
    fi
    
    log_info "Installing whisper.cpp..."
    
    cd /tmp
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git || { log_error "Failed to clone whisper.cpp"; return 1; }
    cd whisper.cpp
    
    # Build
    make -j$(nproc) || { log_error "Failed to build whisper.cpp"; return 1; }
    
    # Install
    sudo cp main "$BIN_DIR/whisper-cli"
    sudo cp *.so "$LIB_DIR/" 2>/dev/null || true
    
    # Download default model
    log_info "Downloading base model..."
    ./models/download-ggml-model.sh base.en || true
    sudo cp models/ggml-base.en.bin "$LIB_DIR/"
    
    cd /tmp
    rm -rf whisper.cpp
    
    log_success "whisper.cpp installed"
}

# Install HeadTTS (if available as binary)
install_headtts() {
    if has_cmd headtts; then
        log_success "HeadTTS already installed"
        return 0
    fi
    
    log_info "Installing HeadTTS..."
    
    # HeadTTS typically runs as a server - install via pip if available
    pip3 install --no-cache-dir headtts || log_warn "HeadTTS not available via pip, may need manual install"
    
    log_success "HeadTTS install attempted"
}

# Install system TTS (espeak-ng as fallback)
install_system_tts() {
    if has_cmd espeak-ng; then
        log_success "espeak-ng already installed"
        return 0
    fi
    
    log_info "Installing espeak-ng (system TTS fallback)..."
    
    apt-get update && apt-get install -y espeak-ng || {
        log_error "Failed to install espeak-ng"
        return 1
    }
    
    log_success "espeak-ng installed"
}

# Install openWakeWord (for wake word detection)
install_openwakeword() {
    log_info "Installing openWakeWord..."
    
    pip3 install --no-cache-dir openwakeword || log_warn "openWakeWord install failed"
    
    log_success "openWakeWord install attempted"
}

# Main install flow
main() {
    log_info "Starting voice binary installation..."
    
    create_dirs
    
    # System TTS (always available)
    install_system_tts
    
    # High-quality TTS
    install_azure_speech
    install_piper
    install_headtts
    
    # STT
    install_whisper
    
    # Wake word
    install_openwakeword
    
    log_success "Voice binary installation complete!"
    log_info "Available binaries:"
    for cmd in espeak-ng piper whisper-cli headtts; do
        if has_cmd "$cmd"; then
            echo "  ✓ $cmd"
        else
            echo "  ✗ $cmd (not found)"
        fi
    done
}

# Parse args
case "${1:-install}" in
    install) main ;;
    check)
        create_dirs
        for cmd in espeak-ng piper whisper-cli headtts; do
            if has_cmd "$cmd"; then
                echo "✓ $cmd"
            else
                echo "✗ $cmd"
            fi
        done
        ;;
    *) log_error "Usage: $0 [install|check]"; exit 1 ;;
esac