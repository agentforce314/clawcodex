#!/usr/bin/env bash
# ============================================================================
#  install.sh — One-click installer for clawcodex
# ----------------------------------------------------------------------------
#  Run it with one line (no clone needed):
#
#      curl -fsSL https://clawcodex.app/install.sh | bash
#
#  To pass flags through the pipe, use bash -s --:
#
#      curl -fsSL https://clawcodex.app/install.sh | bash -s -- --dry-run
#
#  What it does:
#    - OS detection (Linux / macOS / WSL / Git Bash)
#    - Git prerequisite check
#    - uv installation (no sudo, via the official astral.sh installer)
#    - Python 3.10+ provisioning (via uv)
#    - Repo clone/update to ~/.clawcodex/clawcodex
#    - Venv creation (uv-managed) + dependency install (lock-pinned via uv.lock)
#    - Global command: ~/.local/bin/clawcodex
#    - Shell rc patch: .bashrc / .zshrc / .profile  (PATH += ~/.local/bin)
#
#  Subcommands (use exactly one, or omit for default 'install'):
#     install.sh                # install (default)
#     install.sh status         # show current install state
#     install.sh doctor         # diagnose the environment
#     install.sh verify         # health-check an existing install
#     install.sh update         # pull latest + reinstall deps
#     install.sh uninstall      # remove everything this script created
#     install.sh help           # show usage
#
#  Agent-friendly features:
#     - Subcommands (status / doctor / verify) for inspection without side effects
#     - --dry-run             preview every change before applying
#     - --yes / -y            assume yes for any prompts
#     - --log-file <path>     tee all output to a log file
#     - [install.sh] prefix on every line when stdout is not a TTY
#     - "DONE: success|FAILED" summary line on exit (grep-friendly)
#     - Each die() includes a "Next steps" block with actionable fixes
# ----------------------------------------------------------------------------
set -euo pipefail
# ERR trap: if a command fails under set -e, print the line number and
# failing command before exit.  Makes headless / TTY / CI failures
# self-diagnosing without requiring "bash -x".
set -E
trap 'log_err "Installer crash at line $LINENO: $BASH_COMMAND"' ERR

# ============================================================================
#  Config (read-only defaults)
# ============================================================================
readonly INSTALLER_VERSION="1.1.0"
# REPO_REF is intentionally NOT readonly — it gets reassigned when the user
# passes --ref. We have no version tags, so the default is the main branch;
# --ref is the escape hatch for installing a specific commit/tag/branch.
REPO_REF="${CLAWCODEX_REF:-main}"
readonly REPO_URL="https://github.com/agentforce314/clawcodex"
# Install dir = where the project source is cloned and (by default) the .venv
# lives. Runtime config (config.json, skills/, sessions/) is written by the CLI
# to ~/.clawcodex (the parent), so everything lives under one tree.
readonly DEFAULT_INSTALL_DIR="$HOME/.clawcodex/clawcodex"
readonly LOCAL_BIN="$HOME/.local/bin"
readonly PYTHON_MIN_VERSION="3.10"
readonly ENTRY_POINT="clawcodex"   # the single registered entry in pyproject.toml
readonly RC_MARKER="# clawcodex installer — managed by install.sh"
# Node is needed to run the Ink TUI (`clawcodex tui`). Use an existing node if
# present; otherwise the official Node binary is fetched (no sudo) into here.
readonly NODE_VERSION="${CLAWCODEX_NODE_VERSION:-v22.12.0}"
readonly NODE_DIR="$HOME/.clawcodex/node"

# How to refer to "this installer" in user-facing hints. When run as a file,
# that's the script path; when piped (curl | bash) there is no file, so $0 is
# "bash" — point users at the canonical one-liner instead so copy/paste works.
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
    readonly SELF_CMD="bash ${BASH_SOURCE[0]}"
else
    readonly SELF_CMD="curl -fsSL https://clawcodex.app/install.sh | bash -s --"
fi

# ============================================================================
#  UI helpers
# ============================================================================
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
    C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'
    C_BLUE=$'\033[0;34m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_RESET=''
fi

# Agent-friendly line prefix. Emitted only when stdout/stderr is not a TTY
# (i.e. when the script is being driven by another process, an agent, a CI
# runner, or a piped tee). Interactive users see clean output.
_script_p1() { [[ ! -t 1 ]] && printf '[install.sh] '; return 0; }
_script_p2() { [[ ! -t 2 ]] && printf '[install.sh] ' >&2; return 0; }

log_info() { _script_p1; echo -e "${C_BLUE}==>${C_RESET} ${C_BOLD}$1${C_RESET}"; }
log_ok()   { _script_p1; echo -e "  ${C_GREEN}✓${C_RESET} $1"; }
log_warn() { _script_p1; echo -e "  ${C_YELLOW}!${C_RESET} $1"; }
log_err()  { _script_p2; echo -e "${C_RED}✗${C_RESET} $1" >&2; }
log_step() { _script_p1; echo -e "\n${C_BOLD}${C_BLUE}>>>${C_RESET} ${C_BOLD}$1${C_RESET}"; }

die() { log_err "$1"; exit 1; }

# Like die(), but accepts 0+ "next steps" lines that are printed in a clear
# "what to do next" block. Designed for agent-driven installs where the
# failure handler needs to know what to retry.
die_with_help() {
    local header="$1"; shift
    _script_p2
    echo -e "${C_RED}✗${C_RESET} $header" >&2
    if [[ $# -gt 0 ]]; then
        echo "" >&2
        echo "  Next steps to try:" >&2
        for step in "$@"; do
            echo "    → $step" >&2
        done
    fi
    echo "" >&2
    echo "  For diagnosis, run:    $SELF_CMD doctor" >&2
    echo "  For full usage, run:    $SELF_CMD --help" >&2
    exit 1
}

# Wrap a command: if DRY_RUN=1, just print what would happen. Otherwise run it.
run_or_dry() {
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        _script_p1
        echo "[DRY-RUN] would run: $*"
        return 0
    fi
    "$@"
}

# Exit-time summary. Emitted by the EXIT trap after the script's main work
# is done (success or failure). Agents tail the log for this line to know
# whether the install succeeded.
_on_exit_summary() {
    local rc=$1
    local elapsed=$(( $(date +%s) - SCRIPT_START_TS ))
    if [[ $rc -eq 0 ]]; then
        _script_p1
        echo "DONE: success in ${elapsed}s"
        if [[ -n "${LOG_FILE:-}" ]]; then
            _script_p1
            echo "DONE: full log saved to: $LOG_FILE"
        fi
    else
        _script_p2
        echo "DONE: FAILED (exit $rc) after ${elapsed}s" >&2
        if [[ -n "${LOG_FILE:-}" ]]; then
            _script_p2
            echo "DONE: failure log saved to: $LOG_FILE" >&2
        else
            _script_p2
            echo "DONE: re-run with --log-file <path> to capture full output." >&2
        fi
    fi
}

# ============================================================================
#  OS detection
# ============================================================================
detect_os() {
    local ostype="${OSTYPE:-}"
    if [[ "$ostype" == "linux-gnu"* || "$ostype" == "linux-musl"* ]]; then
        # Distinguish WSL from native Linux
        if [[ -r /proc/version ]] && grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
            echo "wsl"
        else
            echo "linux"
        fi
    elif [[ "$ostype" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$ostype" == "msys"* || "$ostype" == "cygwin"* || "$ostype" == "win32" ]]; then
        echo "windows-like"
    elif [[ -r /proc/version ]] && grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
        echo "wsl"
    else
        echo "unknown"
    fi
}

os_install_hint() {
    case "$1" in
        linux|wsl)
            cat <<'EOF'
    Install Git for your distro, e.g.:
        Debian/Ubuntu : sudo apt update && sudo apt install -y git
        Fedora/RHEL   : sudo dnf install -y git
        Arch          : sudo pacman -S --noconfirm git
        openSUSE      : sudo zypper install -y git
EOF
            ;;
        macos)
            cat <<'EOF'
    Install Git on macOS:
        xcode-select --install          # Apple Command Line Tools
        — or —
        brew install git
EOF
            ;;
        windows-like)
            cat <<'EOF'
    On Windows, install one of:
        Git for Windows : https://git-scm.com/download/win  (then run from Git Bash)
        WSL             : https://learn.microsoft.com/windows/wsl/install  (recommended)
EOF
            ;;
    esac
}

# One-liner variant for the doctor output.
os_install_hint_oneliner() {
    case "$1" in
        linux|wsl) echo "sudo apt install -y git   (or your distro's package manager)" ;;
        macos)     echo "xcode-select --install    (or: brew install git)" ;;
        windows-like) echo "install Git for Windows or WSL" ;;
        *)         echo "install git via your package manager" ;;
    esac
}

# ============================================================================
#  Prerequisite: Git
# ============================================================================
check_git() {
    if ! command -v git >/dev/null 2>&1; then
        log_err "Git is not installed."
        os_install_hint "$OS"
        exit 1
    fi
    log_ok "$(git --version)"
}

# ============================================================================
#  Install / locate uv (Astral's Python package manager, no sudo)
# ============================================================================
install_uv() {
    if command -v uv >/dev/null 2>&1; then
        log_ok "uv $(uv --version | awk '{print $2}') already installed"
        return
    fi

    log_info "Installing uv via official astral.sh installer (no sudo)..."
    # The official installer drops uv into ~/.local/bin. We capture its output
    # so we can show progress in our own style.
    local tmp
    tmp=$(mktemp)
    if ! run_or_dry curl -LsSf --max-time 60 https://astral.sh/uv/install.sh -o "$tmp"; then
        rm -f "$tmp"
        die_with_help "Failed to download uv installer (network issue?)." \
                      "Check your network connection and proxy settings." \
                      "Retry:    $SELF_CMD" \
                      "Manual:   see https://docs.astral.sh/uv/"
    fi
    if ! run_or_dry env UV_INSTALL_DIR="$HOME/.local" sh "$tmp"; then
        rm -f "$tmp"
        die_with_help "uv installer exited with an error." \
                      "Inspect the output above for the exact failure." \
                      "Retry:    $SELF_CMD" \
                      "Manual:   curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi
    rm -f "$tmp"

    # Make uv visible to this session, then verify.
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if [[ "${DRY_RUN:-0}" -eq 0 ]] && ! command -v uv >/dev/null 2>&1; then
        die_with_help "uv still not on PATH after install." \
                      "Check:    ls -la $HOME/.local/bin/uv" \
                      "Or:       export PATH=\$HOME/.local/bin:\$HOME/.cargo/bin:\$PATH" \
                      "Then:     $SELF_CMD"
    fi
    log_ok "uv $(uv --version | awk '{print $2}') installed"
}

# ============================================================================
#  Python 3.10+ provisioning (via uv)
# ============================================================================
ensure_python() {
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        _script_p1
        echo "[DRY-RUN] would check for Python $PYTHON_MIN_VERSION+ via uv"
        return 0
    fi
    # Ask uv for any 3.10+ interpreter it can see (system or uv-managed).
    # `|| true` keeps a "not found" exit from tripping the ERR trap (which
    # set -E inherits into this command substitution); we test $py instead.
    local py
    py=$(uv python find "$PYTHON_MIN_VERSION" 2>/dev/null || true)
    if [[ -n "$py" && -x "$py" ]]; then
        log_ok "Python $($py --version 2>&1 | awk '{print $1, $2}')"
        return
    fi

    log_info "Python $PYTHON_MIN_VERSION+ not found — provisioning via uv (no sudo)..."
    if ! run_or_dry uv python install "$PYTHON_MIN_VERSION"; then
        die_with_help "Failed to install Python $PYTHON_MIN_VERSION via uv." \
                      "Retry:    $SELF_CMD" \
                      "Manual:   uv python install $PYTHON_MIN_VERSION" \
                      "Or:       install Python $PYTHON_MIN_VERSION+ from https://python.org"
    fi
    py=$(uv python find "$PYTHON_MIN_VERSION" 2>/dev/null || true)
    if [[ -z "$py" || ! -x "$py" ]]; then
        die_with_help "Python $PYTHON_MIN_VERSION still not found after uv install." \
                      "Retry:    $SELF_CMD" \
                      "Diagnose: $SELF_CMD doctor"
    fi
    log_ok "Python $($py --version 2>&1 | awk '{print $1, $2}')"
}

# ============================================================================
#  Clone or update the repo
# ============================================================================
clone_or_update_repo() {
    # Preview-only in dry-run mode. This guard MUST come before any pull /
    # backup / clone so --dry-run never mutates the filesystem.
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        if [[ -d "$CLAWCODEX_HOME/.git" ]]; then
            _script_p1; echo "[DRY-RUN] would update $CLAWCODEX_HOME: restore uv.lock, then git pull --ff-only (reset to origin/$REPO_REF if it can't fast-forward)"
        elif [[ -e "$CLAWCODEX_HOME" ]]; then
            _script_p1; echo "[DRY-RUN] would back up non-git $CLAWCODEX_HOME, then clone $REPO_URL (ref: $REPO_REF)"
        else
            _script_p1; echo "[DRY-RUN] would clone: $REPO_URL (ref: $REPO_REF) -> $CLAWCODEX_HOME"
        fi
        return 0
    fi

    if [[ -d "$CLAWCODEX_HOME/.git" ]]; then
        log_info "Existing repo found at $CLAWCODEX_HOME — pulling latest changes..."
        # A previous install's `uv sync` re-pins the *tracked* uv.lock in place;
        # that local change blocks `git pull --ff-only`, so without this the
        # installer would warn and silently keep old code on every update. This
        # dir is a managed mirror (not a working copy), so discard the
        # installer's own tracked churn before pulling.
        git -C "$CLAWCODEX_HOME" checkout -- uv.lock >/dev/null 2>&1 || true
        # git -C (not a cd-subshell) keeps this a direct `if` condition, so an
        # expected pull failure doesn't trip the ERR trap.
        if git -C "$CLAWCODEX_HOME" pull --ff-only >/dev/null 2>&1; then
            log_ok "Updated via fast-forward"
        # Fallback: shallow clones can't always fast-forward, and any stray
        # tracked edits would block the pull. Reset the managed mirror to the
        # remote ref so updates are never silently skipped.
        elif git -C "$CLAWCODEX_HOME" fetch --depth 1 origin "$REPO_REF" >/dev/null 2>&1 &&
             git -C "$CLAWCODEX_HOME" reset --hard FETCH_HEAD >/dev/null 2>&1; then
            log_ok "Updated (reset to origin/$REPO_REF)"
        else
            log_warn "Could not update $CLAWCODEX_HOME to latest; continuing with existing code."
        fi
        return
    fi

    if [[ -e "$CLAWCODEX_HOME" ]]; then
        # Exists but isn't a git repo — back it up so we don't clobber user work.
        local stamp
        stamp=$(date +%Y%m%d%H%M%S)
        log_warn "$CLAWCODEX_HOME exists but is not a git checkout. Backing up to ${CLAWCODEX_HOME}.bak.${stamp}"
        mv "$CLAWCODEX_HOME" "${CLAWCODEX_HOME}.bak.${stamp}"
    fi

    mkdir -p "$CLAWCODEX_PARENT_DIR"
    log_info "Cloning $REPO_URL (ref: $REPO_REF) → $CLAWCODEX_HOME"
    # Try the requested ref first (branch or tag).
    if git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$CLAWCODEX_HOME" 2>/dev/null; then
        log_ok "Cloned ref $REPO_REF"
        return
    fi

    # The ref doesn't exist on the remote (e.g. a typo'd --ref, or a tag that
    # isn't pushed). Fall back to the default branch so install still succeeds.
    log_warn "Ref '$REPO_REF' not found on $REPO_URL — falling back to the default branch."
    if ! git clone --depth 1 "$REPO_URL" "$CLAWCODEX_HOME"; then
        die_with_help "git clone failed." \
                      "Check your network connection." \
                      "Verify:  curl -I $REPO_URL" \
                      "Retry:   $SELF_CMD" \
                      "Diagnose: $SELF_CMD doctor"
    fi
    log_ok "Cloned default branch"
}

# ============================================================================
#  Create venv
# ============================================================================
create_venv() {
    if [[ "$USE_VENV" -eq 0 ]]; then
        log_info "--no-venv specified — skipping venv creation (deps will install to system Python)"
        return
    fi
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        _script_p1
        echo "[DRY-RUN] would run: uv venv --python $PYTHON_MIN_VERSION .venv   (in $CLAWCODEX_HOME)"
        return 0
    fi
    cd "$CLAWCODEX_HOME"
    if [[ -d ".venv" ]]; then
        log_ok "Existing venv at $CLAWCODEX_HOME/.venv"
        return
    fi
    log_info "Creating venv with Python $PYTHON_MIN_VERSION..."
    if ! run_or_dry uv venv --python "$PYTHON_MIN_VERSION" .venv; then
        die_with_help "uv venv failed." \
                      "Check:    uv --version" \
                      "Retry:    $SELF_CMD" \
                      "Diagnose: $SELF_CMD doctor"
    fi
    log_ok "Venv created"
}

# ============================================================================
#  Install dependencies
# ============================================================================
install_deps() {
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        log_info "Installing project + dependencies (lock-pinned to uv.lock when possible)..."
        _script_p1
        if [[ "$USE_VENV" -eq 1 ]]; then
            echo "[DRY-RUN] would run: uv sync   (in $CLAWCODEX_HOME; fallback: uv pip install -e .)"
        else
            echo "[DRY-RUN] would run: uv pip install --system -e .   (in $CLAWCODEX_HOME)"
        fi
        return 0
    fi
    cd "$CLAWCODEX_HOME"

    # --- With venv: prefer `uv sync` (honors uv.lock → exact transitive
    #     versions), fall back to an editable `uv pip install` if the lock is
    #     out of sync with pyproject.toml.
    if [[ "$USE_VENV" -eq 1 ]]; then
        [[ -d ".venv" ]] || die "Venv missing at $CLAWCODEX_HOME/.venv — run without --no-venv or re-clone."
        log_info "Installing dependencies (uv sync, lock-pinned to uv.lock)..."
        local synclog; synclog=$(mktemp)
        if uv sync 2>"$synclog"; then
            rm -f "$synclog"
            log_ok "Dependencies installed (lock-pinned via uv.lock)"
            return
        fi
        local sync_err
        sync_err=$(cat "$synclog" 2>/dev/null || true)
        rm -f "$synclog"
        log_warn "uv sync failed; falling back to editable install (NOT lock-pinned)."
        log_warn "  Sync error was: ${sync_err:-<no stderr captured>}"
        if ! uv pip install --python .venv/bin/python -e .; then
            die_with_help "Both uv sync and uv pip install failed." \
                          "Re-run with --log-file <path> to capture full output." \
                          "Retry:    $SELF_CMD" \
                          "Diagnose: $SELF_CMD doctor" \
                          "Clean:    $SELF_CMD uninstall && $SELF_CMD"
        fi
        log_ok "Dependencies installed (editable, fresh-resolved into .venv)"
        return
    fi

    # --- Without venv (--no-venv): install to the active system Python.
    log_info "Installing dependencies into system Python (uv pip install --system)..."
    local piplog; piplog=$(mktemp)
    if uv pip install --system -e . 2>"$piplog"; then
        rm -f "$piplog"
        log_ok "Dependencies installed (system Python)"
        return
    fi
    local pip_err
    pip_err=$(cat "$piplog" 2>/dev/null || true)
    rm -f "$piplog"
    # uv's PEP 668 message has changed wording across versions; match both the
    # structured code and the human message defensively.
    if echo "$pip_err" | grep -qiE 'externally[ -]managed'; then
        log_warn "System Python is externally managed (PEP 668). Retrying with --break-system-packages."
        if ! uv pip install --system --break-system-packages -e .; then
            die_with_help "uv pip install to system failed even with --break-system-packages." \
                          "Inspect the error above for missing system libraries." \
                          "Retry:    $SELF_CMD" \
                          "Or:       $SELF_CMD uninstall && $SELF_CMD   (fresh install with venv)"
        fi
        log_ok "Dependencies installed (system Python, --break-system-packages)"
        return
    fi
    log_err "uv pip install failed: ${pip_err:-<no stderr captured>}"
    die_with_help "Dependency install failed." \
                  "Re-run with --log-file <path> to capture full output." \
                  "Retry:    $SELF_CMD" \
                  "Diagnose: $SELF_CMD doctor"
}

# ============================================================================
#  Node + the Ink TUI client (`clawcodex tui`)
# ============================================================================

# Ensure `node`/`npm` are available. Reuses an existing install; otherwise fetches
# the official Node binary (no sudo) into ~/.clawcodex/node and links it onto PATH
# (~/.local/bin, already added to the shell rc). Non-fatal: returns 1 if Node
# can't be provided (the Python REPL works without it).
provision_node() {
    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        log_ok "node $(node --version 2>/dev/null) already installed"
        return 0
    fi
    if [[ "${DRY_RUN:-0}" -ne 0 ]]; then
        _script_p1; echo "[DRY-RUN] would fetch Node $NODE_VERSION into $NODE_DIR and link node/npm into $LOCAL_BIN"
        return 0
    fi
    local plat arch
    case "$(uname -s)" in
        Darwin) plat="darwin" ;;
        Linux)  plat="linux" ;;
        *) log_warn "Node auto-install unsupported on $(uname -s) — install Node 18+ for 'clawcodex tui'."; return 1 ;;
    esac
    case "$(uname -m)" in
        arm64|aarch64) arch="arm64" ;;
        x86_64|amd64)  arch="x64" ;;
        *) log_warn "Node auto-install unsupported on $(uname -m) — install Node 18+ for 'clawcodex tui'."; return 1 ;;
    esac
    local tarball="node-${NODE_VERSION}-${plat}-${arch}.tar.gz"
    local url="https://nodejs.org/dist/${NODE_VERSION}/${tarball}"
    log_info "Installing Node ${NODE_VERSION} (${plat}-${arch}) for the TUI (no sudo)..."
    local tmp; tmp="$(mktemp -d)"
    if ! curl -fsSL --max-time 180 "$url" -o "$tmp/$tarball"; then
        log_warn "Node download failed ($url) — install Node 18+ manually for 'clawcodex tui'."
        rm -rf "$tmp"; return 1
    fi
    rm -rf "$NODE_DIR"; mkdir -p "$NODE_DIR"
    if ! tar -xzf "$tmp/$tarball" -C "$NODE_DIR" --strip-components=1; then
        log_warn "Node extract failed — install Node 18+ manually for 'clawcodex tui'."
        rm -rf "$tmp"; return 1
    fi
    rm -rf "$tmp"
    mkdir -p "$LOCAL_BIN"
    ln -sf "$NODE_DIR/bin/node" "$LOCAL_BIN/node"
    ln -sf "$NODE_DIR/bin/npm" "$LOCAL_BIN/npm"
    ln -sf "$NODE_DIR/bin/npx" "$LOCAL_BIN/npx"
    export PATH="$LOCAL_BIN:$PATH"
    if command -v node >/dev/null 2>&1; then
        log_ok "Node $(node --version 2>/dev/null) installed"
        return 0
    fi
    log_warn "Node installed to $NODE_DIR but not on PATH — add $LOCAL_BIN to PATH."
    return 1
}

# Build the TypeScript Ink TUI — the sole interactive UI (`clawcodex` and the
# explicit `clawcodex tui`). Needs node + dist/cli.js + node_modules. Non-fatal:
# the headless path (`clawcodex -p`) still works without it.
build_tui() {
    local tui_dir="$CLAWCODEX_HOME/ui-tui"
    if [[ ! -f "$tui_dir/package.json" ]]; then
        log_warn "ui-tui not found at $tui_dir — interactive 'clawcodex' needs it; 'clawcodex -p' (headless) still works."
        return 0
    fi
    if [[ "${DRY_RUN:-0}" -ne 0 ]]; then
        _script_p1; echo "[DRY-RUN] would run: npm install && npm run build   (in $tui_dir)"
        return 0
    fi
    if ! provision_node; then
        log_warn "Skipping TUI build — Node unavailable. Interactive 'clawcodex' needs Node 18+; 'clawcodex -p' (headless) works without it."
        return 0
    fi
    log_info "Building the Ink TUI client (npm install + build; first run ~30s)..."
    if ( cd "$tui_dir" && npm install --no-audit --no-fund >/dev/null 2>&1 && npm run build >/dev/null 2>&1 ); then
        log_ok "Ink TUI built — run 'clawcodex'"
    else
        log_warn "Ink TUI build failed — interactive 'clawcodex' needs it ('clawcodex -p' headless still works). Retry: (cd \"$tui_dir\" && npm install && npm run build)"
    fi
}

# ============================================================================
#  Locate the venv's entry-point binary
# ============================================================================
find_venv_entry() {
    local venv_dir="$1" name="$2"
    # Linux/macOS layout
    if [[ -x "$venv_dir/bin/$name" ]]; then
        echo "$venv_dir/bin/$name"; return 0
    fi
    # Windows layout (Git Bash / WSL interop)
    if [[ -x "$venv_dir/Scripts/$name.exe" ]]; then
        echo "$venv_dir/Scripts/$name.exe"; return 0
    fi
    if [[ -x "$venv_dir/Scripts/$name" ]]; then
        echo "$venv_dir/Scripts/$name"; return 0
    fi
    return 1
}

# ============================================================================
#  Register the global command
#  - We write a tiny wrapper script in ~/.local/bin (more portable than a
#    symlink on Windows / Git Bash, and survives venv re-creation).
# ============================================================================
register_commands() {
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        _script_p1
        echo "[DRY-RUN] would register: $LOCAL_BIN/clawcodex"
        return 0
    fi
    mkdir -p "$LOCAL_BIN"

    local entry
    if [[ "$USE_VENV" -eq 1 ]]; then
        # Venv mode: look for the entry inside the project's .venv
        if ! entry=$(find_venv_entry "$CLAWCODEX_HOME/.venv" "$ENTRY_POINT"); then
            die "Entry point '$ENTRY_POINT' not found inside $CLAWCODEX_HOME/.venv — dependency install may have failed."
        fi
    else
        # --no-venv mode: look for the entry on PATH (uv pip install --system
        # drops scripts in /usr/local/bin or ~/.local/bin). Check a few common
        # locations explicitly so we don't depend on the just-installed PATH
        # being effective in this very shell.
        entry=""
        for candidate in \
            "$HOME/.local/bin/$ENTRY_POINT" \
            "/usr/local/bin/$ENTRY_POINT" \
            "$(command -v "$ENTRY_POINT" 2>/dev/null || true)"; do
            if [[ -n "$candidate" && ( -x "$candidate" || -L "$candidate" ) ]]; then
                entry="$candidate"; break
            fi
        done
        [[ -n "$entry" ]] || die "Entry point '$ENTRY_POINT' not found on PATH after system install — check 'which $ENTRY_POINT'."
    fi

    local wrapper="$LOCAL_BIN/$ENTRY_POINT"
    # Always (re)write so the wrapper reflects any new install dir.
    [[ -L "$wrapper" || -e "$wrapper" ]] && rm -f "$wrapper"
    cat > "$wrapper" <<EOF
#!/usr/bin/env bash
# Auto-generated by clawcodex install.sh — do not edit by hand.
# Regenerate by re-running install.sh.
exec "$entry" "\$@"
EOF
    chmod +x "$wrapper"
    log_ok "$wrapper → $entry"

    # Drop an ownership marker so `uninstall` only ever removes a tree THIS
    # installer created — never an arbitrary --install-dir the user points at.
    printf 'installed by clawcodex install.sh v%s\n' "$INSTALLER_VERSION" \
        > "$CLAWCODEX_HOME/.clawcodex-install" 2>/dev/null || true
}

# ============================================================================
#  Patch shell rc files to include ~/.local/bin in PATH
# ============================================================================
update_shell_rc() {
    local path_line='export PATH="$HOME/.local/bin:$PATH"'
    local rc_files=()

    [[ -f "$HOME/.bashrc" ]] && rc_files+=("$HOME/.bashrc")
    [[ -f "$HOME/.zshrc"  ]] && rc_files+=("$HOME/.zshrc")
    [[ -f "$HOME/.profile" ]] && rc_files+=("$HOME/.profile")

    if [[ ${#rc_files[@]} -eq 0 ]]; then
        log_warn "No shell rc file detected — please add '$path_line' to your shell's startup file."
        return
    fi

    for rc in "${rc_files[@]}"; do
        if grep -qF "$HOME/.local/bin" "$rc" 2>/dev/null; then
            log_ok "PATH already contains ~/.local/bin in $rc"
            continue
        fi
        if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
            _script_p1
            echo "[DRY-RUN] would append PATH entry to: $rc"
            continue
        fi
        {
            echo ""
            echo "$RC_MARKER"
            echo "$path_line"
        } >> "$rc"
        log_ok "Patched $rc (added ~/.local/bin to PATH)"
    done
}

# ============================================================================
#  Post-install pointer (non-blocking)
# ============================================================================
run_post_install_setup() {
    if [[ "$RUN_SETUP" -eq 0 ]]; then
        log_warn "Setup pointer skipped (--no-setup). Run 'clawcodex' manually to configure."
        return
    fi
    # We intentionally do NOT exec a blocking interactive REPL here — the
    # installer must stay non-interactive so it can run unattended (CI/Docker/
    # agents). We just point the user at the first-run commands.
    if command -v clawcodex >/dev/null 2>&1; then
        log_ok "Next, configure a provider + API key:"
        echo -e "    ${C_BOLD}clawcodex login${C_RESET}        # interactive provider + key setup"
        echo -e "    ${C_BOLD}clawcodex${C_RESET}              # start the REPL in any project"
        echo -e "    ${C_BOLD}clawcodex tui${C_RESET}          # the Ink TUI (Claude-Code-style)"
    else
        log_warn "clawcodex not on PATH yet — run 'source ~/.bashrc' (or ~/.zshrc) first."
    fi
}

# ============================================================================
#  Inspection subcommands (no side effects — safe for agents to call)
# ============================================================================

# Show current install state. No side effects.
cmd_status() {
    echo "=== clawcodex install status ==="
    echo "  Installer   : v${INSTALLER_VERSION}"
    echo "  Repo URL    : $REPO_URL"
    echo "  Git ref     : $REPO_REF"
    echo "  Install dir : $CLAWCODEX_HOME"
    echo "  Local bin   : $LOCAL_BIN"
    echo ""
    if [[ -d "$CLAWCODEX_HOME/.git" ]]; then
        local installed_sha installed_branch
        installed_sha=$(cd "$CLAWCODEX_HOME" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        installed_branch=$(cd "$CLAWCODEX_HOME" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        echo "  Git state   :"
        echo "    branch    : $installed_branch"
        echo "    commit    : $installed_sha"
        if [[ -d "$CLAWCODEX_HOME/.venv" ]]; then
            local py_ver
            py_ver=$("$CLAWCODEX_HOME/.venv/bin/python" --version 2>&1 | head -1 || echo "missing")
            echo "  Venv        : present (Python: $py_ver)"
        else
            echo "  Venv        : MISSING (run '$SELF_CMD update' to recreate)"
        fi
    else
        echo "  Git state   : NOT INSTALLED (run '$SELF_CMD install')"
    fi
    echo ""
    echo "  Command:"
    if [[ -x "$LOCAL_BIN/clawcodex" ]]; then
        echo "    $LOCAL_BIN/clawcodex : present"
    else
        echo "    $LOCAL_BIN/clawcodex : MISSING"
    fi
    echo ""
    if command -v clawcodex >/dev/null 2>&1; then
        echo "  clawcodex resolves to: $(command -v clawcodex)"
    else
        echo "  clawcodex NOT on PATH (run: source ~/.bashrc)"
    fi
    echo ""
    echo "=== end of status ==="
}

# Diagnose the environment. No side effects (only reads).
# Exit 0 if all critical checks pass, 1 if any fail.
cmd_doctor() {
    local fail=0 warn=0
    echo "=== clawcodex environment doctor ==="
    echo ""

    echo "[1/9] OS detection"
    if [[ "$OS" == "unknown" ]]; then
        echo "        ✗ unknown OS"; fail=$((fail+1))
    else
        echo "        ✓ $OS"
    fi

    echo "[2/9] Git"
    if command -v git >/dev/null 2>&1; then
        echo "        ✓ $(git --version)"
    else
        echo "        ✗ git not found"
        echo "          install: $(os_install_hint_oneliner "$OS")"
        fail=$((fail+1))
    fi

    echo "[3/9] Python >= $PYTHON_MIN_VERSION"
    if command -v uv >/dev/null 2>&1; then
        local py
        py=$(uv python find "$PYTHON_MIN_VERSION" 2>/dev/null || true)
        if [[ -n "$py" ]]; then
            echo "        ✓ $py ($($py --version 2>&1))"
        else
            echo "        ! no Python $PYTHON_MIN_VERSION+ found (uv will provision on install)"
            warn=$((warn+1))
        fi
    else
        echo "        ! uv not on PATH yet (Python check deferred to install time)"
        warn=$((warn+1))
    fi

    echo "[4/9] uv"
    if command -v uv >/dev/null 2>&1; then
        echo "        ✓ $(uv --version)"
    else
        echo "        ! uv not on PATH (will be installed by the installer)"
        warn=$((warn+1))
    fi

    echo "[5/9] Network reachability"
    if curl -sSf --max-time 5 -o /dev/null "$REPO_URL" 2>/dev/null; then
        echo "        ✓ repo reachable: $REPO_URL"
    else
        echo "        ✗ cannot reach $REPO_URL"
        echo "          check: proxy settings, VPN, DNS, firewall"
        fail=$((fail+1))
    fi

    # Walk up to the nearest EXISTING ancestor and test that — doctor must not
    # create directories (it is documented as side-effect-free).
    local probe="$CLAWCODEX_PARENT_DIR"
    while [[ -n "$probe" && "$probe" != "/" && ! -d "$probe" ]]; do
        probe=$(dirname -- "$probe")
    done

    echo "[6/9] Write access to install dir"
    if [[ -d "$probe" && -w "$probe" ]]; then
        echo "        ✓ writable: $probe"
    else
        echo "        ✗ cannot write: $probe"
        echo "          fix: sudo chown -R \$USER $probe   (or pick a different --install-dir)"
        fail=$((fail+1))
    fi

    echo "[7/9] Disk space"
    local avail_kb
    avail_kb=$(df -Pk "$probe" 2>/dev/null | awk 'NR==2 {print $4}' || true)
    if [[ -n "$avail_kb" ]] && [[ $avail_kb -gt 524288 ]]; then
        echo "        ✓ $(( avail_kb / 1024 ))MB available"
    else
        echo "        ✗ < 512MB available at $probe (need ~500MB for venv + deps)"
        fail=$((fail+1))
    fi

    echo "[8/9] ~/.local/bin in PATH"
    if [[ ":$PATH:" == *":$HOME/.local/bin:"* ]]; then
        echo "        ✓ $HOME/.local/bin is in current PATH"
    else
        echo "        ! $HOME/.local/bin NOT in current PATH (will be patched on install)"
        warn=$((warn+1))
    fi

    echo "[9/9] Existing install"
    if [[ -d "$CLAWCODEX_HOME/.git" ]]; then
        echo "        ✓ installed at $CLAWCODEX_HOME"
        echo "          (run '$SELF_CMD verify' to check health, '$SELF_CMD update' to refresh)"
    else
        echo "        ! not installed yet"
        warn=$((warn+1))
    fi

    echo ""
    echo "=== summary ==="
    echo "  critical : $fail"
    echo "  warnings : $warn"
    echo ""
    if [[ $fail -gt 0 ]]; then
        echo "  Result: NOT READY ($fail critical issue(s))"
        exit 1
    else
        echo "  Result: READY to install (or already installed)"
        exit 0
    fi
}

# Health check an existing install. No side effects.
cmd_verify() {
    local fail=0 warn=0
    echo "=== clawcodex install verification ==="
    echo ""

    echo "[1/6] Repo"
    if [[ -d "$CLAWCODEX_HOME/.git" ]]; then
        echo "      ✓ present at $CLAWCODEX_HOME"
    else
        echo "      ✗ NOT FOUND at $CLAWCODEX_HOME"
        echo "        run: $SELF_CMD install"
        fail=$((fail+1))
    fi

    echo "[2/6] Venv"
    if [[ -d "$CLAWCODEX_HOME/.venv" ]]; then
        echo "      ✓ present at $CLAWCODEX_HOME/.venv"
        if [[ -x "$CLAWCODEX_HOME/.venv/bin/python" ]]; then
            echo "      ✓ python works: $($CLAWCODEX_HOME/.venv/bin/python --version 2>&1)"
        else
            echo "      ✗ python missing in venv"; fail=$((fail+1))
        fi
    else
        echo "      ✗ venv MISSING at $CLAWCODEX_HOME/.venv"
        echo "        run: $SELF_CMD update   (or: $SELF_CMD install)"
        fail=$((fail+1))
    fi

    echo "[3/6] Entry point"
    local entry=""
    if [[ -d "$CLAWCODEX_HOME/.venv" ]]; then
        entry=$(find_venv_entry "$CLAWCODEX_HOME/.venv" "$ENTRY_POINT" 2>/dev/null || true)
    fi
    if [[ -n "$entry" && -x "$entry" ]]; then
        echo "      ✓ $ENTRY_POINT at $entry"
    else
        echo "      ✗ $ENTRY_POINT not found in venv"
        echo "        run: $SELF_CMD update"
        fail=$((fail+1))
    fi

    echo "[4/6] Command wrapper"
    if [[ -x "$LOCAL_BIN/clawcodex" ]]; then
        echo "      ✓ $LOCAL_BIN/clawcodex"
    else
        echo "      ✗ $LOCAL_BIN/clawcodex MISSING"
        echo "        run: $SELF_CMD install"
        fail=$((fail+1))
    fi

    echo "[5/6] PATH"
    if command -v clawcodex >/dev/null 2>&1; then
        echo "      ✓ clawcodex resolves to: $(command -v clawcodex)"
    else
        echo "      ! clawcodex NOT on PATH (wrapper exists but not exported)"
        echo "        run: source ~/.bashrc   (or ~/.zshrc)"
        warn=$((warn+1))
    fi

    echo "[6/6] Smoke test (clawcodex --version)"
    if command -v clawcodex >/dev/null 2>&1; then
        if clawcodex --version >/dev/null 2>&1; then
            echo "      ✓ clawcodex --version works"
        else
            echo "      ✗ clawcodex --version FAILED"; fail=$((fail+1))
        fi
    else
        echo "      ! skipped (not on PATH)"; warn=$((warn+1))
    fi

    echo ""
    if [[ $fail -gt 0 ]]; then
        echo "=== Result: UNHEALTHY ($fail issue(s), $warn warning(s)) ==="
        echo ""
        echo "Try:"
        echo "  $SELF_CMD update                 # re-pull and re-install deps"
        echo "  $SELF_CMD uninstall && $SELF_CMD # full clean reinstall"
        exit 1
    else
        echo "=== Result: HEALTHY ($warn warning(s)) ==="
        exit 0
    fi
}

# Update: pull latest and reinstall deps. Side effects: yes.
cmd_update() {
    log_info "Updating clawcodex at $CLAWCODEX_HOME (ref: $REPO_REF)..."
    if [[ ! -d "$CLAWCODEX_HOME/.git" ]]; then
        die_with_help "No existing install at $CLAWCODEX_HOME." \
                      "Run: $SELF_CMD install   (fresh install)" \
                      "Or:  $SELF_CMD doctor    (diagnose environment)"
    fi
    clone_or_update_repo
    create_venv
    install_deps
    register_commands
    build_tui
    log_ok "Update complete."
    log_info "Run '$SELF_CMD verify' to confirm health."
}

# ============================================================================
#  Uninstall — only removes what this script created
# ============================================================================
uninstall() {
    log_info "Uninstalling clawcodex..."
    log_info "  Install dir : $CLAWCODEX_HOME"
    log_info "  Local bin   : $LOCAL_BIN"

    # Hard safety: never operate on a protected path, regardless of markers.
    case "$CLAWCODEX_HOME" in
        "" | "/" | "$HOME" | "$HOME/")
            die "Refusing to uninstall from a protected path: '$CLAWCODEX_HOME'." ;;
    esac

    # Ownership gate: only delete an install dir that carries OUR marker file
    # (written by register_commands). This prevents `uninstall --install-dir
    # <a-user-dir>` from removing a tree this installer never created.
    local owned=0
    [[ -f "$CLAWCODEX_HOME/.clawcodex-install" ]] && owned=1

    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        _script_p1; echo "[DRY-RUN] would remove wrapper $LOCAL_BIN/clawcodex (only if it points into $CLAWCODEX_HOME/)"
        if [[ $owned -eq 1 ]]; then
            _script_p1; echo "[DRY-RUN] would remove install dir: $CLAWCODEX_HOME"
            _script_p1; echo "[DRY-RUN] would remove $CLAWCODEX_PARENT_DIR only if it is empty afterwards"
        else
            _script_p1; echo "[DRY-RUN] would SKIP $CLAWCODEX_HOME (no .clawcodex-install marker — not created by this installer)"
        fi
        return 0
    fi

    local wrapper="$LOCAL_BIN/clawcodex"
    if [[ -e "$wrapper" || -L "$wrapper" ]]; then
        # Only remove a wrapper that execs a binary inside THIS install dir. The
        # trailing slash anchors the match so /tmp/cc can't match /tmp/cc-prod.
        if grep -qF "$CLAWCODEX_HOME/" "$wrapper" 2>/dev/null; then
            rm -f "$wrapper"
            log_ok "Removed $wrapper"
        else
            log_warn "Skipped $wrapper — does not point inside $CLAWCODEX_HOME (other install?)"
        fi
    fi

    if [[ $owned -eq 0 ]]; then
        log_warn "Skipped $CLAWCODEX_HOME — no .clawcodex-install marker found."
        log_warn "  This directory was not created by this installer, so it is left untouched."
        log_warn "  (A half-finished install — deps failed before the marker was written —"
        log_warn "   can be repaired by re-running install; it reuses the clone.)"
        log_warn "  Otherwise remove it manually with 'rm -rf' if you are sure."
        log_ok "Uninstall complete (wrapper only)."
        return
    fi

    if [[ -d "$CLAWCODEX_HOME" ]]; then
        rm -rf "$CLAWCODEX_HOME"
        log_ok "Removed $CLAWCODEX_HOME"
    fi
    # Only auto-remove the install's parent dir if it's empty. ~/.clawcodex
    # usually still holds the user's config.json / sessions / skills, so it
    # will NOT be empty and we keep it by design.
    if [[ -d "$CLAWCODEX_PARENT_DIR" ]] \
        && [[ -z "$(ls -A "$CLAWCODEX_PARENT_DIR" 2>/dev/null)" ]]; then
        rmdir "$CLAWCODEX_PARENT_DIR" 2>/dev/null || true
        log_ok "Removed empty $CLAWCODEX_PARENT_DIR"
    elif [[ -d "$CLAWCODEX_PARENT_DIR" ]]; then
        log_warn "Preserved $CLAWCODEX_PARENT_DIR  (holds your config/sessions; delete manually with 'rm -rf' if desired)"
    fi

    log_warn "Note: this script does not edit your shell rc files. To remove the"
    log_warn "PATH entry, search for '$RC_MARKER' in ~/.bashrc / ~/.zshrc / ~/.profile"
    log_warn "and delete the two lines under it."
    log_ok "Uninstall complete."
}

# ============================================================================
#  Help / version
# ============================================================================
print_help() {
    cat <<EOF
clawcodex installer v${INSTALLER_VERSION}

USAGE
    $SELF_CMD [SUBCOMMAND] [OPTIONS]

SUBCOMMANDS
    (none) / install   Install clawcodex (default action).
    status             Show current install state — no side effects.
    doctor             Diagnose the environment (git, python, network, disk,
                       permissions) — no side effects.
    verify             Health-check an existing install (venv, entry point,
                       PATH, smoke test) — no side effects.
    update             Pull latest from the configured ref and reinstall deps.
    uninstall          Remove everything this installer created.
    help               Show this help.

OPTIONS
    --ref <ref>            Git ref to install (commit SHA, tag, or branch).
                           Default: ${REPO_REF}.
    --install-dir <path>   Override the project clone + venv location.
                           Default: ${DEFAULT_INSTALL_DIR}
    --no-venv              Skip virtual-environment creation. Dependencies are
                           installed into the active system Python via
                           'uv pip install --system'. Use in Docker images or
                           system-Python distros.
    --no-setup             Skip the post-install "next steps" pointer.
    --debug                Enable shell trace mode (set -x).
    --dry-run              Preview every change without applying it.
    --yes, -y              Assume 'yes' for any interactive prompts.
    --log-file <path>      Tee all output (stdout + stderr) to <path>.
    --uninstall, -u        Alias for the 'uninstall' subcommand.
    --help, -h             Show this help.
    --version, -v          Print installer version.

DEFAULTS
    Repo         : ${REPO_URL}
    Git ref      : ${REPO_REF}  (override with --ref)
    Install path : ${DEFAULT_INSTALL_DIR}  (override with --install-dir)
    Python       : >= ${PYTHON_MIN_VERSION}  (provisioned by uv if missing)
    Tooling      : uv (Astral's package manager — installed user-local, no sudo)

EXAMPLES
    # First-time install (most common):
    curl -fsSL https://clawcodex.app/install.sh | bash

    # Pass flags through the pipe:
    curl -fsSL https://clawcodex.app/install.sh | bash -s -- --dry-run

    # If you already cloned the repo:
    bash install.sh verify          # health-check
    bash install.sh doctor          # diagnose the environment
    bash install.sh --ref my-branch # install a specific branch/tag/commit
    bash install.sh uninstall       # remove everything this script installed

EXIT CODES
    0    Success.
    1    Installation / verification / doctor found a problem.
    2    Invalid CLI argument (unknown flag, missing value).

NOTES
    - Re-running this script is safe: existing repos are fast-forwarded,
      existing venvs are reused, the command wrapper is regenerated.
    - On Windows, run from Git Bash or WSL (native cmd.exe / PowerShell are
      detected and rejected with instructions).
    - In non-TTY mode (piped / agent / CI), every emitted line is prefixed
      with '[install.sh]', and a grep-friendly status line is emitted on exit.
EOF
}

# ============================================================================
#  Install pipeline
# ============================================================================
install_main() {
    echo -e "${C_BOLD}clawcodex installer v${INSTALLER_VERSION}${C_RESET}"
    echo -e "  ${C_BOLD}OS:${C_RESET}          $OS"
    echo -e "  ${C_BOLD}Install dir:${C_RESET} $CLAWCODEX_HOME"
    echo -e "  ${C_BOLD}Git ref:${C_RESET}     $REPO_REF"
    echo -e "  ${C_BOLD}Venv:${C_RESET}        $([[ $USE_VENV -eq 1 ]] && echo "create at $CLAWCODEX_HOME/.venv" || echo "${C_YELLOW}skipped (--no-venv, system Python)${C_RESET}")"
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        echo -e "  ${C_BOLD}Mode:${C_RESET}        ${C_YELLOW}DRY-RUN (no changes will be made)${C_RESET}"
    fi
    if [[ -n "${LOG_FILE:-}" ]]; then
        echo -e "  ${C_BOLD}Log file:${C_RESET}    $LOG_FILE"
    fi
    if [[ "${DEBUG:-0}" -eq 1 ]]; then
        echo -e "  ${C_BOLD}Debug:${C_RESET}       ${C_YELLOW}ON (set -x trace)${C_RESET}"
    fi

    log_step "1/8  Checking prerequisites"
    check_git

    log_step "2/8  Installing uv (Astral, no sudo)"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    install_uv

    log_step "3/8  Provisioning Python $PYTHON_MIN_VERSION+"
    ensure_python

    log_step "4/8  Cloning / updating repository"
    clone_or_update_repo

    log_step "5/8  $([[ $USE_VENV -eq 1 ]] && echo "Creating virtual environment" || echo "Preparing (no venv — using system Python)")"
    create_venv

    log_step "6/8  Installing dependencies"
    install_deps

    log_step "7/8  Registering global command & patching PATH"
    register_commands
    update_shell_rc

    log_step "8/8  Building the Ink TUI client (node + dist)"
    build_tui

    echo ""
    log_ok "Installation complete!"
    echo ""
    echo -e "  ${C_BOLD}Installed at:${C_RESET}  $CLAWCODEX_HOME"
    echo -e "  ${C_BOLD}Command at:${C_RESET}    $LOCAL_BIN/clawcodex"
    echo ""

    run_post_install_setup
    echo ""
    log_warn "Open a new shell, or run:  source ~/.bashrc   (or ~/.zshrc)"
}

# ============================================================================
#  CLI argument parser
# ============================================================================
REF_OVERRIDE=""
INSTALL_DIR_OVERRIDE=""
USE_VENV=1       # --no-venv flips to 0
RUN_SETUP=1      # --no-setup flips to 0
DRY_RUN=0        # --dry-run flips to 1
LOG_FILE=""      # --log-file <path>
DEBUG=0          # --debug flips to 1 (set -x trace)
SUBCOMMAND=""    # positional verb (install/status/doctor/verify/update/uninstall/help)
SCRIPT_START_TS=$(date +%s)

print_usage_hint() {
    echo "Try '$SELF_CMD --help' for usage." >&2
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            install|status|doctor|verify|update|uninstall|help)
                SUBCOMMAND="$1"; shift ;;
            --ref)
                [[ $# -ge 2 ]] || { log_err "--ref requires a value (commit/tag/branch)"; print_usage_hint; exit 2; }
                REF_OVERRIDE="$2"; shift 2 ;;
            --install-dir)
                [[ $# -ge 2 ]] || { log_err "--install-dir requires a path"; print_usage_hint; exit 2; }
                INSTALL_DIR_OVERRIDE="$2"; shift 2 ;;
            --log-file)
                [[ $# -ge 2 ]] || { log_err "--log-file requires a path"; print_usage_hint; exit 2; }
                LOG_FILE="$2"; shift 2 ;;
            --dry-run)   DRY_RUN=1; shift ;;
            --yes|-y)    shift ;;  # accepted for ergonomics; installer is already non-interactive
            --no-venv)   USE_VENV=0; shift ;;
            --no-setup)  RUN_SETUP=0; shift ;;
            --debug)     DEBUG=1; shift ;;
            --uninstall|-u) SUBCOMMAND="uninstall"; shift ;;
            --help|-h)   print_help; exit 0 ;;
            --version|-v)
                echo "install.sh v${INSTALLER_VERSION}"; exit 0 ;;
            --)          shift; break ;;
            -*)          log_err "Unknown option: $1"; print_usage_hint; exit 2 ;;
            *)           log_err "Unexpected positional argument: $1"; print_usage_hint; exit 2 ;;
        esac
    done
}

# ============================================================================
#  Entry point
# ============================================================================
# Install the EXIT trap before arg parsing so even early exits (unknown flag,
# --version, the Windows reject) still emit the grep-friendly DONE: line.
trap '_on_exit_summary $?' EXIT

parse_args "$@"

# Resolve overrides → effective paths. Must run AFTER parse_args.
CLAWCODEX_HOME="${INSTALL_DIR_OVERRIDE:-$DEFAULT_INSTALL_DIR}"
CLAWCODEX_PARENT_DIR="$(dirname -- "$CLAWCODEX_HOME")"
[[ -n "$REF_OVERRIDE" ]] && REPO_REF="$REF_OVERRIDE"

OS=$(detect_os)

# Bail out for native Windows shells — this script targets bash, not cmd/PS.
if [[ "$OS" == "unknown" ]] && [[ -n "${COMSPEC:-}" || -n "${WINDIR:-}" ]]; then
    cat >&2 <<END_MSG
✗ Native Windows shell detected (cmd.exe or PowerShell).

  install.sh is a bash script and cannot run directly in cmd or PowerShell.
  Please use one of the following options:

  Option A — Git Bash (recommended, zero-config):
    1. Install Git for Windows from https://git-scm.com/download/win
    2. Open "Git Bash" from the Start menu
    3. In Git Bash, run:    bash install.sh

  Option B — WSL2 (full Linux environment):
    1. Open PowerShell as Administrator and run:
         wsl --install -d Ubuntu
    2. Restart your computer
    3. Open the Ubuntu terminal and run:
         sudo apt update && sudo apt install -y git curl
         bash install.sh

  Option C — Install manually from source:
    1. Install Git, Python 3.10+, and curl
    2. Run:
         git clone ${REPO_URL} /tmp/clawcodex
         cd /tmp/clawcodex
         pip install -e .
    (See ${REPO_URL} for details)

END_MSG
    exit 1
fi

# Make uv visible early in case it's already installed but not on PATH for this shell.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Set up log-file tee if requested. Must happen AFTER parse_args so LOG_FILE
# is set, but BEFORE any other output. After this exec, [[ -t 1 ]] is false
# (it's a pipe), so the [install.sh] prefix is added on every line.
if [[ -n "$LOG_FILE" ]]; then
    log_file_dir=$(dirname -- "$LOG_FILE")
    if [[ ! -d "$log_file_dir" ]]; then
        mkdir -p "$log_file_dir" 2>/dev/null || { log_warn "Cannot create log dir $log_file_dir; --log-file ignored"; LOG_FILE=""; }
    fi
    if [[ -n "$LOG_FILE" ]]; then
        exec > >(tee -a "$LOG_FILE") 2>&1
    fi
fi

# Activate debug mode (set -x) if --debug was passed.
if [[ "$DEBUG" -eq 1 ]]; then
    set -x
fi

# Dispatch to subcommand. Default to 'install' when none was given.
case "${SUBCOMMAND:-install}" in
    install)   install_main ;;
    status)    cmd_status ;;
    doctor)    cmd_doctor ;;
    verify)    cmd_verify ;;
    update)    cmd_update ;;
    uninstall) uninstall ;;
    help)      print_help ;;
    *)
        log_err "Unknown subcommand: $SUBCOMMAND"
        print_usage_hint
        exit 2
        ;;
esac
