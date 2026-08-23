#!/usr/bin/env bash
set -euo pipefail

# ─── swiggy-lyr installer ───────────────────────────────────────────────
# curl -sSL https://raw.githubusercontent.com/ishan-parihar/swiggy-lyr/main/install.sh | bash
#
# Installs swiggy-lyr globally using uv (preferred) or pipx/pip as fallback.
# ──────────────────────────────────────────────────────────────────────────

REPO="https://github.com/ishan-parihar/swiggy-lyr.git"
BIN="swiggy-lyr"
MIN_PYTHON_VERSION="3.11"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

info()  { echo -e "${GREEN}▸${NC} $*"; }
warn()  { echo -e "${YELLOW}▸${NC} $*"; }
err()   { echo -e "${RED}▸${NC} $*" >&2; }

check_python() {
    local py=""
    for cmd in python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$cmd" &>/dev/null; then
            if "$cmd" -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
                py="$cmd"; break
            fi
        fi
    done
    if [[ -z "$py" ]]; then
        err "Python ${MIN_PYTHON_VERSION}+ not found. Install it first: https://python.org"
        exit 1
    fi
    echo "$py"
}

install_with_uv() {
    info "Installing with uv (isolated tool environment)"
    uv tool install --force --python "$(check_python)" "${REPO_GIT:-git+$REPO}"
}

install_with_pipx() {
    info "Installing with pipx"
    pipx install --python "$(check_python)" "git+${REPO}"
}

install_with_pip() {
    local py; py="$(check_python)"
    info "Installing with ${py} -m pip --user"
    "$py" -m pip install --user --upgrade "git+${REPO}"
    local bin_dir
    bin_dir="$("$py" -m site --user-base)/bin"
    export PATH="${bin_dir}:${PATH}"
}

main() {
    info "swiggy-lyr installer"
    if command -v uv &>/dev/null; then
        install_with_uv || warn "uv install failed, trying pipx"
        command -v "$BIN" &>/dev/null && { finish; return; }
    fi
    if command -v pipx &>/dev/null; then
        install_with_pipx || warn "pipx install failed, trying pip --user"
        command -v "$BIN" &>/dev/null && { finish; return; }
    fi
    install_with_pip
    finish
}

finish() {
    if ! command -v "$BIN" &>/dev/null; then
        err "Installed but '$BIN' is not on PATH. Add your tool/user bin dir to PATH."
        exit 1
    fi
    echo
    info "$("$BIN" --version) installed ✓"
    echo
    info "Next steps:"
    echo -e "  ${BLUE}1.${NC} $BIN --login     # OAuth consent via browser"
    echo -e "  ${BLUE}2.${NC} $BIN --status    # verify auth"
    echo -e "  ${BLUE}3.${NC} Point your MCP client at:"
    echo '       {"mcpServers": {"swiggy": {"command": "swiggy-lyr"}}}'
}

REPO_GIT=""
main
