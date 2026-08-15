# ============================================================================
#  install.ps1 — One-click Windows installer for clawcodex
# ----------------------------------------------------------------------------
#  Run it with one line (no clone needed) from PowerShell:
#
#      irm https://clawcodex.app/install.ps1 | iex
#
#  To pass parameters through the pipe, wrap in a script block:
#
#      & ([scriptblock]::Create((irm https://clawcodex.app/install.ps1))) -DryRun
#
#  What it does (the native-Windows analog of install.sh):
#    - Windows/PowerShell detection (5.1+ and pwsh 7 both supported)
#    - Git prerequisite check (winget/git-scm hints)
#    - uv installation (user-local, via the official astral.sh installer)
#    - Python 3.10+ provisioning (via uv)
#    - Repo clone/update to %USERPROFILE%\.clawcodex\clawcodex
#    - Venv creation (uv-managed) + dependency install (lock-pinned via uv.lock)
#    - Global command shims: %USERPROFILE%\.local\bin\clawcodex(.cmd)
#    - User PATH update (registry; no admin rights needed)
#    - Node provisioning + Ink TUI build (the interactive `clawcodex` UI)
#
#  Subcommands (use exactly one, or omit for default 'install'):
#     install.ps1                    # install (default)
#     install.ps1 status             # show current install state
#     install.ps1 doctor             # diagnose the environment
#     install.ps1 verify             # health-check an existing install
#     install.ps1 update             # pull latest + reinstall deps
#     install.ps1 uninstall          # remove everything this script created
#     install.ps1 help               # show usage
#
#  Agent-friendly features (parity with install.sh):
#     - Subcommands (status / doctor / verify) for inspection without side effects
#     - -DryRun               preview every change before applying
#     - -Yes                  assume yes for any prompts
#     - -LogFile <path>       transcript of all output to a log file
#     - [install.ps1] prefix on every line when output is redirected
#     - "DONE: success|FAILED" summary line on exit (grep-friendly)
#     - Failures print a "Next steps" block with actionable fixes
# ----------------------------------------------------------------------------
[CmdletBinding(PositionalBinding = $false)]
param(
    # Positional verb: install | status | doctor | verify | update | uninstall | help
    [Parameter(Position = 0)]
    [ValidateSet('install', 'status', 'doctor', 'verify', 'update', 'uninstall', 'help', '')]
    [string]$Subcommand = '',

    # Git ref to install (commit SHA, tag, or branch). Default: main.
    [string]$Ref = '',

    # Override the project clone + venv location.
    [string]$InstallDir = '',

    # Preview every change without applying it.
    [switch]$DryRun,

    # Assume 'yes' for any interactive prompts.
    [switch]$Yes,

    # Transcript all output (stdout + stderr) to this path.
    [string]$LogFile = '',

    # Skip the post-install "next steps" pointer.
    [switch]$NoSetup,

    # Alias for the 'uninstall' subcommand.
    [switch]$Uninstall,

    # Show help / version.
    [switch]$Help,
    [switch]$Version
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

# ============================================================================
#  Config (defaults; env vars override like install.sh)
# ============================================================================
$INSTALLER_VERSION = '1.6.0'
# CLAWCODEX_REPO_URL override: install from a fork/mirror (or a local
# checkout when testing the installer itself).
$REPO_URL = if ($env:CLAWCODEX_REPO_URL) { $env:CLAWCODEX_REPO_URL }
            else { 'https://github.com/agentforce314/clawcodex' }
$PYTHON_MIN_VERSION = '3.10'
$ENTRY_POINT = 'clawcodex'
$NODE_VERSION = if ($env:CLAWCODEX_NODE_VERSION) { $env:CLAWCODEX_NODE_VERSION } else { 'v22.12.0' }

$RepoRef = if ($Ref) { $Ref } elseif ($env:CLAWCODEX_REF) { $env:CLAWCODEX_REF } else { 'main' }
$DefaultInstallDir = Join-Path $HOME '.clawcodex\clawcodex'
$ClawcodexHome = if ($InstallDir) { $InstallDir } else { $DefaultInstallDir }
$ClawcodexParent = Split-Path -Parent $ClawcodexHome
$LocalBin = Join-Path $HOME '.local\bin'
$NodeDir = Join-Path $HOME '.clawcodex\node'
$MarkerFile = Join-Path $ClawcodexHome '.clawcodex-install'

# How to refer to "this installer" in hints. Piped (iex) runs have no file path.
$SelfCmd = if ($PSCommandPath) { "powershell -File `"$PSCommandPath`"" }
           else { 'irm https://clawcodex.app/install.ps1 | iex' }

$script:StartTime = Get-Date
$script:ExitSummaryEmitted = $false

# ============================================================================
#  UI helpers
# ============================================================================
function Test-Redirected {
    try { return [Console]::IsOutputRedirected } catch { return $false }
}
$script:Prefix = if (Test-Redirected) { '[install.ps1] ' } else { '' }

function Write-Info ([string]$Msg) { Write-Host "$($script:Prefix)==> $Msg" -ForegroundColor Cyan }
function Write-Ok   ([string]$Msg) { Write-Host "$($script:Prefix)  + $Msg" -ForegroundColor Green }
function Write-Warn2([string]$Msg) { Write-Host "$($script:Prefix)  ! $Msg" -ForegroundColor Yellow }
function Write-Err  ([string]$Msg) { Write-Host "$($script:Prefix)  x $Msg" -ForegroundColor Red }
function Write-Step ([string]$Msg) { Write-Host ''; Write-Host "$($script:Prefix)>>> $Msg" -ForegroundColor Cyan }
function Write-Plain([string]$Msg) { Write-Host "$($script:Prefix)$Msg" }

# die with an actionable "Next steps" block (agent-friendly failure contract).
function Stop-WithHelp {
    param([string]$Header, [string[]]$NextSteps = @())
    Write-Err $Header
    if ($NextSteps.Count -gt 0) {
        Write-Plain ''
        Write-Plain '  Next steps to try:'
        foreach ($s in $NextSteps) { Write-Plain "    -> $s" }
    }
    Write-Plain ''
    Write-Plain "  For diagnosis, run:    $SelfCmd doctor"
    Write-Plain "  For full usage, run:   $SelfCmd help"
    throw "INSTALL_FAILED: $Header"
}

function Write-ExitSummary ([bool]$Success, [int]$Code) {
    if ($script:ExitSummaryEmitted) { return }
    $script:ExitSummaryEmitted = $true
    $elapsed = [int]((Get-Date) - $script:StartTime).TotalSeconds
    if ($Success) {
        Write-Plain "DONE: success in ${elapsed}s"
        if ($LogFile) { Write-Plain "DONE: full log saved to: $LogFile" }
    } else {
        Write-Plain "DONE: FAILED (exit $Code) after ${elapsed}s"
        if ($LogFile) { Write-Plain "DONE: failure log saved to: $LogFile" }
        else { Write-Plain 'DONE: re-run with -LogFile <path> to capture full output.' }
    }
}

# ============================================================================
#  Small utilities
# ============================================================================
function Test-Command ([string]$Name) {
    try { return $null -ne (Get-Command $Name -ErrorAction Stop) } catch { return $false }
}

# Run a native executable safely under PS 5.1 + $ErrorActionPreference='Stop'.
#
# THE trap this exists for: any in-script redirect of a native command's
# stderr (2>$null, 2>&1) makes PS 5.1 wrap each stderr line in a
# NativeCommandError record — and with EAP='Stop' the FIRST such line kills
# the script. git prints its "Cloning into ..." banner to stderr, so a bare
# `git clone ... 2>$null` crashes the installer. The same wrapping happens
# with NO redirect at all when the host process itself was captured (agents,
# CI). So: every git/uv call goes through here — EAP is dropped to
# 'Continue' for the call, both streams are captured as plain strings, and
# callers branch on the exit code.
function Invoke-Native {
    param([Parameter(Mandatory)][string[]]$Argv)
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $exe = $Argv[0]
        $rest = @($Argv | Select-Object -Skip 1)
        # Plain array argument (NOT `@rest` splatting): PS 5.1 mangles
        # array-splats to native commands, while a plain array variable is
        # expanded element-per-argument reliably.
        $lines = if ($rest.Count -gt 0) { & $exe $rest 2>&1 | ForEach-Object { "$_" } }
                 else { & $exe 2>&1 | ForEach-Object { "$_" } }
        $joined = if ($null -eq $lines) { '' } else { ($lines -join "`n") }
        return @{ Code = $LASTEXITCODE; Output = $joined }
    } finally {
        $ErrorActionPreference = $eap
    }
}

function Add-ToProcessPath ([string]$Dir) {
    if (-not $Dir) { return }
    $parts = $env:Path -split ';' | Where-Object { $_ }
    if ($parts -notcontains $Dir) { $env:Path = "$Dir;$env:Path" }
}

# The user-level PATH lives in the registry; SetEnvironmentVariable is safe
# (no setx 1024-char truncation) and needs no admin rights.
function Add-ToUserPath ([string]$Dir) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($null -eq $current) { $current = '' }
    $parts = $current -split ';' | Where-Object { $_ }
    foreach ($p in $parts) {
        if ($p.TrimEnd('\') -ieq $Dir.TrimEnd('\')) {
            Write-Ok "PATH already contains $Dir (user environment)"
            return
        }
    }
    if ($DryRun) { Write-Plain "[DRY-RUN] would append to user PATH: $Dir"; return }
    $newPath = if ($current) { "$current;$Dir" } else { $Dir }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Ok "Added $Dir to user PATH (new terminals pick it up automatically)"
}

# Delete a directory tree, surviving >260-char paths. Remove-Item (PS 5.1)
# fails on long paths, and a venv's site-packages or a node_modules tree
# routinely exceeds the limit. Fallback: robocopy /MIR from an empty dir —
# robocopy handles long paths natively and ships with Windows — then remove
# the emptied shell.
function Remove-TreeRobust ([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        Remove-Item -Recurse -Force -LiteralPath $Path -ErrorAction Stop
        return
    } catch {
        Write-Warn2 "Standard delete hit long paths; retrying via robocopy mirror..."
    }
    $empty = Join-Path ([IO.Path]::GetTempPath()) ("cc-empty-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Force -Path $empty | Out-Null
    try {
        Invoke-Native @('robocopy', $empty, $Path, '/MIR', '/NJH', '/NJS', '/NC', '/NS', '/NP', '/NFL', '/NDL') | Out-Null
        Remove-Item -Recurse -Force -LiteralPath $Path -ErrorAction SilentlyContinue
    } finally {
        Remove-Item -Recurse -Force -LiteralPath $empty -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $Path) {
        throw "INSTALL_FAILED: could not fully remove $Path (files may be in use)"
    }
}

# Locate the venv's entry-point binary (Windows layout only — this installer
# is Windows-native; install.sh handles the POSIX layouts).
function Find-VenvEntry ([string]$VenvDir, [string]$Name) {
    foreach ($cand in @("$VenvDir\Scripts\$Name.exe", "$VenvDir\Scripts\$Name")) {
        if (Test-Path -LiteralPath $cand -PathType Leaf) { return $cand }
    }
    return $null
}

# Git Bash (NOT the WSL System32 shim) — required by clawcodex's Bash tool at
# runtime. Mirrors src/utils/shell_platform.py's resolution order.
function Find-GitBash {
    foreach ($v in @($env:CLAWCODEX_GIT_BASH_PATH, $env:CLAUDE_CODE_GIT_BASH_PATH)) {
        if ($v -and (Test-Path -LiteralPath $v -PathType Leaf)) { return $v }
    }
    $git = $null
    try { $git = (Get-Command git -ErrorAction Stop).Source } catch { }
    if ($git) {
        $gitRoot = Split-Path -Parent (Split-Path -Parent $git)
        foreach ($cand in @(
            (Join-Path $gitRoot 'bin\bash.exe'),
            (Join-Path (Split-Path -Parent $gitRoot) 'bin\bash.exe')
        )) {
            if (Test-Path -LiteralPath $cand -PathType Leaf) { return $cand }
        }
    }
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $base) { continue }
        foreach ($suffix in @('Git\bin\bash.exe', 'Programs\Git\bin\bash.exe')) {
            $cand = Join-Path $base $suffix
            if (Test-Path -LiteralPath $cand -PathType Leaf) { return $cand }
        }
    }
    return $null
}

# ============================================================================
#  Prerequisite: Git
# ============================================================================
function Test-Git {
    if (-not (Test-Command 'git')) {
        Write-Err 'Git is not installed.'
        Write-Plain '    Install Git for Windows (includes Git Bash, required by clawcodex):'
        Write-Plain '        winget install --id Git.Git -e'
        Write-Plain '        - or -  https://git-scm.com/download/win'
        throw 'INSTALL_FAILED: git missing'
    }
    Write-Ok (git --version)
}

# ============================================================================
#  Install / locate uv (Astral's Python manager; user-local, no admin)
# ============================================================================
function Install-Uv {
    if (Test-Command 'uv') {
        Write-Ok "uv $((uv --version) -replace 'uv ', '') already installed"
        return
    }
    Write-Info 'Installing uv via official astral.sh installer (user-local)...'
    if ($DryRun) { Write-Plain '[DRY-RUN] would run: irm https://astral.sh/uv/install.ps1 | iex'; return }
    try {
        $script = Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' -TimeoutSec 60
        $env:UV_PRINT_QUIET = '1'
        Invoke-Expression $script
    } catch {
        Stop-WithHelp 'Failed to download/run the uv installer (network issue?).' @(
            'Check your network connection and proxy settings.',
            'Manual install:   irm https://astral.sh/uv/install.ps1 | iex',
            "Then re-run:      $SelfCmd"
        )
    }
    # uv lands in %USERPROFILE%\.local\bin — make it visible to THIS process.
    Add-ToProcessPath $LocalBin
    if (-not (Test-Command 'uv')) {
        Stop-WithHelp 'uv still not on PATH after install.' @(
            "Open a NEW terminal and re-run: $SelfCmd",
            "Or add $LocalBin to PATH manually."
        )
    }
    Write-Ok "uv $((uv --version) -replace 'uv ', '') installed"
}

# ============================================================================
#  Python provisioning (via uv)
# ============================================================================
function Get-UvPython {
    $found = Invoke-Native @('uv', 'python', 'find', $PYTHON_MIN_VERSION)
    if ($found.Code -eq 0 -and $found.Output) {
        $cand = ($found.Output -split "`n")[0].Trim()
        if ($cand -and (Test-Path -LiteralPath $cand)) { return $cand }
    }
    return $null
}

function Confirm-Python {
    if ($DryRun) { Write-Plain "[DRY-RUN] would check for Python $PYTHON_MIN_VERSION+ via uv"; return }
    $py = Get-UvPython
    if ($py) {
        $ver = Invoke-Native @($py, '--version')
        Write-Ok ("Python " + ($ver.Output -replace 'Python ', ''))
        return
    }
    Write-Info "Python $PYTHON_MIN_VERSION+ not found - provisioning via uv..."
    $res = Invoke-Native @('uv', 'python', 'install', $PYTHON_MIN_VERSION)
    if ($res.Output) { Write-Plain $res.Output }
    if ($res.Code -ne 0) {
        Stop-WithHelp "Failed to install Python $PYTHON_MIN_VERSION via uv." @(
            "Retry:    $SelfCmd",
            "Manual:   uv python install $PYTHON_MIN_VERSION",
            'Or:       install Python 3.10+ from https://python.org'
        )
    }
    Write-Ok "Python $PYTHON_MIN_VERSION provisioned"
}

# ============================================================================
#  Clone or update the repo
# ============================================================================
function Sync-Repo {
    if ($DryRun) {
        if (Test-Path (Join-Path $ClawcodexHome '.git')) {
            Write-Plain "[DRY-RUN] would update ${ClawcodexHome}: restore uv.lock, then git pull --ff-only (reset to origin/$RepoRef if it can't fast-forward)"
        } elseif (Test-Path $ClawcodexHome) {
            Write-Plain "[DRY-RUN] would back up non-git $ClawcodexHome, then clone $REPO_URL (ref: $RepoRef)"
        } else {
            Write-Plain "[DRY-RUN] would clone: $REPO_URL (ref: $RepoRef) -> $ClawcodexHome"
        }
        return
    }

    if (Test-Path (Join-Path $ClawcodexHome '.git')) {
        Write-Info "Existing repo found at $ClawcodexHome - pulling latest changes..."
        # A previous install's `uv sync` may have re-pinned the tracked uv.lock;
        # discard the installer's own churn so --ff-only can move.
        Invoke-Native @('git', '-C', $ClawcodexHome, 'checkout', '--', 'uv.lock') | Out-Null
        $pull = Invoke-Native @('git', '-C', $ClawcodexHome, 'pull', '--ff-only')
        if ($pull.Code -eq 0) {
            Write-Ok 'Updated via fast-forward'
            return
        }
        $fetch = Invoke-Native @('git', '-C', $ClawcodexHome, 'fetch', '--depth', '1', 'origin', $RepoRef)
        if ($fetch.Code -eq 0) {
            $reset = Invoke-Native @('git', '-C', $ClawcodexHome, 'reset', '--hard', 'FETCH_HEAD')
            if ($reset.Code -eq 0) { Write-Ok "Updated (reset to origin/$RepoRef)"; return }
        }
        Write-Warn2 "Could not update $ClawcodexHome to latest; continuing with existing code."
        return
    }

    if (Test-Path $ClawcodexHome) {
        $stamp = Get-Date -Format 'yyyyMMddHHmmss'
        Write-Warn2 "$ClawcodexHome exists but is not a git checkout. Backing up to $ClawcodexHome.bak.$stamp"
        Move-Item -LiteralPath $ClawcodexHome -Destination "$ClawcodexHome.bak.$stamp"
    }

    New-Item -ItemType Directory -Force -Path $ClawcodexParent | Out-Null
    Write-Info "Cloning $REPO_URL (ref: $RepoRef) -> $ClawcodexHome"
    $clone = Invoke-Native @('git', 'clone', '--depth', '1', '--branch', $RepoRef, $REPO_URL, $ClawcodexHome)
    if ($clone.Code -eq 0) { Write-Ok "Cloned ref $RepoRef"; return }

    Write-Warn2 "Ref '$RepoRef' not found on $REPO_URL - falling back to the default branch."
    $clone2 = Invoke-Native @('git', 'clone', '--depth', '1', $REPO_URL, $ClawcodexHome)
    if ($clone2.Code -ne 0) {
        if ($clone2.Output) { Write-Plain $clone2.Output }
        Stop-WithHelp 'git clone failed.' @(
            'Check your network connection.',
            "Verify:   git ls-remote $REPO_URL",
            "Retry:    $SelfCmd"
        )
    }
    Write-Ok 'Cloned default branch'
}

# ============================================================================
#  Venv + dependencies (uv-managed, lock-pinned)
# ============================================================================
function New-Venv {
    if ($DryRun) { Write-Plain "[DRY-RUN] would run: uv venv --python $PYTHON_MIN_VERSION .venv   (in $ClawcodexHome)"; return }
    if (Test-Path (Join-Path $ClawcodexHome '.venv')) {
        Write-Ok "Existing venv at $ClawcodexHome\.venv"
        return
    }
    Write-Info "Creating venv with Python $PYTHON_MIN_VERSION..."
    Push-Location $ClawcodexHome
    try {
        $venv = Invoke-Native @('uv', 'venv', '--python', $PYTHON_MIN_VERSION, '.venv')
        if ($venv.Code -ne 0) {
            if ($venv.Output) { Write-Plain $venv.Output }
            Stop-WithHelp 'uv venv failed.' @('Check:  uv --version', "Retry:  $SelfCmd")
        }
    } finally { Pop-Location }
    Write-Ok 'Venv created'
}

function Install-Deps {
    if ($DryRun) { Write-Plain "[DRY-RUN] would run: uv sync   (in $ClawcodexHome; fallback: uv pip install -e .)"; return }
    Write-Info 'Installing dependencies (uv sync, lock-pinned to uv.lock)...'
    Push-Location $ClawcodexHome
    try {
        $sync = Invoke-Native @('uv', 'sync')
        if ($sync.Code -eq 0) {
            Write-Ok 'Dependencies installed (lock-pinned via uv.lock)'
            return
        }
        Write-Warn2 'uv sync failed; falling back to editable install (NOT lock-pinned).'
        if ($sync.Output) {
            $tail = ($sync.Output -split "`n" | Select-Object -Last 3) -join ' '
            Write-Warn2 "  Sync error was: $tail"
        }
        $pip = Invoke-Native @('uv', 'pip', 'install', '--python', '.venv', '-e', '.')
        if ($pip.Code -ne 0) {
            if ($pip.Output) { Write-Plain $pip.Output }
            Stop-WithHelp 'Both uv sync and uv pip install failed.' @(
                "Re-run with -LogFile <path> to capture full output.",
                "Retry:    $SelfCmd",
                "Clean:    $SelfCmd uninstall; then $SelfCmd"
            )
        }
        Write-Ok 'Dependencies installed (editable, fresh-resolved into .venv)'
    } finally { Pop-Location }
}

# ============================================================================
#  Register the global command (shims in ~\.local\bin) + PATH
# ============================================================================
function Register-Commands {
    if ($DryRun) { Write-Plain "[DRY-RUN] would register: $LocalBin\clawcodex.cmd (+ Git Bash shim)"; return }
    New-Item -ItemType Directory -Force -Path $LocalBin | Out-Null

    $entry = Find-VenvEntry (Join-Path $ClawcodexHome '.venv') $ENTRY_POINT
    if (-not $entry) {
        Stop-WithHelp "Entry point '$ENTRY_POINT' not found inside $ClawcodexHome\.venv - dependency install may have failed." @(
            "Retry:    $SelfCmd update"
        )
    }

    # .cmd shim: resolves from cmd.exe AND PowerShell. Always rewritten so it
    # reflects any new install dir.
    $cmdShim = Join-Path $LocalBin "$ENTRY_POINT.cmd"
    @(
        '@echo off',
        'rem Auto-generated by clawcodex install.ps1 - do not edit by hand.',
        'rem Regenerate by re-running install.ps1.',
        "`"$entry`" %*"
    ) | Set-Content -LiteralPath $cmdShim -Encoding ascii
    Write-Ok "$cmdShim -> $entry"

    # Extension-less bash shim so `clawcodex` also works from Git Bash.
    $bashShim = Join-Path $LocalBin $ENTRY_POINT
    $entryFwd = $entry -replace '\\', '/'
    $bashLines = "#!/usr/bin/env bash`n# Auto-generated by clawcodex install.ps1 - do not edit by hand.`nexec `"$entryFwd`" `"`$@`"`n"
    [IO.File]::WriteAllText($bashShim, $bashLines)
    Write-Ok "$bashShim (Git Bash shim)"

    # Ownership marker so `uninstall` only ever removes a tree THIS installer
    # created - never an arbitrary -InstallDir the user pointed at.
    "installed by clawcodex install.ps1 v$INSTALLER_VERSION" | Set-Content -LiteralPath $MarkerFile -Encoding ascii

    Add-ToUserPath $LocalBin
    Add-ToProcessPath $LocalBin
}

# ============================================================================
#  Node + the Ink TUI client (`clawcodex` interactive UI)
# ============================================================================
function Install-Node {
    if ((Test-Command 'node') -and (Test-Command 'npm')) {
        Write-Ok "node $(node --version) already installed"
        return $true
    }
    if ($DryRun) { Write-Plain "[DRY-RUN] would fetch Node $NODE_VERSION into $NodeDir and shim node/npm into $LocalBin"; return $true }

    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    $zipName = "node-$NODE_VERSION-win-$arch.zip"
    $url = "https://nodejs.org/dist/$NODE_VERSION/$zipName"
    Write-Info "Installing Node $NODE_VERSION (win-$arch) for the TUI (user-local)..."
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "clawcodex-node-$([guid]::NewGuid().ToString('n'))"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        try {
            Invoke-WebRequest -Uri $url -OutFile (Join-Path $tmp $zipName) -TimeoutSec 300 -UseBasicParsing
        } catch {
            Write-Warn2 "Node download failed ($url) - install Node 18+ manually for the interactive TUI."
            return $false
        }
        if (Test-Path $NodeDir) { Remove-Item -Recurse -Force $NodeDir }
        New-Item -ItemType Directory -Force -Path $NodeDir | Out-Null
        Expand-Archive -LiteralPath (Join-Path $tmp $zipName) -DestinationPath $tmp -Force
        $extracted = Join-Path $tmp "node-$NODE_VERSION-win-$arch"
        if (-not (Test-Path (Join-Path $extracted 'node.exe'))) {
            Write-Warn2 'Node extract failed - install Node 18+ manually for the interactive TUI.'
            return $false
        }
        Get-ChildItem -LiteralPath $extracted | Move-Item -Destination $NodeDir -Force
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }

    # Shims into ~\.local\bin (already on PATH). npm.cmd resolves node.exe
    # relative to its own directory, so shim via cmd wrappers into $NodeDir.
    New-Item -ItemType Directory -Force -Path $LocalBin | Out-Null
    foreach ($tool in @('node', 'npm', 'npx')) {
        $target = if ($tool -eq 'node') { "$NodeDir\node.exe" } else { "$NodeDir\$tool.cmd" }
        @('@echo off', "`"$target`" %*") | Set-Content -LiteralPath (Join-Path $LocalBin "$tool.cmd") -Encoding ascii
    }
    Add-ToProcessPath $NodeDir
    Add-ToProcessPath $LocalBin
    if (Test-Command 'node') {
        Write-Ok "Node $(node --version) installed"
        return $true
    }
    Write-Warn2 "Node installed to $NodeDir but not resolvable - add $LocalBin to PATH."
    return $false
}

function Build-Tui {
    $tuiDir = Join-Path $ClawcodexHome 'ui-tui'
    # Dry-run check FIRST: in a dry-run nothing was cloned, so the
    # package.json probe below would misreport "ui-tui not found".
    if ($DryRun) { Write-Plain "[DRY-RUN] would run: npm install; npm run build   (in $tuiDir)"; return }
    if (-not (Test-Path (Join-Path $tuiDir 'package.json'))) {
        Write-Warn2 "ui-tui not found at $tuiDir - interactive 'clawcodex' needs it; 'clawcodex -p' (headless) still works."
        return
    }
    if (-not (Install-Node)) {
        Write-Warn2 "Skipping TUI build - Node unavailable. Interactive 'clawcodex' needs Node 18+; 'clawcodex -p' (headless) works without it."
        return
    }
    Write-Info 'Building the Ink TUI client (npm install + build; first run ~30s)...'
    Push-Location $tuiDir
    try {
        cmd /c 'npm install --no-audit --no-fund >nul 2>&1'
        $installOk = ($LASTEXITCODE -eq 0)
        $buildOk = $false
        if ($installOk) {
            cmd /c 'npm run build >nul 2>&1'
            $buildOk = ($LASTEXITCODE -eq 0)
        }
        if ($installOk -and $buildOk) {
            Write-Ok "Ink TUI built - run 'clawcodex'"
        } else {
            Write-Warn2 "Ink TUI build failed - interactive 'clawcodex' needs it ('clawcodex -p' headless still works)."
            Write-Warn2 "  Retry: cd `"$tuiDir`"; npm install; npm run build"
        }
    } finally { Pop-Location }
}

# ============================================================================
#  Inspection subcommands (no side effects)
# ============================================================================
function Invoke-Status {
    Write-Plain '=== clawcodex install status ==='
    Write-Plain "  Installer   : v$INSTALLER_VERSION (install.ps1)"
    Write-Plain "  Repo URL    : $REPO_URL"
    Write-Plain "  Git ref     : $RepoRef"
    Write-Plain "  Install dir : $ClawcodexHome"
    Write-Plain "  Local bin   : $LocalBin"
    Write-Plain ''
    if (Test-Path (Join-Path $ClawcodexHome '.git')) {
        $sha = (Invoke-Native @('git', '-C', $ClawcodexHome, 'rev-parse', '--short', 'HEAD')).Output
        $branch = (Invoke-Native @('git', '-C', $ClawcodexHome, 'rev-parse', '--abbrev-ref', 'HEAD')).Output
        Write-Plain '  Git state   :'
        Write-Plain "    branch    : $branch"
        Write-Plain "    commit    : $sha"
        $venvPy = Join-Path $ClawcodexHome '.venv\Scripts\python.exe'
        if (Test-Path $venvPy) {
            Write-Plain "  Venv        : present (Python: $((& $venvPy --version 2>&1)))"
        } else {
            Write-Plain "  Venv        : MISSING (run '$SelfCmd update' to recreate)"
        }
    } else {
        Write-Plain "  Git state   : NOT INSTALLED (run '$SelfCmd')"
    }
    Write-Plain ''
    Write-Plain '  Command:'
    $shim = Join-Path $LocalBin 'clawcodex.cmd'
    if (Test-Path $shim) { Write-Plain "    ${shim} : present" } else { Write-Plain "    ${shim} : MISSING" }
    Write-Plain ''
    if (Test-Command 'clawcodex') {
        Write-Plain "  clawcodex resolves to: $((Get-Command clawcodex).Source)"
    } else {
        Write-Plain '  clawcodex NOT on PATH (open a new terminal after install)'
    }
    Write-Plain ''
    Write-Plain '=== end of status ==='
}

function Invoke-Doctor {
    $fail = 0; $warn = 0
    Write-Plain '=== clawcodex environment doctor (Windows) ==='
    Write-Plain ''

    Write-Plain '[1/10] OS / PowerShell'
    $psv = $PSVersionTable.PSVersion
    Write-Plain "        + Windows $([Environment]::OSVersion.Version) / PowerShell $psv"

    Write-Plain '[2/10] Git'
    if (Test-Command 'git') {
        Write-Plain "        + $(git --version)"
    } else {
        Write-Plain '        x git not found'
        Write-Plain '          install: winget install --id Git.Git -e   (or https://git-scm.com/download/win)'
        $fail++
    }

    Write-Plain '[3/10] Git Bash (required by the clawcodex Bash tool at runtime)'
    $gitBash = Find-GitBash
    if ($gitBash) {
        Write-Plain "        + $gitBash"
    } else {
        Write-Plain '        ! Git Bash not found - shell commands inside clawcodex need it'
        Write-Plain '          fix: install Git for Windows (winget install --id Git.Git -e)'
        $warn++
    }

    Write-Plain "[4/10] Python >= $PYTHON_MIN_VERSION"
    if (Test-Command 'uv') {
        $py = Get-UvPython
        if ($py) {
            Write-Plain "        + $py"
        } else {
            Write-Plain "        ! no Python $PYTHON_MIN_VERSION+ found (uv will provision on install)"
            $warn++
        }
    } else {
        Write-Plain '        ! uv not on PATH yet (Python check deferred to install time)'
        $warn++
    }

    Write-Plain '[5/10] uv'
    if (Test-Command 'uv') {
        Write-Plain "        + $(uv --version)"
    } else {
        Write-Plain '        ! uv not on PATH (will be installed by the installer)'
        $warn++
    }

    Write-Plain '[6/10] Network reachability'
    $reachable = $false
    try {
        $resp = Invoke-WebRequest -Uri $REPO_URL -Method Head -TimeoutSec 5 -UseBasicParsing
        $reachable = ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400)
    } catch { $reachable = $false }
    if ($reachable) {
        Write-Plain "        + repo reachable: $REPO_URL"
    } else {
        Write-Plain "        x cannot reach $REPO_URL"
        Write-Plain '          check: proxy settings, VPN, DNS, firewall'
        $fail++
    }

    # Walk up to the nearest EXISTING ancestor and test that - doctor must not
    # create directories (documented as side-effect-free).
    $probe = $ClawcodexParent
    while ($probe -and -not (Test-Path $probe)) { $probe = Split-Path -Parent $probe }
    if (-not $probe) { $probe = $HOME }

    Write-Plain '[7/10] Write access to install dir'
    $canWrite = $false
    try {
        $testFile = Join-Path $probe ".clawcodex-write-test-$PID"
        [IO.File]::WriteAllText($testFile, 'x')
        Remove-Item -LiteralPath $testFile -Force
        $canWrite = $true
    } catch { $canWrite = $false }
    if ($canWrite) {
        Write-Plain "        + writable: $probe"
    } else {
        Write-Plain "        x cannot write: $probe"
        Write-Plain '          fix: pick a different -InstallDir, or check folder permissions'
        $fail++
    }

    Write-Plain '[8/10] Disk space'
    $freeMB = 0
    try {
        $driveName = ([IO.Path]::GetPathRoot($probe)).TrimEnd('\').TrimEnd(':')
        $drive = Get-PSDrive -Name $driveName -ErrorAction Stop
        $freeMB = [int]($drive.Free / 1MB)
    } catch { $freeMB = -1 }
    if ($freeMB -lt 0) {
        Write-Plain '        ! could not determine free space'
        $warn++
    } elseif ($freeMB -gt 512) {
        Write-Plain "        + ${freeMB}MB available"
    } else {
        Write-Plain "        x < 512MB available at $probe (need ~500MB for venv + deps)"
        $fail++
    }

    Write-Plain '[9/10] ~\.local\bin in PATH'
    $inPath = ($env:Path -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ieq $LocalBin.TrimEnd('\')) })
    if ($inPath) {
        Write-Plain "        + $LocalBin is in current PATH"
    } else {
        Write-Plain "        ! $LocalBin NOT in current PATH (will be added on install)"
        $warn++
    }

    Write-Plain '[10/10] Existing install'
    if (Test-Path (Join-Path $ClawcodexHome '.git')) {
        Write-Plain "        + installed at $ClawcodexHome"
        Write-Plain "          (run '$SelfCmd verify' to check health, '$SelfCmd update' to refresh)"
    } else {
        Write-Plain '        ! not installed yet'
        $warn++
    }

    Write-Plain ''
    Write-Plain '=== summary ==='
    Write-Plain "  critical : $fail"
    Write-Plain "  warnings : $warn"
    Write-Plain ''
    if ($fail -gt 0) {
        Write-Plain "  Result: NOT READY ($fail critical issue(s))"
        return 1
    }
    Write-Plain '  Result: READY to install (or already installed)'
    return 0
}

function Invoke-Verify {
    $fail = 0; $warn = 0
    Write-Plain '=== clawcodex install verification ==='
    Write-Plain ''

    Write-Plain '[1/6] Repo'
    if (Test-Path (Join-Path $ClawcodexHome '.git')) {
        Write-Plain "      + present at $ClawcodexHome"
    } else {
        Write-Plain "      x NOT FOUND at $ClawcodexHome"
        Write-Plain "        run: $SelfCmd"
        $fail++
    }

    Write-Plain '[2/6] Venv'
    $venvPy = Join-Path $ClawcodexHome '.venv\Scripts\python.exe'
    if (Test-Path (Join-Path $ClawcodexHome '.venv')) {
        Write-Plain "      + present at $ClawcodexHome\.venv"
        if (Test-Path $venvPy) {
            Write-Plain "      + python works: $((& $venvPy --version 2>&1))"
        } else {
            Write-Plain '      x python missing in venv'; $fail++
        }
    } else {
        Write-Plain "      x venv MISSING at $ClawcodexHome\.venv"
        Write-Plain "        run: $SelfCmd update"
        $fail++
    }

    Write-Plain '[3/6] Entry point'
    $entry = Find-VenvEntry (Join-Path $ClawcodexHome '.venv') $ENTRY_POINT
    if ($entry) {
        Write-Plain "      + $ENTRY_POINT at $entry"
    } else {
        Write-Plain "      x $ENTRY_POINT not found in venv"
        Write-Plain "        run: $SelfCmd update"
        $fail++
    }

    Write-Plain '[4/6] Command shim'
    $shim = Join-Path $LocalBin 'clawcodex.cmd'
    if (Test-Path $shim) {
        Write-Plain "      + $shim"
    } else {
        Write-Plain "      x $shim MISSING"
        Write-Plain "        run: $SelfCmd"
        $fail++
    }

    Write-Plain '[5/6] PATH'
    if (Test-Command 'clawcodex') {
        Write-Plain "      + clawcodex resolves to: $((Get-Command clawcodex).Source)"
    } else {
        Write-Plain '      ! clawcodex NOT on PATH (shim exists but PATH not refreshed)'
        Write-Plain '        fix: open a NEW terminal (the installer updated the user PATH)'
        $warn++
    }

    Write-Plain '[6/6] Smoke test (clawcodex --version)'
    $smokeCmd = if (Test-Command 'clawcodex') { 'clawcodex' } elseif ($entry) { $entry } else { $null }
    if ($smokeCmd) {
        $smoke = Invoke-Native @($smokeCmd, '--version')
        if ($smoke.Code -eq 0) {
            Write-Plain "      + $($smoke.Output)"
        } else {
            Write-Plain '      x clawcodex --version FAILED'; $fail++
        }
    } else {
        Write-Plain '      ! skipped (no entry point found)'; $warn++
    }

    Write-Plain ''
    if ($fail -gt 0) {
        Write-Plain "=== Result: UNHEALTHY ($fail issue(s), $warn warning(s)) ==="
        Write-Plain ''
        Write-Plain 'Try:'
        Write-Plain "  $SelfCmd update       # re-pull and re-install deps"
        Write-Plain "  $SelfCmd uninstall    # then re-run the installer for a clean slate"
        return 1
    }
    Write-Plain "=== Result: HEALTHY ($warn warning(s)) ==="
    return 0
}

function Invoke-Update {
    Write-Info "Updating clawcodex at $ClawcodexHome (ref: $RepoRef)..."
    if (-not (Test-Path (Join-Path $ClawcodexHome '.git'))) {
        Stop-WithHelp "No existing install at $ClawcodexHome." @(
            "Run: $SelfCmd          (fresh install)",
            "Or:  $SelfCmd doctor   (diagnose environment)"
        )
    }
    Sync-Repo
    New-Venv
    Install-Deps
    Register-Commands
    Build-Tui
    Write-Ok 'Update complete.'
    Write-Info "Run '$SelfCmd verify' to confirm health."
}

# ============================================================================
#  Uninstall - only removes what this script created
# ============================================================================
function Invoke-Uninstall {
    Write-Info 'Uninstalling clawcodex...'
    Write-Info "  Install dir : $ClawcodexHome"
    Write-Info "  Local bin   : $LocalBin"

    # Hard safety: never operate on a protected path, regardless of markers.
    $normalized = $ClawcodexHome.TrimEnd('\')
    $protected = @('', $HOME.TrimEnd('\'), [IO.Path]::GetPathRoot($HOME).TrimEnd('\'))
    if ($protected -contains $normalized) {
        throw "INSTALL_FAILED: Refusing to uninstall from a protected path: '$ClawcodexHome'."
    }

    $owned = Test-Path -LiteralPath $MarkerFile

    if ($DryRun) {
        Write-Plain "[DRY-RUN] would remove shims $LocalBin\clawcodex.cmd + clawcodex (only if they point into $ClawcodexHome\)"
        if ($owned) {
            Write-Plain "[DRY-RUN] would remove install dir: $ClawcodexHome"
            Write-Plain "[DRY-RUN] would remove $ClawcodexParent only if it is empty afterwards"
        } else {
            Write-Plain "[DRY-RUN] would SKIP $ClawcodexHome (no .clawcodex-install marker - not created by this installer)"
        }
        return
    }

    foreach ($shimName in @('clawcodex.cmd', 'clawcodex')) {
        $shim = Join-Path $LocalBin $shimName
        if (Test-Path -LiteralPath $shim) {
            $content = Get-Content -LiteralPath $shim -Raw -ErrorAction SilentlyContinue
            $homeFwd = $ClawcodexHome -replace '\\', '/'
            if ($content -and (($content -like "*$ClawcodexHome\*") -or ($content -like "*$homeFwd/*"))) {
                Remove-Item -LiteralPath $shim -Force
                Write-Ok "Removed $shim"
            } else {
                Write-Warn2 "Skipped $shim - does not point inside $ClawcodexHome (other install?)"
            }
        }
    }

    if (-not $owned) {
        Write-Warn2 "Skipped $ClawcodexHome - no .clawcodex-install marker found."
        Write-Warn2 '  This directory was not created by this installer, so it is left untouched.'
        Write-Warn2 '  Remove it manually if you are sure.'
        Write-Ok 'Uninstall complete (shims only).'
        return
    }

    if (Test-Path -LiteralPath $ClawcodexHome) {
        Remove-TreeRobust $ClawcodexHome
        Write-Ok "Removed $ClawcodexHome"
    }
    # Only auto-remove the parent if empty. ~\.clawcodex usually still holds
    # the user's config.json / sessions / skills - preserved by design.
    if ((Test-Path -LiteralPath $ClawcodexParent) -and
        -not (Get-ChildItem -LiteralPath $ClawcodexParent -Force | Select-Object -First 1)) {
        Remove-Item -LiteralPath $ClawcodexParent -Force
        Write-Ok "Removed empty $ClawcodexParent"
    } elseif (Test-Path -LiteralPath $ClawcodexParent) {
        Write-Warn2 "Preserved $ClawcodexParent  (holds your config/sessions; delete manually if desired)"
    }

    Write-Warn2 "Note: the user-PATH entry for $LocalBin is left in place (other tools"
    Write-Warn2 'like uv share it). Remove it via Settings > Environment Variables if desired.'
    Write-Ok 'Uninstall complete.'
}

# ============================================================================
#  Help
# ============================================================================
function Show-Help {
    Write-Host @"
clawcodex Windows installer v$INSTALLER_VERSION (install.ps1)

USAGE
    irm https://clawcodex.app/install.ps1 | iex                # install
    powershell -File install.ps1 [SUBCOMMAND] [OPTIONS]        # from a checkout

SUBCOMMANDS
    (none) / install   Install clawcodex (default action).
    status             Show current install state - no side effects.
    doctor             Diagnose the environment (git, git-bash, python,
                       network, disk, permissions) - no side effects.
    verify             Health-check an existing install - no side effects.
    update             Pull latest from the configured ref and reinstall deps.
    uninstall          Remove everything this installer created.
    help               Show this help.

OPTIONS
    -Ref <ref>           Git ref to install (commit SHA, tag, or branch).
                         Default: main (or `$env:CLAWCODEX_REF).
    -InstallDir <path>   Override the project clone + venv location.
                         Default: $DefaultInstallDir
    -NoSetup             Skip the post-install "next steps" pointer.
    -DryRun              Preview every change without applying it.
    -Yes                 Assume 'yes' for any interactive prompts.
    -LogFile <path>      Transcript all output to <path>.
    -Uninstall           Alias for the 'uninstall' subcommand.
    -Help                Show this help.
    -Version             Print installer version.

DEFAULTS
    Repo         : $REPO_URL
    Git ref      : $RepoRef
    Install path : $DefaultInstallDir
    Python       : >= $PYTHON_MIN_VERSION  (provisioned by uv if missing)
    Tooling      : uv (Astral's package manager - user-local, no admin)

NOTES
    - Requires Git for Windows (the runtime Bash tool uses Git Bash).
    - Re-running is safe: existing repos are fast-forwarded, existing venvs
      reused, shims regenerated.
    - Linux / macOS / WSL / Git Bash: use install.sh instead
      (curl -fsSL https://clawcodex.app/install.sh | bash).

EXIT CODES
    0    Success.
    1    Installation / verification / doctor found a problem.
"@
}

# ============================================================================
#  Install pipeline
# ============================================================================
function Invoke-Install {
    Write-Host "clawcodex Windows installer v$INSTALLER_VERSION" -ForegroundColor White
    Write-Plain "  OS:          Windows $([Environment]::OSVersion.Version) / PowerShell $($PSVersionTable.PSVersion)"
    Write-Plain "  Install dir: $ClawcodexHome"
    Write-Plain "  Git ref:     $RepoRef"
    if ($DryRun) { Write-Plain '  Mode:        DRY-RUN (no changes will be made)' }
    if ($LogFile) { Write-Plain "  Log file:    $LogFile" }

    Write-Step '1/8  Checking prerequisites'
    Test-Git
    $gitBash = Find-GitBash
    if ($gitBash) {
        Write-Ok "Git Bash: $gitBash"
    } else {
        Write-Warn2 'Git Bash not found - clawcodex''s shell tool needs it at runtime.'
        Write-Warn2 '  Standard Git for Windows installs include it; a minimal/MinGit does not.'
    }

    Write-Step '2/8  Installing uv (Astral, user-local)'
    Add-ToProcessPath $LocalBin
    Install-Uv

    Write-Step "3/8  Provisioning Python $PYTHON_MIN_VERSION+"
    Confirm-Python

    Write-Step '4/8  Cloning / updating repository'
    Sync-Repo

    Write-Step '5/8  Creating virtual environment'
    New-Venv

    Write-Step '6/8  Installing dependencies'
    Install-Deps

    Write-Step '7/8  Registering global command & updating PATH'
    Register-Commands

    Write-Step '8/8  Building the Ink TUI client (node + dist)'
    Build-Tui

    Write-Plain ''
    Write-Ok 'Installation complete!'
    Write-Plain ''
    Write-Plain "  Installed at:  $ClawcodexHome"
    Write-Plain "  Command:       $LocalBin\clawcodex.cmd"
    Write-Plain ''

    if (-not $NoSetup) {
        Write-Ok 'Next, configure a provider + API key:'
        Write-Plain '    clawcodex login        # interactive provider + key setup'
        Write-Plain '    clawcodex              # start the interactive TUI in any project'
        Write-Plain '    clawcodex -p "hi"      # headless one-shot'
        Write-Plain ''
    }
    Write-Warn2 'Open a NEW terminal so the PATH update takes effect.'
}

# ============================================================================
#  Entry point
# ============================================================================
if ($Help) { Show-Help; return }
if ($Version) { Write-Host "install.ps1 v$INSTALLER_VERSION"; return }
if ($Uninstall) { $Subcommand = 'uninstall' }
if (-not $Subcommand) { $Subcommand = 'install' }

if ($LogFile) {
    $logDir = Split-Path -Parent $LogFile
    if ($logDir -and -not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    }
    try { Start-Transcript -Path $LogFile -Append | Out-Null } catch { Write-Warn2 "Could not start transcript: $_" }
}

$exitCode = 0
try {
    switch ($Subcommand) {
        'install'   { Invoke-Install }
        'status'    { Invoke-Status }
        'doctor'    { $exitCode = Invoke-Doctor }
        'verify'    { $exitCode = Invoke-Verify }
        'update'    { Invoke-Update }
        'uninstall' { Invoke-Uninstall }
        'help'      { Show-Help }
    }
} catch {
    if ("$_" -notlike 'INSTALL_FAILED:*') { Write-Err "Installer crash: $_" }
    $exitCode = 1
} finally {
    if ($Subcommand -ne 'help') { Write-ExitSummary ($exitCode -eq 0) $exitCode }
    if ($LogFile) { try { Stop-Transcript | Out-Null } catch { } }
}

if ($exitCode -ne 0) { exit $exitCode }
