#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  KodeKey installer — Linux / macOS
#
#  Usage:
#    bash install.sh                 install or update
#    bash install.sh --uninstall     remove KodeKey
#    bash install.sh --help          all options
#
#  Put this file in the same folder as kkey.py, then run it.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── pretty output ────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  B=$(tput bold 2>/dev/null || echo "");   R=$(tput sgr0 2>/dev/null || echo "")
  GRN=$(tput setaf 2 2>/dev/null || echo ""); RED=$(tput setaf 1 2>/dev/null || echo "")
  YLW=$(tput setaf 3 2>/dev/null || echo ""); CYN=$(tput setaf 6 2>/dev/null || echo "")
else
  B=""; R=""; GRN=""; RED=""; YLW=""; CYN=""
fi
ok()   { echo "  ${GRN}✔${R} $*"; }
info() { echo "  ${CYN}→${R} $*"; }
warn() { echo "  ${YLW}⚠${R} $*"; }
die()  { echo "  ${RED}✖ $*${R}" >&2; exit 1; }

usage() {
  cat <<'USAGE'
KodeKey installer (Linux / macOS)

  bash install.sh [options]

Options:
  --dir <path>       Install directory   (default: ~/kkey, env override: KKEY_HOME)
  --bin <path>       Launcher directory  (default: ~/.local/bin, env override: KKEY_BIN)
  --python <exe>     Use a specific Python 3.9+ interpreter
  --link             Symlink kkey.py instead of copying (edits propagate)
  --no-rich          Skip the optional `rich` package
  --uninstall, -u    Remove KodeKey
  --force, -f        With --uninstall: delete data without asking
  --help, -h         This help
USAGE
}

# ── defaults & argument parsing ──────────────────────────────────────────────
INSTALL_DIR="${KKEY_HOME:-$HOME/kkey}"
BIN_DIR="${KKEY_BIN:-$HOME/.local/bin}"
PYEXE=""; UNINSTALL=0; FORCE=0; LINK=0; NO_RICH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)
      INSTALL_DIR="${2:-}"; [ -n "$INSTALL_DIR" ] || die "--dir requires a path"; shift 2 ;;
    --bin)
      BIN_DIR="${2:-}"; [ -n "$BIN_DIR" ] || die "--bin requires a path"; shift 2 ;;
    --python)
      PYEXE="${2:-}"; [ -n "$PYEXE" ] || die "--python requires an interpreter"; shift 2 ;;
    --link)          LINK=1; shift ;;
    --no-rich)       NO_RICH=1; shift ;;
    --uninstall|-u)  UNINSTALL=1; shift ;;
    --force|-f)      FORCE=1; shift ;;
    --help|-h)       usage; exit 0 ;;
    *)               die "Unknown option: $1 (try --help)" ;;
  esac
done

# ── uninstall ────────────────────────────────────────────────────────────────
if [ "$UNINSTALL" -eq 1 ]; then
  echo "  ${B}Uninstalling KodeKey…${R}"

  if [ -e "$BIN_DIR/kkey" ]; then
    rm -f "$BIN_DIR/kkey"; ok "Removed launcher $BIN_DIR/kkey"
  else
    warn "No launcher found at $BIN_DIR/kkey"
  fi

  ask() {
    if [ "$FORCE" -eq 1 ]; then return 0; fi
    printf "  Remove %s? [y/N] " "$1"
    read -r ans || return 1
    case "$ans" in [yY] | [yY][eE][sS]) return 0 ;; *) return 1 ;; esac
  }

  if ask "$INSTALL_DIR (program + virtualenv)"; then
    rm -rf "$INSTALL_DIR"; ok "Removed $INSTALL_DIR"
  fi
  if ask "$HOME/.kkey (config, global memory, history)"; then
    rm -rf "$HOME/.kkey"; ok "Removed $HOME/.kkey"
  fi

  echo
  echo "  ${DIM:-}Note: project-level .kkey/ folders and the PATH line in your shell rc were left in place.${R}"
  exit 0
fi

# ── locate kkey.py ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
SRC="$SCRIPT_DIR/kkey.py"
[ -f "$SRC" ] || die "kkey.py not found next to install.sh — put both files in the same folder"

# ── find Python 3.9+ ─────────────────────────────────────────────────────────
if [ -z "$PYEXE" ]; then
  for c in python3 python python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        PYEXE="$c"; break
      fi
    fi
  done
fi
[ -n "$PYEXE" ] || die "Python 3.9+ not found. Install it first: https://www.python.org/downloads/"
"$PYEXE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1 \
  || die "$PYEXE is not a working Python 3.9+ interpreter"
PYVER="$("$PYEXE" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
ok "Found Python $PYVER ($PYEXE)"

# ── install files ────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
if [ "$(cd "$SCRIPT_DIR" && pwd)" = "$(cd "$INSTALL_DIR" && pwd)" ]; then
  ok "kkey.py already in place ($INSTALL_DIR/kkey.py)"
elif [ "$LINK" -eq 1 ]; then
  ln -sf "$SRC" "$INSTALL_DIR/kkey.py"; ok "Linked $SRC → $INSTALL_DIR/kkey.py"
else
  cp -f "$SRC" "$INSTALL_DIR/kkey.py"; ok "Copied kkey.py → $INSTALL_DIR/kkey.py"
fi

# ── virtualenv + packages ────────────────────────────────────────────────────
VENV="$INSTALL_DIR/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  if [ -d "$VENV" ]; then rm -rf "$VENV"; fi
  info "Creating virtual environment…"
  "$PYEXE" -m venv "$VENV" || die "venv creation failed (Debian/Ubuntu: sudo apt install python3-venv)"
fi
VPY="$VENV/bin/python"

"$VPY" -m pip install --upgrade pip -q 2>/dev/null || warn "pip self-upgrade skipped"

info "Installing openai…"
"$VPY" -m pip install -q openai || die "failed to install the 'openai' package"
ok "openai installed"

if [ "$NO_RICH" -eq 0 ]; then
  info "Installing rich (pretty output)…"
  if "$VPY" -m pip install -q rich; then ok "rich installed"
  else warn "rich failed — KodeKey will use plain ANSI output"; fi
fi

# ── launcher ─────────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/kkey" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$INSTALL_DIR/kkey.py" "\$@"
EOF
chmod +x "$BIN_DIR/kkey"
ok "Launcher → $BIN_DIR/kkey"

# ── PATH setup ───────────────────────────────────────────────────────────────
PATH_UPDATED=0
case ":$PATH:" in
  *":$BIN_DIR:"*)
    ok "PATH: $BIN_DIR already configured"
    ;;
  *)
    RC_FILE=""
    case "${SHELL##*/}" in
      zsh)  RC_FILE="$HOME/.zshrc" ;;
      bash) RC_FILE="$HOME/.bashrc" ;;
      fish) RC_FILE="fish" ;;
      *)    RC_FILE="$HOME/.profile" ;;
    esac
    if [ "$RC_FILE" = "fish" ]; then
      warn "fish detected — run this once:  fish_add_path $BIN_DIR"
      PATH_UPDATED=2
    elif [ -n "$RC_FILE" ]; then
      if ! grep -qF -- "$BIN_DIR" "$RC_FILE" 2>/dev/null; then
        { echo ""; echo "# Added by KodeKey installer"; echo "export PATH=\"$BIN_DIR:\$PATH\""; } >> "$RC_FILE"
        ok "PATH: added $BIN_DIR to $RC_FILE"
        PATH_UPDATED=1
      else
        ok "PATH: $RC_FILE already references $BIN_DIR"
      fi
    fi
    ;;
esac

# ── optional: ripgrep hint ───────────────────────────────────────────────────
if command -v rg >/dev/null 2>&1; then
  ok "ripgrep found — fast search enabled"
else
  info "Optional, for faster code search:"
  case "$(uname -s)" in
    Darwin) echo "        brew install ripgrep" ;;
    *)
      if   command -v apt-get >/dev/null 2>&1; then echo "        sudo apt install ripgrep"
      elif command -v dnf     >/dev/null 2>&1; then echo "        sudo dnf install ripgrep"
      elif command -v pacman  >/dev/null 2>&1; then echo "        sudo pacman -S ripgrep"
      elif command -v zypper  >/dev/null 2>&1; then echo "        sudo zypper install ripgrep"
      fi ;;
  esac
fi

# ── verify ───────────────────────────────────────────────────────────────────
info "Verifying…"
if OUT="$("$BIN_DIR/kkey" --version 2>&1)"; then
  ok "Verified: $OUT"
else
  warn "Self-test failed: $OUT"
fi

# ── summary ──────────────────────────────────────────────────────────────────
echo
echo "  ${B}════════════════════════════════════════${R}"
echo "  ${GRN}${B} KodeKey installed!${R}"
echo "  ${B}════════════════════════════════════════${R}"
echo
echo "  1. Set your API key (pick one):"
echo "       export KODEKEY_API_KEY='sk-your-key-here'   # add to ~/.bashrc or ~/.zshrc"
echo "     or:  mkdir -p ~/.kkey && printf '{\"api_key\": \"sk-your-key-here\"}' > ~/.kkey/config.json"
echo
echo "  2. Start it from any project folder:"
echo "       kkey"
if [ "$PATH_UPDATED" -eq 1 ]; then
  echo
  echo "  ${YLW}⚠  Open a new terminal first (or: source $RC_FILE) so the PATH change applies.${R}"
elif [ "$PATH_UPDATED" -eq 2 ]; then
  echo
  echo "  ${YLW}⚠  Run fish_add_path $BIN_DIR, then restart your terminal.${R}"
fi
echo