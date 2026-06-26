#!/usr/bin/env bash
# Vendor the openclaude (Claude Code) Ink TUI source into the clawcodex tree.
#
# The migration runs the real openclaude TUI as a thin *client* of the Python
# agent-server over Direct Connect (cc://). The TUI source must LIVE in clawcodex
# (the reference checkout at ./typescript is gitignored and must not be a runtime
# dependency), so this script copies the buildable source into
#   ui-tui/vendor/openclaude/
# leaving out node_modules, build output, the web app, tests and VCS metadata.
#
# Reproducible: re-run after bumping the reference to refresh the vendor. Records
# the upstream version so reviewers can see exactly what was copied.
#
# Usage:
#   scripts/vendor-openclaude.sh [SRC_DIR] [DEST_DIR]
# Defaults:
#   SRC_DIR  = ./typescript                 (the reference openclaude checkout)
#   DEST_DIR = ./ui-tui/vendor/openclaude
#
# After vendoring, build the connect-mode CLI (Phase 2 of the migration plan):
#   cd ui-tui/vendor/openclaude && bun install && DIRECT_CONNECT=true bun run build
# then point `clawcodex tui` at ui-tui/vendor/openclaude/dist/cli.mjs.
set -euo pipefail

SRC_DIR="${1:-./typescript}"
DEST_DIR="${2:-./ui-tui/vendor/openclaude}"

if [[ ! -d "$SRC_DIR/src" ]]; then
  echo "error: $SRC_DIR/src not found — pass the openclaude checkout as SRC_DIR" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "error: rsync is required" >&2
  exit 1
fi

# What the vendored build needs (source + build config), and what it must NOT
# carry (deps, build output, the separate web app, tests, VCS, large assets).
INCLUDE=(src scripts bin package.json tsconfig.json bun.lock README.md LICENSE)
EXCLUDE=(
  --exclude '.git' --exclude 'node_modules' --exclude 'dist' --exclude 'web'
  --exclude 'tests' --exclude 'coverage' --exclude 'reports' --exclude '*.test.ts'
  --exclude '*.test.tsx' --exclude 'vscode-extension' --exclude 'docs'
)

mkdir -p "$DEST_DIR"
echo "vendoring openclaude:  $SRC_DIR  →  $DEST_DIR"

for item in "${INCLUDE[@]}"; do
  if [[ -e "$SRC_DIR/$item" ]]; then
    rsync -a --delete "${EXCLUDE[@]}" "$SRC_DIR/$item" "$DEST_DIR/"
  fi
done

# Record provenance: upstream name + version + copy timestamp + source commit.
ver="$(node -e "process.stdout.write(require('$PWD/$SRC_DIR/package.json').version||'unknown')" 2>/dev/null || echo unknown)"
name="$(node -e "process.stdout.write(require('$PWD/$SRC_DIR/package.json').name||'unknown')" 2>/dev/null || echo unknown)"
commit="$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo 'n/a')"
cat > "$DEST_DIR/VENDOR.md" <<EOF
# Vendored openclaude (Claude Code Ink TUI)

Copied by \`scripts/vendor-openclaude.sh\` — do not edit by hand; re-run the script.

- upstream package: \`$name\`
- upstream version: \`$ver\`
- source commit:    \`$commit\`
- copied from:      \`$SRC_DIR\`

Only the buildable source is vendored (no node_modules / dist / web / tests).
Build the connect-mode CLI with:

    cd ui-tui/vendor/openclaude
    bun install
    DIRECT_CONNECT=true bun run build      # connect mode is behind feature('DIRECT_CONNECT')

See \`my-docs/tui-ink-migration/migration-plan.md\` for the full migration.
EOF

echo "done. upstream $name@$ver (commit $commit)"
echo "next: cd $DEST_DIR && bun install && DIRECT_CONNECT=true bun run build"
