<#
.SYNOPSIS
  KodeKey installer for Windows.

.DESCRIPTION
  Put this file in the same folder as kkey.py, then run:

      powershell -ExecutionPolicy Bypass -File .\install.ps1

  To remove KodeKey:

      powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
#>
param(
  [switch]$Uninstall,
  [switch]$Force,
  [string]$InstallDir = "",
  [string]$BinDir = "",
  [switch]$NoRich
)

 $ErrorActionPreference = "Stop"
 $Script:NeedRestart = $false

# ── output helpers (ASCII markers for legacy consoles) ──────────────────────
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Info($m) { Write-Host "  [..] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  [X]  $m" -ForegroundColor Red; exit 1 }

if (-not $InstallDir) { $InstallDir = "$env:USERPROFILE\kkey" }
if (-not $BinDir)     { $BinDir     = "$env:USERPROFILE\bin" }

# ── find Python 3.9+ ─────────────────────────────────────────────────────────
function Find-Python {
  $candidates = @(
    @{ Exe = "py";      Args = @("-3") },
    @{ Exe = "python";  Args = @() },
    @{ Exe = "python3"; Args = @() }
  )
  foreach ($cand in $candidates) {
    $cmd = Get-Command $cand.Exe -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
      $a   = $cand.Args
      $out = (& $cmd.Source @a --version 2>&1) -join " "
      if ($out -match "Python\s+(\d+)\.(\d+)") {
        $maj = [int]$Matches[1]; $min = [int]$Matches[2]
        if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 9)) {
          return @{ Exe = $cmd.Source; Args = $cand.Args }
        }
      }
    } catch { continue }
  }
  return $null
}

function Pip([string[]]$Packages) {
  & $Vpy -m pip install -q @Packages
  return ($LASTEXITCODE -eq 0)
}

function Confirm-Remove([string]$Target) {
  if ($Force) { return $true }
  $answer = Read-Host "  Remove $Target? [y/N]"
  return ($answer -match "^[Yy]")
}

# ── uninstall ────────────────────────────────────────────────────────────────
if ($Uninstall) {
  Write-Host ""
  Write-Host "  Uninstalling KodeKey..."

  $launcher = Join-Path $BinDir "kkey.cmd"
  if (Test-Path $launcher) { Remove-Item $launcher -Force; Ok "Removed $launcher" }
  else { Warn "No launcher found at $launcher" }

  # Remove the bin dir + PATH entry only if we left it empty
  if ((Test-Path $BinDir) -and -not (Get-ChildItem $BinDir -Force -ErrorAction SilentlyContinue)) {
    Remove-Item $BinDir -Force
    $paths = ([Environment]::GetEnvironmentVariable("Path", "User") -split ";") |
             Where-Object { $_ -and ($_.TrimEnd("\") -ine $BinDir.TrimEnd("\")) }
    [Environment]::SetEnvironmentVariable("Path", ($paths -join ";"), "User")
    Ok "Removed $BinDir from user PATH"
  }

  if (Confirm-Remove "$InstallDir (program + virtualenv)") {
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    Ok "Removed $InstallDir"
  }
  if (Confirm-Remove "$env:USERPROFILE\.kkey (config, global memory, history)") {
    if (Test-Path "$env:USERPROFILE\.kkey") { Remove-Item -Recurse -Force "$env:USERPROFILE\.kkey" }
    Ok "Removed $env:USERPROFILE\.kkey"
  }

  Write-Host ""
  Write-Host "  Note: project-level .kkey folders were left in place." -ForegroundColor DarkGray
  exit 0
}

# ── install ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Installing KodeKey for Windows" -ForegroundColor Cyan
Write-Host ""

# 1. Python
 $Python = Find-Python
if (-not $Python) {
  Die "Python 3.9+ not found. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'), then re-run."
}
 $pyExe  = $Python.Exe
 $pyArgs = $Python.Args
 $pyVer  = (& $pyExe @pyArgs --version) -join ""
Ok "Found $pyVer"

# 2. Install directory + kkey.py
 $Src = Join-Path $PSScriptRoot "kkey.py"
if (-not (Test-Path $Src)) { Die "kkey.py not found next to install.ps1 - put both files in the same folder" }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
 $Dst = Join-Path $InstallDir "kkey.py"
if (-not ($Src -ieq $Dst)) { Copy-Item $Src $Dst -Force; Ok "Copied kkey.py -> $Dst" }
else { Ok "kkey.py already in place ($Dst)" }

# 3. Virtual environment
 $Vpy = "$InstallDir\.venv\Scripts\python.exe"
if (-not (Test-Path $Vpy)) {
  if (Test-Path "$InstallDir\.venv") { Remove-Item -Recurse -Force "$InstallDir\.venv" }
  Info "Creating virtual environment..."
  & $pyExe @pyArgs -m venv "$InstallDir\.venv"
  if ($LASTEXITCODE -ne 0) { Die "venv creation failed" }
} else {
  Info "Reusing existing virtual environment"
}

# 4. Packages (openai required; the rest are best-effort)
try { & $Vpy -m pip install --upgrade pip -q 2>$null } catch { Warn "pip self-upgrade skipped" }

Info "Installing openai..."
if (-not (Pip @("openai"))) { Die "Failed to install the 'openai' package" }
Ok "openai installed"

if (-not $NoRich) {
  Info "Installing rich (pretty output)..."
  if (Pip @("rich")) { Ok "rich installed" }
  else { Warn "rich failed - KodeKey will use plain ANSI output" }
}

Info "Installing pyreadline3 (arrow-key history + Tab completion)..."
if (Pip @("pyreadline3")) { Ok "pyreadline3 installed" }
else { Warn "pyreadline3 not available on this Python - history/Tab completion disabled (everything else works)" }

# 5. Launcher
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
 $launcherPath = Join-Path $BinDir "kkey.cmd"
 $launcherBody = "@echo off`r`n`"$InstallDir\.venv\Scripts\python.exe`" `"$InstallDir\kkey.py`" %*"
Set-Content -Path $launcherPath -Value $launcherBody -Encoding ASCII
Ok "Created launcher -> $launcherPath"

# 6. PATH
 $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
 $already  = ($userPath -split ";") | Where-Object { $_ } |
            Where-Object { $_.TrimEnd("\") -ieq $BinDir.TrimEnd("\") }
if (-not $already) {
  $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $BinDir } else { "$userPath;$BinDir" }
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  Ok "Added $BinDir to user PATH"
  $Script:NeedRestart = $true
} else {
  Ok "$BinDir already on PATH"
}

# 7. UTF-8 mode (prevents UnicodeEncodeError with emoji output)
if (-not [Environment]::GetEnvironmentVariable("PYTHONUTF8", "User")) {
  [Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
  Ok "Set PYTHONUTF8=1 (proper Unicode/emoji output)"
  $Script:NeedRestart = $true
}

# 8. Verify
Info "Verifying installation..."
try {
  $ver = (& $launcherPath --version 2>&1) -join " "
  if ($LASTEXITCODE -eq 0 -and $ver) { Ok "Verified: $ver" }
  else { Warn "Self-test returned: $ver" }
} catch {
  Warn "Self-test could not run: $_"
}

# 9. Optional ripgrep
if (-not (Get-Command rg -ErrorAction SilentlyContinue)) {
  Info "Optional, for faster code search:  winget install BurntSushi.ripgrep.MSVC"
}

# 10. Summary
Write-Host ""
Write-Host "  ==============================================" -ForegroundColor Cyan
Write-Host "   KodeKey installed!" -ForegroundColor Green
Write-Host "  ==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Set your API key (pick one):"
Write-Host '       setx KODEKEY_API_KEY "sk-your-key-here"'
Write-Host "     or create %USERPROFILE%\.kkey\config.json containing:"
Write-Host '       { "api_key": "sk-your-key-here" }'
Write-Host ""
Write-Host "  2. Open a NEW terminal, cd into a project, then run:"
Write-Host "       kkey"
if ($Script:NeedRestart) {
  Write-Host ""
  Write-Host "  NOTE: open a NEW terminal so PATH / UTF-8 changes take effect." -ForegroundColor Yellow
}
Write-Host ""