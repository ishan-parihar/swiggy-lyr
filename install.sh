#!/usr/bin/env bash
set -euo pipefail

# ─── swiggy-lyr installer ───────────────────────────────────────────────
# curl -sSL https://raw.githubusercontent.com/ishan-parihar/swiggy-lyr/main/install.sh | bash
#
# Designed for bare machines: bootstraps uv if missing (uv then provisions
# its own Python), installs the CLI as an isolated tool, verifies the entry
# point, and registers the agent skill. Fallbacks: pipx, then pip --user.
# ──────────────────────────────────────────────────────────────────────────

REPO="https://github.com/ishan-parihar/swiggy-lyr.git"
PKG="swiggy-lyr"
BIN="swiggy-lyr"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

info()  { echo -e "${GREEN}▸${NC} $*"; }
warn()  { echo -e "${YELLOW}▸${NC} $*"; }
err()   { echo -e "${RED}▸${NC} $*" >&2; }
step()  { echo -e "${BLUE}▸${NC} $*"; }

# ── uv bootstrap: the only true prerequisite ─────────────────────────────
ensure_uv() {
    if command -v uv &>/dev/null; then
        info "uv $(uv --version | awk '{print $2}') found"
        return 0
    fi
    step "uv not found — bootstrapping via astral.sh installer"
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        export PATH="$HOME/.local/bin:$PATH"
        command -v uv &>/dev/null && { info "uv installed"; return 0; }
    fi
    return 1
}

install_with_uv() {
    step "Installing ${PKG} as an isolated uv tool (from ${REPO})"
    uv tool install --force "git+${REPO}"
}

install_with_pipx() {
    step "Installing with pipx"
    pipx install "git+${REPO}"
}

install_with_pip() {
    local py=""
    for cmd in python3 python3.13 python3.12 python3.11; do
        command -v "$cmd" &>/dev/null && py="$cmd" && break
    done
    if [[ -z "$py" ]]; then
        err "No python3 found and uv/pipx unavailable. Install Python 3.11+ first."
        return 1
    fi
    step "Installing with ${py} -m pip --user"
    "$py" -m pip install --user --upgrade "git+${REPO}" || return 1
    local bin_dir
    bin_dir="$("$py" -m site --user-base)/bin"
    export PATH="${bin_dir}:${PATH}"
}

# ── agent skill registration (family convention) ─────────────────────────
install_skills() {
    step "Registering agent skill"
    local skill_dir="$HOME/.agents/skills/swiggy-mcp"
    if [[ -f "SKILL.md" ]]; then
        mkdir -p "$skill_dir" && cp SKILL.md "$skill_dir/"
    else
        # curl|bash runs outside the repo — fetch the skill file directly
        mkdir -p "$skill_dir" \
            && curl -sSL "https://raw.githubusercontent.com/ishan-parihar/swiggy-lyr/main/SKILL.md" \
                -o "$skill_dir/SKILL.md" 2>/dev/null \
            || { warn "Could not fetch SKILL.md — skill not registered"; return 0; }
    fi
    info "Skill installed → ${skill_dir}/SKILL.md"
}

verify() {
    if ! command -v "$BIN" &>/dev/null; then
        err "Installed but '$BIN' is not on PATH."
        err "Add ~/.local/bin to PATH:  export PATH=\"\$HOME/.local/bin:\$PATH\""
        exit 1
    fi
    info "$("$BIN" --version) ✓"
}

finish() {
    verify
    install_skills
    echo
    info "Next steps:"
    echo -e "  ${BLUE}1.${NC} ${BIN} --login      # browser OAuth consent (~5 day token)"
    echo -e "  ${BLUE}2.${NC} ${BIN} --status     # verify auth"
    echo -e "  ${BLUE}3.${NC} Point your MCP client at:"
    echo '         {"mcpServers": {"swiggy": {"command": "swiggy-lyr"}}}'
    echo
    info "Upgrade anytime:  uv tool upgrade ${PKG}"
}

main() {
    info "swiggy-lyr installer"
    echo

    if ensure_uv; then
        if install_with_uv; then finish; exit 0; fi
        warn "uv install failed — trying pipx"
    fi

    if command -v pipx &>/dev/null; then
        if install_with_pipx; then finish; exit 0; fi
        warn "pipx install failed — trying pip --user"
    fi

    if install_with_pip; then finish; exit 0; fi

    err "All install paths failed. Try manually:"
    err "  git clone ${REPO} && cd swiggy-lyr && uv sync"
    exit 1
}

main "$@"
