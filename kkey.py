#!/usr/bin/env python3
"""
KodeKey 2.0 — Claude Code-style terminal AI coding agent.

New in 2.0:
  • Streaming responses (with reasoning-token display)
  • One-shot mode:  kkey "fix the failing tests"  (supports stdin pipes, -c continue)
  • File checkpoints: /undo, /diff  (every agent file change is tracked & reversible)
  • @-mentions: attach files/dirs to a prompt (@src/, @main.py)
  • Context management: /compact + automatic compaction on huge contexts
  • /init — scan the codebase & write project memory automatically
  • /plan mode, /retry, /export, /stats (tokens + cost estimate), /todos
  • Custom slash commands: .kkey/commands/*.md  ($ARGUMENTS, $1..$9)
  • New tools: glob, fetch_url, update_todos; bash timeout up to 30 min
  • Tab-completion (commands, session names, @paths), persistent input history
  • All previous file/command restrictions removed (opt-in "safe_mode" in config)
  • API retry with backoff, graceful Ctrl-C that never corrupts the conversation
"""

import os
import sys
import json
import re
import time
import argparse
import atexit
import datetime
import difflib
import fnmatch
import platform
import shutil
import subprocess
import threading
import itertools
import html as html_mod
from pathlib import Path
from urllib.request import Request, urlopen

from openai import OpenAI

try:
    import readline  # noqa: F401  (history + tab completion)
except ImportError:
    readline = None

# ── Rich UI (graceful fallback to ANSI) ──────────────────────────────────────
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    class Console:
        def print(self, *a, **kw): print(*a)
        def rule(self, *a, **kw): print("─" * 60)
        def status(self, *a, **kw): return _NoStatus()
    class _NoStatus:
        def start(self): pass
        def stop(self): pass
    console = Console()

# ── ANSI helpers ──────────────────────────────────────────────────────────────
R   = "\033[0m"
B   = "\033[1m"
DIM = "\033[2m"
CYN = "\033[36m"
GRN = "\033[32m"
YLW = "\033[33m"
RED = "\033[31m"
MGT = "\033[35m"
BLU = "\033[34m"

def c(text, col): return f"{col}{text}{R}"

def rl(text, col):
    """Readline-safe colored text (wraps escapes in \001..\002 so editing works)."""
    return f"\001{col}\002{text}\001{R}\002"

VERSION = "2.0.0"

# ── Storage paths / config ────────────────────────────────────────────────────
KKEY_GLOBAL_DIR  = Path.home() / ".kkey"
GLOBAL_MEMORY    = KKEY_GLOBAL_DIR / "memory.md"
CONFIG_PATH      = KKEY_GLOBAL_DIR / "history"  # placeholder, real config below
CONFIG_PATH      = KKEY_GLOBAL_DIR / "config.json"
HISTORY_FILE     = KKEY_GLOBAL_DIR / "history"

DEFAULT_CONFIG = {
    "default_model":  "",
    "stream":         True,      # stream tokens as they arrive
    "show_reasoning": True,      # display reasoning tokens (dim) if the model sends them
    "bash_timeout":   300,       # default bash tool timeout (seconds)
    "context_chars":  600000,    # auto-compact threshold (~150k tokens)
    "max_read_lines": 2000,      # read_file paging default
    "safe_mode":      False,     # re-enable the old file/command blocks if you ever want them
}

RAW_CONFIG = {}
try:
    RAW_CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
except Exception:
    RAW_CONFIG = {}

CONFIG = dict(DEFAULT_CONFIG)
for _k in DEFAULT_CONFIG:
    if _k in RAW_CONFIG:
        CONFIG[_k] = RAW_CONFIG[_k]

if os.environ.get("KKEY_STREAM") == "0":
    CONFIG["stream"] = False
if os.environ.get("KKEY_STREAM") == "1":
    CONFIG["stream"] = True

def save_config():
    try:
        KKEY_GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        data = dict(RAW_CONFIG)
        data.update(CONFIG)
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  {GRN}✔ Saved → {CONFIG_PATH}{R}")
    except Exception as e:
        print(f"  {RED}✖ Could not save config: {e}{R}")

def project_kkey_dir() -> Path:
    d = Path(os.getcwd()) / ".kkey"
    d.mkdir(parents=True, exist_ok=True)
    return d

def project_memory_path() -> Path:
    return project_kkey_dir() / "memory.md"

def sessions_dir() -> Path:
    d = project_kkey_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d

# ── Environment ───────────────────────────────────────────────────────────────
api_key  = (os.environ.get("KODEKEY_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or str(RAW_CONFIG.get("api_key", "") or ""))
base_url = (os.environ.get("KODEKEY_BASE_URL")
            or str(RAW_CONFIG.get("base_url", "") or ""))

client = None  # created in main() after the key check (so --help/--version work without a key)

AVAILABLE_MODELS: dict = {}
current_model = ""
PLAN_MODE = False
USAGE = {"prompt": 0, "completion": 0}
TODOS: list = []

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv", "env",
    "dist", "build", "target", ".next", "vendor", ".mypy_cache",
    ".pytest_cache", ".tox", ".idea", ".vscode", "coverage", ".cache",
}

# ── Optional safe mode (OFF by default — restrictions were removed) ──────────
SENSITIVE_PATTERNS = [
    r'(^|/)\.env[^/]*$', r'(^|/)\.git(/|$)', r'(^|/)\.gitconfig$',
    r'(^|/)\.ssh(/|$)', r'(^|/)id_rsa[^/]*$', r'.*\.(key|pem|p12|secret)$',
]
BANNED_COMMAND_TOKENS = [
    r'\brm\s+-[rR]*[fF]*\s+/', r'\bmkfs\b', r'\bdd\b\s+if=',
    r'\bshutdown\b', r'\breboot\b', r'\bpoweroff\b',
]

def is_file_blocked(filepath: str) -> bool:
    if not CONFIG["safe_mode"]:
        return False
    if ".." in filepath:
        return True
    clean = filepath.strip().lower()
    fname = os.path.basename(clean)
    return any(re.search(p, clean) or re.search(p, fname) for p in SENSITIVE_PATTERNS)

def is_command_blocked(command: str) -> bool:
    return CONFIG["safe_mode"] and any(re.search(p, command.strip().lower()) for p in BANNED_COMMAND_TOKENS)

# ════════════════════════════════════════════════════════════════════════════════
# MEMORY SYSTEM
# ════════════════════════════════════════════════════════════════════════════════

MEMORY_TEMPLATE = """\
# KodeKey Memory
_This file is automatically read at startup and injected into the agent's context._
_The agent can update this file. Edit it manually anytime._

## Facts
<!-- Add persistent facts below. e.g. "Prefers async/await over callbacks." -->

## Project Notes
<!-- Tech stack, conventions, architecture decisions, etc. -->

## Preferences
<!-- Coding style, language, framework preferences, etc. -->
"""

def ensure_memory_files():
    KKEY_GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_MEMORY.exists():
        GLOBAL_MEMORY.write_text(
            "# KodeKey Global Memory\n"
            "_User-level preferences and facts that apply across all projects._\n\n"
            "## Preferences\n\n"
            "## Personal Notes\n"
        )
    proj_mem = project_memory_path()
    if not proj_mem.exists():
        proj_mem.write_text(MEMORY_TEMPLATE)

def load_memory() -> tuple:
    ensure_memory_files()
    g = GLOBAL_MEMORY.read_text(encoding="utf-8").strip()
    p = project_memory_path().read_text(encoding="utf-8").strip()
    return g, p

def build_memory_block() -> str:
    g, p = load_memory()
    parts = []
    if g:
        parts.append(f"<global_memory>\n{g}\n</global_memory>")
    if p:
        parts.append(f"<project_memory>\n{p}\n</project_memory>")
    if parts:
        return "\n\n---\n# Persistent Memory (read at startup)\n" + "\n\n".join(parts)
    return ""

def tool_update_memory(scope: str, content: str) -> str:
    scope = (scope or "").lower().strip()
    if scope == "global":
        path = GLOBAL_MEMORY
    elif scope == "project":
        path = project_memory_path()
    else:
        return f"Error: scope must be 'global' or 'project', got '{scope}'."
    try:
        path.write_text(content, encoding="utf-8")
        return f"✅ Updated {scope} memory ({content.count(chr(10)) + 1} lines) → {path}"
    except Exception as e:
        return f"Error updating memory: {e}"

def tool_read_memory(scope: str = "both") -> str:
    g, p = load_memory()
    scope = (scope or "both").lower().strip()
    if scope == "global":
        return f"## Global Memory ({GLOBAL_MEMORY})\n\n{g}"
    if scope == "project":
        return f"## Project Memory ({project_memory_path()})\n\n{p}"
    return (f"## Global Memory ({GLOBAL_MEMORY})\n\n{g}\n\n"
            f"## Project Memory ({project_memory_path()})\n\n{p}")

# ════════════════════════════════════════════════════════════════════════════════
# CHANGE TRACKING (checkpoints for /undo and /diff)
# ════════════════════════════════════════════════════════════════════════════════

class ChangeLog:
    def __init__(self, entries=None):
        self.entries = entries or []

    def record(self, action: str, path: str, before):
        """before = original content (str) or None if the file didn't exist."""
        self.entries.append({"action": action, "path": path, "before": before})

    def undo(self) -> str:
        if not self.entries:
            return "Nothing to undo."
        e = self.entries.pop()
        path = e["path"]
        try:
            if e["before"] is None:
                if os.path.exists(path):
                    os.remove(path)
                return f"↩ Undid {e['action']} — removed created file {path}"
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(e["before"], encoding="utf-8")
            return f"↩ Undid {e['action']} — restored {path}"
        except Exception as ex:
            return f"Error undoing {path}: {ex}"

    def iter_diffs(self):
        first = {}
        for e in self.entries:
            first.setdefault(e["path"], e)
        for path, e in first.items():
            cur = None
            try:
                if os.path.exists(path):
                    with open(path, encoding="utf-8", errors="replace") as f:
                        cur = f.read()
            except Exception:
                cur = None
            if e.get("before") is None:
                if cur is None:
                    continue
                yield path, f"(new file, {len(cur.splitlines())} lines)"
            elif cur is None:
                yield path, "(file deleted)"
            elif cur != e["before"]:
                d = difflib.unified_diff(
                    e["before"].splitlines(), cur.splitlines(),
                    lineterm="", fromfile=f"a/{path}", tofile=f"b/{path}")
                yield path, "\n".join(d)

# ════════════════════════════════════════════════════════════════════════════════
# SESSION SYSTEM
# ════════════════════════════════════════════════════════════════════════════════

def save_session(sess, name: str) -> str:
    serialisable = [m for m in sess.messages if m.get("role") != "system"]
    payload = {
        "name":      name,
        "model":     current_model,
        "timestamp": datetime.datetime.now().isoformat(),
        "workspace": os.getcwd(),
        "messages":  serialisable,
        "changes":   sess.changes.entries,
        "todos":     TODOS,
        "usage":     USAGE,
    }
    path = sessions_dir() / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return str(path)

def load_session(name: str) -> dict:
    path = sessions_dir() / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Session '{name}' not found. Use /sessions to list.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Could not load session '{name}': {e}")

def list_sessions() -> list:
    results = []
    for f in sorted(sessions_dir().glob("*.json")):
        try:
            data = json.loads(f.read_text())
            results.append({
                "name":      data.get("name", f.stem),
                "timestamp": data.get("timestamp", "?"),
                "model":     data.get("model", "?"),
                "turns":     len([m for m in data.get("messages", []) if m.get("role") == "user"]),
            })
        except Exception:
            results.append({"name": f.stem, "timestamp": "?", "model": "?", "turns": "?"})
    results.sort(key=lambda s: str(s.get("timestamp") or ""))
    return results

def delete_session(name: str) -> str:
    path = sessions_dir() / f"{name}.json"
    if not path.exists():
        return f"Session '{name}' not found."
    path.unlink()
    return f"Deleted session '{name}'."

def auto_session_name() -> str:
    return datetime.datetime.now().strftime("session_%Y%m%d_%H%M%S")

def latest_session_name():
    s = list_sessions()
    return s[-1]["name"] if s else None

# ════════════════════════════════════════════════════════════════════════════════
# FILE / SHELL / WEB TOOLS
# ════════════════════════════════════════════════════════════════════════════════

def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

def tool_read_file(filepath: str, offset=None, limit=None) -> str:
    if is_file_blocked(filepath):
        return f"🔒 Blocked (safe mode): '{filepath}' matches a sensitive pattern."
    if not os.path.exists(filepath):
        return f"Error: '{filepath}' does not exist."
    if os.path.isdir(filepath):
        return f"Error: '{filepath}' is a directory. Use list_directory or glob."
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading '{filepath}': {e}"
    if "\x00" in content[:4096]:
        return f"<binary file path=\"{filepath}\" size=\"{len(content)} bytes\" — not displayable>"
    lines = content.splitlines()
    total = len(lines)
    start = max(0, _int(offset, 1) - 1)
    lim = _int(limit, CONFIG["max_read_lines"])
    end = min(total, start + lim)
    numbered = "\n".join(f"{i+1:4} │ {line}" for i, line in enumerate(lines[start:end], start))
    note = ""
    if start > 0 or end < total:
        note = f"\n[showing lines {start+1}–{end} of {total}; call read_file again with offset={end+1} for more]"
    return f'<file path="{filepath}" lines="{total}">\n{numbered}\n</file>{note}'

def tool_write_file(filepath: str, content: str) -> str:
    if is_file_blocked(filepath):
        return f"🔒 Blocked (safe mode): writing to '{filepath}' is not allowed."
    try:
        original = None
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8", errors="replace") as f:
                original = f.read()
        dirpath = os.path.dirname(filepath)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        CHANGES.record("write", filepath, original)
        lines = content.count("\n") + 1
        tag = "new file" if original is None else "overwrote existing"
        return f"Wrote {lines} lines ({len(content)} bytes) → {filepath} ({tag})"
    except Exception as e:
        return f"Error writing '{filepath}': {e}"

def tool_edit_file(filepath: str, old_str: str, new_str: str) -> str:
    if is_file_blocked(filepath):
        return f"🔒 Blocked (safe mode): editing '{filepath}' is not allowed."
    if not os.path.exists(filepath):
        return f"Error: '{filepath}' does not exist. Use write_file to create it."
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            original = f.read()
        count = original.count(old_str)
        if count == 0:
            return ("Error: The string to replace was not found in '{filepath}'.\n"
                    "Tip: Read the file first and copy the exact text.")
        if count > 1:
            return f"Error: The string appears {count} times — make old_str more specific."
        updated = original.replace(old_str, new_str, 1)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
        CHANGES.record("edit", filepath, original)
        return (f"Edited '{filepath}': replaced {old_str.count(chr(10)) + 1} line(s) "
                f"with {new_str.count(chr(10)) + 1} line(s).")
    except Exception as e:
        return f"Error editing '{filepath}': {e}"

def tool_delete_file(filepath: str) -> str:
    if is_file_blocked(filepath):
        return f"🔒 Blocked (safe mode): deleting '{filepath}' is not allowed."
    if not os.path.exists(filepath):
        return f"Error: '{filepath}' does not exist."
    if os.path.isdir(filepath):
        return f"Error: '{filepath}' is a directory. Use bash with rm -r."
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            original = f.read()
        os.remove(filepath)
        CHANGES.record("delete", filepath, original)
        return f"Deleted '{filepath}'. (/undo can restore it)"
    except Exception as e:
        return f"Error deleting '{filepath}': {e}"

def tool_list_directory(path: str = ".", max_depth=None) -> str:
    max_depth = min(_int(max_depth, 3), 10)
    if not os.path.exists(path):
        return f"Error: '{path}' does not exist."
    if os.path.isfile(path):
        return tool_read_file(path)
    lines = [f"{path}/"]
    count = [0]

    def _walk(dirpath, prefix, depth):
        if count[0] > 600:
            return
        try:
            entries = sorted(os.listdir(dirpath))
        except PermissionError:
            lines.append(prefix + "└── [permission denied]")
            return
        visible = [e for e in entries if not e.startswith(".")]
        for i, entry in enumerate(visible):
            if count[0] > 600:
                if count[0] == 601:
                    lines.append(prefix + "… (truncated)")
                    count[0] += 1
                return
            count[0] += 1
            connector = "└── " if i == len(visible) - 1 else "├── "
            full = os.path.join(dirpath, entry)
            if os.path.isdir(full):
                lines.append(f"{prefix}{connector}{entry}/")
                if depth < max_depth and entry not in SKIP_DIRS:
                    ext = "    " if i == len(visible) - 1 else "│   "
                    _walk(full, prefix + ext, depth + 1)
            else:
                try:
                    size = os.path.getsize(full)
                    size_str = f"{size}B" if size < 1024 else f"{size//1024}KB"
                except Exception:
                    size_str = "?"
                lines.append(f"{prefix}{connector}{entry}  ({size_str})")

    _walk(path, "", 1)
    return "\n".join(lines) if len(lines) > 1 else "Empty directory."

def tool_search_files(pattern: str, path: str = ".", file_glob: str = "*", max_results=None) -> str:
    max_results = min(_int(max_results, 100), 500)
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Invalid regex: {e}"

    # ripgrep fast path
    rg = shutil.which("rg")
    if rg:
        try:
            r = subprocess.run(
                [rg, "--no-heading", "-n", "-i", "--max-count", "20", "-g", file_glob, "--", pattern, path],
                capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            if r.returncode in (0, 1):
                found = r.stdout.splitlines()
                if not found:
                    return f"No matches for '{pattern}'."
                shown = found[:max_results]
                more = f"\n… {len(found) - max_results} more" if len(found) > max_results else ""
                return f"Found {len(found)} match(es) [ripgrep]:\n" + "\n".join(shown) + more
        except Exception:
            pass

    results = []
    regex = re.compile(pattern, re.IGNORECASE)
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in sorted(files):
            if not fnmatch.fnmatch(fname, file_glob):
                continue
            fpath = os.path.join(root, fname)
            if is_file_blocked(fpath):
                continue
            try:
                if os.path.getsize(fpath) > 2_000_000:
                    continue
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{os.path.relpath(fpath, path)}:{i}: {line.rstrip()}")
                            if len(results) >= max_results:
                                return (f"Found ≥{len(results)} match(es):\n"
                                        + "\n".join(results) + "\n… (capped — refine pattern)")
            except Exception:
                continue
    if not results:
        return f"No matches for '{pattern}'."
    return f"Found {len(results)} match(es):\n" + "\n".join(results)

def tool_glob(pattern: str, path: str = ".") -> str:
    if not pattern:
        return "Error: pattern required (e.g. '**/*.py')."
    base = Path(path)
    if not base.exists():
        return f"Error: '{path}' does not exist."
    try:
        matches = sorted(p for p in base.glob(pattern)
                         if not any(part in SKIP_DIRS for part in p.parts))
    except Exception as e:
        return f"Error: bad glob pattern: {e}"
    if not matches:
        return f"No files match '{pattern}' under '{path}'."
    shown = matches[:300]
    out = [f"{p}/" if p.is_dir() else str(p) for p in shown]
    note = f"\n… and {len(matches) - 300} more" if len(matches) > 300 else ""
    return f"{len(matches)} match(es):\n" + "\n".join(out) + note

def tool_bash(command: str, timeout=None) -> str:
    if is_command_blocked(command):
        return f"🔒 Blocked (safe mode): '{command}' violates shell security policy."
    tmo = min(max(_int(timeout, CONFIG["bash_timeout"]), 5), 1800)
    try:
        # stdin=DEVNULL → interactive tools (vim/top/htop) exit instantly instead of hanging
        result = subprocess.run(command, shell=True, capture_output=True, text=True,
                                timeout=tmo, cwd=os.getcwd(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {tmo}s. Re-run with a larger timeout param if it needs longer."
    except Exception as e:
        return f"Error: {e}"
    out = result.stdout or ""
    if (result.stderr or "").strip():
        out += ("\n[stderr]\n" if out else "") + result.stderr
    out = out.strip()
    if result.returncode != 0:
        out += f"\n[exit code: {result.returncode}]"
    if not out:
        out = f"(no output, exit {result.returncode})"
    if len(out) > 30000:
        out = out[:30000] + f"\n… (output truncated, {len(out) - 30000} chars omitted)"
    return out

def tool_create_directory(path: str) -> str:
    try:
        os.makedirs(path, exist_ok=True)
        return f"Created: {path}"
    except Exception as e:
        return f"Error: {e}"

def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()

def tool_fetch_url(url: str, max_chars=None) -> str:
    max_chars = min(_int(max_chars, 15000), 60000)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; KodeKeyCLI)"})
        with urlopen(req, timeout=20) as r:
            ctype = r.headers.get("content-type", "")
            raw = r.read(2_000_000)
        text = raw.decode("utf-8", errors="replace")
        if "html" in ctype.lower():
            text = _html_to_text(text)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… (truncated)"
        return f'<url path="{url}">\n{text}\n</url>'
    except Exception as e:
        return f"Error fetching '{url}': {e}"

def render_todos():
    print(f"  {B}📋 Todo list{R}")
    icons = {"done": "✔", "in_progress": "▶", "pending": "○"}
    for t in TODOS:
        st = t.get("status", "pending")
        icon = icons.get(st, "?")
        col = GRN if st == "done" else (YLW if st == "in_progress" else DIM)
        print(f"    {col}{icon}{R} {t.get('content', '')}")
    print()

def tool_update_todos(todos) -> str:
    global TODOS
    clean = []
    for t in todos if isinstance(todos, list) else []:
        if isinstance(t, dict):
            clean.append({"content": str(t.get("content", "")),
                          "status": str(t.get("status", "pending"))})
    TODOS = clean
    render_todos()
    return f"Todo list updated ({len(clean)} items)."

# ── Tool schema ───────────────────────────────────────────────────────────────
TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a file with line numbers. Supports offset/limit for paging large files.", "parameters": {"type": "object", "properties": {"filepath": {"type": "string"}, "offset": {"type": "integer", "description": "1-based line to start from"}, "limit": {"type": "integer", "description": "Max lines to return"}}, "required": ["filepath"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or fully overwrite a file.", "parameters": {"type": "object", "properties": {"filepath": {"type": "string"}, "content": {"type": "string"}}, "required": ["filepath", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Targeted str-replace edit. Read file first. old_str must match exactly once.", "parameters": {"type": "object", "properties": {"filepath": {"type": "string"}, "old_str": {"type": "string", "description": "Exact unique text to replace."}, "new_str": {"type": "string", "description": "Replacement text."}}, "required": ["filepath", "old_str", "new_str"]}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete a file (undoable by the user via /undo).", "parameters": {"type": "object", "properties": {"filepath": {"type": "string"}}, "required": ["filepath"]}}},
    {"type": "function", "function": {"name": "list_directory", "description": "Directory tree (depth-limited, skips node_modules/.git/etc).", "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "max_depth": {"type": "integer", "default": 3}}}}},
    {"type": "function", "function": {"name": "glob", "description": "Find files by glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "default": "."}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "search_files", "description": "Grep-style regex search across files (uses ripgrep when available).", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "default": "."}, "file_glob": {"type": "string", "default": "*"}, "max_results": {"type": "integer", "default": 100}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "bash", "description": "Run shell commands: compile, test, git, lint, install packages, etc. Accepts a timeout in seconds (max 1800) for long builds.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer", "description": "Seconds to wait (default 300, max 1800)"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "create_directory", "description": "Create a directory (recursive).", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a web page / API docs and return readable text.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer", "default": 15000}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "update_todos", "description": "Track progress on multi-step tasks. Replace the whole list each call.", "parameters": {"type": "object", "properties": {"todos": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}}, "required": ["content", "status"]}}}, "required": ["todos"]}}},
    {"type": "function", "function": {"name": "update_memory", "description": "Persist important information so it survives across sessions. Use proactively for preferences, conventions, tech stack, build commands. Provide the FULL updated file content.", "parameters": {"type": "object", "properties": {"scope": {"type": "string", "enum": ["global", "project"]}, "content": {"type": "string"}}, "required": ["scope", "content"]}}},
    {"type": "function", "function": {"name": "read_memory", "description": "Read current memory contents.", "parameters": {"type": "object", "properties": {"scope": {"type": "string", "enum": ["global", "project", "both"], "default": "both"}}}}},
]

TOOL_DISPATCH = {
    "read_file":        lambda a: tool_read_file(a["filepath"], a.get("offset"), a.get("limit")),
    "write_file":       lambda a: tool_write_file(a["filepath"], a["content"]),
    "edit_file":        lambda a: tool_edit_file(a["filepath"], a["old_str"], a["new_str"]),
    "delete_file":      lambda a: tool_delete_file(a["filepath"]),
    "list_directory":   lambda a: tool_list_directory(a.get("path", "."), a.get("max_depth")),
    "glob":             lambda a: tool_glob(a["pattern"], a.get("path", ".")),
    "search_files":     lambda a: tool_search_files(a["pattern"], a.get("path", "."), a.get("file_glob", "*"), a.get("max_results")),
    "bash":             lambda a: tool_bash(a["command"], a.get("timeout")),
    "create_directory": lambda a: tool_create_directory(a["path"]),
    "fetch_url":        lambda a: tool_fetch_url(a["url"], a.get("max_chars")),
    "update_todos":     lambda a: tool_update_todos(a.get("todos") or []),
    "update_memory":    lambda a: tool_update_memory(a["scope"], a["content"]),
    "read_memory":      lambda a: tool_read_memory(a.get("scope", "both")),
}

TOOL_ICONS = {
    "read_file": ("📖", CYN), "write_file": ("✏️ ", GRN), "edit_file": ("🖊️ ", YLW),
    "delete_file": ("🗑️ ", RED), "list_directory": ("📂", BLU), "glob": ("🗂️ ", BLU),
    "search_files": ("🔍", MGT), "bash": ("⚡", YLW), "create_directory": ("📁", BLU),
    "fetch_url": ("🌐", MGT), "update_todos": ("📋", MGT),
    "update_memory": ("🧠", MGT), "read_memory": ("🧠", CYN),
}

PLAN_MUTATING = {"write_file", "edit_file", "delete_file", "create_directory"}

# Global change log (persisted inside sessions)
CHANGES = ChangeLog()

# ── Spinner ───────────────────────────────────────────────────────────────────
class SimpleSpinner:
    def __init__(self, text="Thinking"):
        self.text = text
        self.stop_ = threading.Event()
        self.thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        frames = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
        while not self.stop_.is_set():
            print(f"\r{CYN}{next(frames)}{R}  {self.text}…", end="", flush=True)
            time.sleep(0.08)
        print("\r" + " " * (len(self.text) + 12) + "\r", end="", flush=True)

    def stop(self):
        self.stop_.set()
        try:
            self.thread.join(timeout=1)
        except RuntimeError:
            pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop()

class Spinner:
    """Unified spinner (rich if available). Starts immediately; stop() is idempotent."""
    def __init__(self):
        self._rich = None
        self._simple = None
        try:
            if RICH:
                self._rich = console.status("[dim]Thinking…[/]", spinner="dots")
                self._rich.start()
            else:
                self._simple = SimpleSpinner()
                self._simple.thread.start()
        except Exception:
            pass

    def stop(self):
        try:
            if self._rich:
                self._rich.stop()
            if self._simple:
                self._simple.stop()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()

# ── UI helpers ────────────────────────────────────────────────────────────────
def print_tool_call(name: str, args: dict):
    icon, col = TOOL_ICONS.get(name, ("🔧", CYN))
    display = {}
    for k, v in args.items():
        display[k] = (v[:97] + "…") if isinstance(v, str) and len(v) > 100 else v
    arg_str = "  ".join(f"{c(k, DIM)}={c(repr(v), col)}" for k, v in display.items())
    print(f"  {icon} {c(name, B + col)}({arg_str})")

def print_tool_result(name: str, result: str, dt: float = 0.0):
    lines = (result or "").strip().splitlines()
    preview = lines[:5]
    tail = f"\n  {DIM}… {len(lines) - 5} more lines{R}" if len(lines) > 5 else ""
    body = f"\n  {DIM}│{R} ".join(preview)
    tstr = f" {DIM}({dt:.1f}s){R}" if dt else ""
    print(f"  {DIM}↳{R} {body}{tail}{tstr}")

def print_response(text: str):
    if RICH:
        console.print(Markdown(text))
    else:
        text = re.sub(r"```(\w*)\n(.*?)```",
                      lambda m: f"{CYN}{'─' * 50}{R}\n{m.group(2)}\n{CYN}{'─' * 50}{R}",
                      text, flags=re.DOTALL)
        text = re.sub(r"`([^`]+)`", lambda m: f"{YLW}{m.group(1)}{R}", text)
        print(text)

def print_separator():
    if RICH:
        console.rule(style="dim")
    else:
        print(f"{DIM}{'─' * 54}{R}")

def print_header():
    print()
    if RICH:
        console.print(Panel.fit(f"[bold cyan]KodeKey[/] [dim]v{VERSION} — AI Coding Agent[/]",
                                border_style="cyan"))
    else:
        print(f"{CYN}{'━' * 54}{R}")
        print(f"  {B}{CYN}KodeKey{R} {DIM}v{VERSION} — AI Coding Agent{R}")
        print(f"{CYN}{'━' * 54}{R}")
    print(f"  {DIM}Workspace :{R}  {c(os.getcwd(), CYN)}")
    print(f"  {DIM}Model     :{R}  {c(current_model, GRN)}  "
          f"{DIM}({'streaming' if CONFIG['stream'] else 'buffered'}){R}")
    print(f"  {DIM}Memory    :{R}  {c(str(project_memory_path()), CYN)}  +  {c(str(GLOBAL_MEMORY), CYN)}")
    saved = list_sessions()
    if saved:
        latest = saved[-1]
        print(f"  {DIM}Sessions  :{R}  {c(str(len(saved)), YLW)} saved — latest: "
              f"{c(latest['name'], YLW)} ({str(latest['timestamp'])[:16]})")
        print(f"             {DIM}run /load {latest['name']} or start with -c to continue{R}")
    if CUSTOM_COMMANDS:
        print(f"  {DIM}Custom    :{R}  {c(', '.join('/' + k for k in sorted(CUSTOM_COMMANDS)), MGT)}")
    print(f"\n  {DIM}Type {c('/help', YLW)} for commands · {c('@file', YLW)} attaches files · "
          f"{c('Tab', YLW)} completes{R}\n")

def print_help():
    custom_lines = ""
    for nm, tpl in sorted(CUSTOM_COMMANDS.items()):
        first = tpl.strip().splitlines()[0][:60] if tpl.strip() else ""
        custom_lines += f"    /{nm:<18} {first}\n"
    print(f"""
  {B}Session{R}
    /save [name]        Save conversation
    /load <name>        Load a session          /sessions     List sessions
    /delete <name>      Delete a session
    /new                Fresh session (context + changes + stats)
    /clear              Clear conversation only
    /retry              Re-run the last prompt
    /compact [note]     Summarize conversation to free context
    /export [file]      Export conversation to Markdown

  {B}Memory{R}
    /memory [scope]     Show memory (global | project | both)
    /memory edit [g|p]  Edit memory in $EDITOR
    /init               Scan codebase & write project memory

  {B}Model & config{R}
    /models             List models
    /model <n|name>     Switch model (fuzzy match OK)
    /refresh            Re-fetch the model list
    /config [set k v]   Show / change settings

  {B}Changes & info{R}
    /undo               Revert the agent's last file change
    /diff               Show all session file changes (unified diff)
    /todos              Show the agent's todo list
    /stats              Tokens, cost estimate, tool usage
    /plan [on|off]      Toggle plan mode (read-only agent)

  {B}Other{R}
    /help  /?           This help
    exit / quit / q     Quit (offers to save)

  {DIM}Tips: @file.py or @src/ attaches files · ↑/↓ history · Tab completes commands,
  session names and @paths · kkey "prompt" runs one-shot · kkey -c continues latest{R}"""
          + (f"\n\n  {B}Custom commands{R} (.kkey/commands/*.md)\n{custom_lines}" if custom_lines else ""))

# ── Model discovery ───────────────────────────────────────────────────────────
def fetch_live_models():
    global AVAILABLE_MODELS, current_model
    try:
        ids = sorted(m.id for m in client.models.list().data)
        if not ids:
            raise RuntimeError("empty model list")
    except Exception:
        ids = ["claude-sonnet-4-6", "deepseek/deepseek-v3.2"]
    AVAILABLE_MODELS = {str(i + 1): mid for i, mid in enumerate(ids)}
    preferred = str(CONFIG.get("default_model") or "")
    sonnet = [m for m in ids if "claude-sonnet" in m]
    if preferred in ids:
        current_model = preferred
    elif sonnet:
        current_model = sonnet[0]
    else:
        current_model = ids[0]

def print_model_menu():
    print(f"\n{B}Available models:{R}")
    print_separator()
    items = list(AVAILABLE_MODELS.items())
    for i in range(0, len(items), 2):
        k1, v1 = items[i]
        left = f"  {c(f'[{k1}]', CYN)} {v1}"
        right = f"  {c(f'[{items[i+1][0]}]', CYN)} {items[i+1][1]}" if i + 1 < len(items) else ""
        print(f"{left:<42}{right}")
    print_separator()
    print(f"  Active: {c(current_model, GRN)}\n")

def resolve_model(sel: str, fatal=False) -> bool:
    global current_model
    if sel in AVAILABLE_MODELS:
        current_model = AVAILABLE_MODELS[sel]
        return True
    if sel in AVAILABLE_MODELS.values():
        current_model = sel
        return True
    matches = [m for m in AVAILABLE_MODELS.values() if sel.lower() in m.lower()]
    if len(matches) == 1:
        current_model = matches[0]
        return True
    if matches:
        print(f"  {YLW}Ambiguous '{sel}' — matches: {', '.join(matches)}{R}")
    else:
        print(f"  {RED}Unknown model '{sel}' — use /models to list options{R}")
    if fatal:
        sys.exit(2)
    return False

# ── System prompt ─────────────────────────────────────────────────────────────
BASE_SYSTEM = """\
You are KodeKey, an elite software engineer embedded in the user's terminal — like Claude Code.

CORE RULES:
- Do EXACTLY what the user asks. Complete implementations — no placeholders, no "..." elisions, no TODOs unless requested.
- Read before editing: use read_file, then edit_file for targeted changes. write_file only for new files or full rewrites.
- Explore unfamiliar code first: glob, search_files, list_directory.
- Use bash for builds, tests, git, package installs, linting. Verify your changes when possible.
- Chain steps autonomously — don't stop to ask unless genuinely ambiguous.
- Keep explanations brief. Show code, not prose.
- For multi-step tasks, maintain the todo list with update_todos.

ACCESS:
- The user has granted FULL access to this machine: all files (including dotfiles and .env) and all commands are permitted.
- With that trust: prefer reversible actions, be surgical with deletions, and avoid destructive commands (rm -rf outside the project, force-push, DROP TABLE) unless the user explicitly asked for them.
- Never exfiltrate secrets: don't paste credentials into files, URLs, or commands where they don't belong.

TOOL NOTES:
- read_file supports offset/limit — page through large files instead of dumping them.
- bash accepts a timeout (seconds, up to 1800) for long builds/tests.
- fetch_url retrieves documentation or web pages when needed.
- The user can attach files by @-mentioning them — look for <file> blocks in the message.

MEMORY RULES:
- You have persistent memory via update_memory / read_memory (survives across sessions).
- Proactively save project conventions, tech stack, build commands, and user preferences when you learn them.
- scope='project' for this codebase; scope='global' for user-level preferences.

EDITING PHILOSOPHY:
- Smallest change that achieves the goal.
- Preserve existing style, indentation, and conventions.
- Add imports with existing imports."""

def build_system_prompt() -> str:
    ctx = (f"\n\n---\n# Environment\n"
           f"- Workspace: {os.getcwd()}\n"
           f"- Date: {datetime.date.today().isoformat()}\n"
           f"- OS: {platform.system()} {platform.release()}")
    plan = ""
    if PLAN_MODE:
        plan = ("\n\n---\n# PLAN MODE (ACTIVE)\n"
                "The user wants a plan before execution. Do NOT create, edit, or delete files. "
                "You may read files, search, and list directories. "
                "Present a clear plan: goals, files to change (with paths), and ordered steps. "
                "Then stop and wait for approval.")
    return BASE_SYSTEM + ctx + plan + build_memory_block()

# ── Usage / cost ──────────────────────────────────────────────────────────────
PRICING = [  # (prefix, $ per 1M input, $ per 1M output) — rough estimates
    ("claude-opus", 15.0, 75.0),
    ("claude-sonnet", 3.0, 15.0),
    ("claude-3-5-haiku", 0.8, 4.0),
    ("claude", 3.0, 15.0),
    ("deepseek-reasoner", 0.55, 2.19),
    ("deepseek", 0.27, 1.10),
    ("gpt-4o", 2.50, 10.0),
    ("gpt-4.1", 2.50, 10.0),
    ("gpt", 1.25, 10.0),
]

def _add_usage(u):
    if not u:
        return
    try:
        USAGE["prompt"] += getattr(u, "prompt_tokens", 0) or 0
        USAGE["completion"] += getattr(u, "completion_tokens", 0) or 0
    except Exception:
        pass

def estimate_cost(model: str, pt: int, ct: int):
    for pref, pin, pout in PRICING:
        if model.startswith(pref) or pref in model:
            return pt / 1e6 * pin + ct / 1e6 * pout
    return None

def fmt_duration(s: float) -> str:
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else (f"{m}m {sec}s" if m else f"{sec}s")

# ════════════════════════════════════════════════════════════════════════════════
# API LAYER (streaming, retries, tool-call assembly)
# ════════════════════════════════════════════════════════════════════════════════

def stream_message(messages):
    """Stream a completion; prints content/reasoning live. Returns an assistant dict."""
    last_err = None
    for include_usage in (True, False):
        content, reasoning, tool_acc = "", "", {}
        received = False
        spin = Spinner()
        try:
            kwargs = {"model": current_model, "messages": messages, "stream": True}
            if TOOLS_SCHEMA:
                kwargs["tools"] = TOOLS_SCHEMA
                kwargs["tool_choice"] = "auto"
            if include_usage:
                kwargs["stream_options"] = {"include_usage": True}
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                u = getattr(chunk, "usage", None)
                if u:
                    _add_usage(u)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if rc:
                    if not received:
                        received = True
                        spin.stop()
                    reasoning += rc
                    if CONFIG["show_reasoning"]:
                        sys.stdout.write(f"{DIM}{rc}{R}")
                        sys.stdout.flush()
                if delta.content:
                    if not received:
                        received = True
                        spin.stop()
                    elif reasoning and not content and CONFIG["show_reasoning"]:
                        sys.stdout.write("\n")
                    content += delta.content
                    sys.stdout.write(delta.content)
                    sys.stdout.flush()
                if delta.tool_calls:
                    if not received:
                        received = True
                        spin.stop()
                    for tcd in delta.tool_calls:
                        idx = tcd.index if tcd.index is not None else 0
                        slot = tool_acc.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if tcd.id:
                            slot["id"] = tcd.id
                        if tcd.function:
                            if tcd.function.name:
                                slot["name"] += tcd.function.name
                            if tcd.function.arguments:
                                slot["arguments"] += tcd.function.arguments
            spin.stop()
            if content and not content.endswith("\n"):
                print()
            msg = {"role": "assistant", "content": content}
            if tool_acc:
                msg["tool_calls"] = []
                for i in sorted(tool_acc):
                    slot = tool_acc[i]
                    cid = slot["id"] or f"call_auto_{i}_{int(time.time() * 1000)}"
                    msg["tool_calls"].append({
                        "id": cid, "type": "function",
                        "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
                    })
            return msg
        except KeyboardInterrupt:
            spin.stop()
            if content and not content.endswith("\n"):
                print()
            print(f"{DIM}(interrupted){R}")
            return {"role": "assistant", "content": content}
        except Exception as e:
            spin.stop()
            last_err = e
            if received:
                if content and not content.endswith("\n"):
                    print()
                print(f"\n{YLW}⚠ Stream interrupted after partial output: {e}{R}")
                return {"role": "assistant", "content": content}
            if include_usage:
                continue  # maybe stream_options unsupported → retry without it
            raise
    raise RuntimeError(f"Streaming failed: {last_err}")

def create_message(messages):
    last = None
    for attempt in range(3):
        try:
            if CONFIG["stream"]:
                return stream_message(messages)
            with Spinner():
                resp = client.chat.completions.create(
                    model=current_model, messages=messages,
                    tools=TOOLS_SCHEMA, tool_choice="auto")
            m = resp.choices[0].message
            _add_usage(getattr(resp, "usage", None))
            rc = getattr(m, "reasoning_content", None) or getattr(m, "reasoning", None)
            if rc and CONFIG["show_reasoning"]:
                print(f"{DIM}{rc}{R}\n")
            msg = {"role": "assistant", "content": m.content or ""}
            if getattr(m, "tool_calls", None):
                msg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in m.tool_calls]
            return msg
        except KeyboardInterrupt:
            raise
        except Exception as e:
            last = e
            if attempt < 2:
                wait = 2 ** attempt
                print(f"  {YLW}⚠ API error: {e} — retrying in {wait}s…{R}")
                time.sleep(wait)
    raise RuntimeError(f"API request failed after 3 attempts: {last}")

def tool_msg(call_id, name, content):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}

def execute_tool(name, args):
    if PLAN_MODE and name in PLAN_MUTATING:
        return ("Plan mode is active — file modifications are disabled. "
                "Read/search tools remain available. Present your plan instead."), 0.0
    handler = TOOL_DISPATCH.get(name)
    if handler is None:
        return f"Unknown tool '{name}'.", 0.0
    t0 = time.time()
    try:
        result = handler(args)
    except Exception as e:
        result = f"Tool error: {e}"
    return result, time.time() - t0

def agent_turn(sess):
    """Run the model↔tool loop until the model stops calling tools."""
    while True:
        msg = create_message(sess.messages)
        sess.messages.append(msg)
        if not CONFIG["stream"] and (msg.get("content") or "").strip():
            print_response(msg["content"])
        if not msg.get("tool_calls"):
            return
        print()
        interrupted = False
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError as e:
                print_tool_call(name, {"?": "?"})
                result = f"Error: could not parse tool arguments: {e}"
                print_tool_result(name, result, 0.0)
                sess.messages.append(tool_msg(tc["id"], name, result))
                continue
            if interrupted:
                sess.messages.append(tool_msg(tc["id"], name, "Interrupted by user; not executed."))
                continue
            print_tool_call(name, args)
            try:
                result, dt = execute_tool(name, args)
            except KeyboardInterrupt:
                interrupted = True
                dt = 0.0
                result = "Interrupted by user; not executed."
                print(f"\n  {YLW}⏹ Interrupted — skipping remaining tool calls.{R}")
            print_tool_result(name, result, dt)
            sess.messages.append(tool_msg(tc["id"], name, result))
            if name == "update_memory":
                sess.messages[0] = {"role": "system", "content": build_system_prompt()}
        print()
        if interrupted:
            return

def _repair_dangling_tool_calls(sess):
    """If the last message is an assistant msg with tool_calls but no results yet,
    append synthetic results so the history stays valid for the API."""
    msgs = sess.messages
    if msgs and msgs[-1].get("role") == "assistant" and msgs[-1].get("tool_calls"):
        for tc in msgs[-1]["tool_calls"]:
            msgs.append(tool_msg(tc["id"], tc["function"]["name"], "Interrupted by user."))

# ── Context compaction ────────────────────────────────────────────────────────
def compact(sess, instructions=""):
    convo = []
    for m in sess.messages[1:]:
        role = m.get("role")
        if role == "user":
            convo.append(f"USER: {m.get('content', '')}")
        elif role == "assistant":
            c = m.get("content") or ""
            tcs = m.get("tool_calls") or []
            if tcs:
                names = ",".join(tc["function"]["name"] for tc in tcs)
                c = (c + f" [called tools: {names}]").strip()
            if c:
                convo.append(f"ASSISTANT: {c}")
        elif role == "tool":
            content = m.get("content", "") or ""
            if len(content) > 400:
                content = content[:400] + "…"
            convo.append(f"TOOL RESULT ({m.get('name')}): {content}")
    transcript = "\n\n".join(convo)
    if len(transcript) > 200000:
        transcript = transcript[:200000] + "\n…(truncated)"
    prompt = ("Summarize this coding conversation for context continuity. Preserve: the user's "
              "goal, decisions made, files created/edited (with paths), current state of work, "
              "and unresolved issues or next steps. Be concise but complete — this summary "
              "replaces the full transcript.")
    if instructions:
        prompt += f"\nAdditional focus: {instructions}"
    try:
        with Spinner():
            resp = client.chat.completions.create(
                model=current_model,
                messages=[{"role": "system", "content": "You write precise technical summaries."},
                          {"role": "user", "content": prompt + "\n\n<transcript>\n" + transcript + "\n</transcript>"}])
        summary = resp.choices[0].message.content or ""
        _add_usage(getattr(resp, "usage", None))
    except Exception as e:
        print(f"  {RED}✖ Compaction failed: {e}{R}")
        return
    sess.messages = [
        sess.messages[0],
        {"role": "user", "content":
         f"<conversation_summary>\n{summary}\n</conversation_summary>\n\n"
         "(The above summarizes our conversation so far and replaces earlier messages.)"},
        {"role": "assistant", "content": "Understood — continuing from the summary."},
    ]
    print(f"  {GRN}✔ Compacted ({len(summary)} char summary, context freed).{R}")

def maybe_autocompact(sess):
    threshold = _int(CONFIG["context_chars"], 600000)
    if not threshold:
        return
    size = sum(len(str(m.get("content") or "")) for m in sess.messages)
    size += sum(len(tc["function"].get("arguments") or "")
                for m in sess.messages if isinstance(m, dict)
                for tc in (m.get("tool_calls") or []))
    if size > threshold:
        print(f"\n{YLW}⚠ Context is large ({size:,} chars) — auto-compacting…{R}")
        compact(sess)

# ── @-mentions ────────────────────────────────────────────────────────────────
MENTION_RE = re.compile(r"(?<![\w])@([A-Za-z0-9_./+-]+)")

def expand_mentions(prompt: str) -> str:
    attachments = []
    for m in MENTION_RE.finditer(prompt):
        p = m.group(1)
        if os.path.exists(p) and p not in attachments:
            attachments.append(p)
    if not attachments:
        return prompt
    blocks = []
    for p in attachments:
        try:
            if os.path.isdir(p):
                blocks.append(f'<directory path="{p}">\n{tool_list_directory(p, max_depth=2)}\n</directory>')
                print(f"  {DIM}📎 @{p} (directory tree attached){R}")
            else:
                text = Path(p).read_text(encoding="utf-8", errors="replace")
                n = len(text.splitlines())
                if len(text) > 60000:
                    text = text[:60000] + "\n… (truncated)"
                blocks.append(f'<file path="{p}">\n{text}\n</file>')
                print(f"  {DIM}📎 @{p} ({n} lines attached){R}")
        except Exception as e:
            print(f"  {YLW}⚠ Could not attach @{p}: {e}{R}")
    if not blocks:
        return prompt
    return prompt + "\n\n" + "\n\n".join(blocks)

# ── Custom slash commands ─────────────────────────────────────────────────────
CUSTOM_COMMANDS = {}

def load_custom_commands():
    cmds = {}
    for base in (KKEY_GLOBAL_DIR / "commands", Path(os.getcwd()) / ".kkey" / "commands"):
        try:
            if base.is_dir():
                for f in sorted(base.glob("*.md")):
                    try:
                        cmds[f.stem] = f.read_text(encoding="utf-8")
                    except Exception:
                        pass
        except Exception:
            pass
    return cmds

def expand_custom_command(template, arg_str, args):
    out = template.replace("$ARGUMENTS", arg_str)
    for i in range(1, 10):
        out = out.replace(f"${i}", args[i - 1] if i - 1 < len(args) else "")
    return out

def all_command_names():
    return sorted(set(list(COMMANDS) + list(CUSTOM_COMMANDS) + ["exit", "quit", "q"]))

# ── Session object ────────────────────────────────────────────────────────────
class Session:
    def __init__(self):
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        self.changes = ChangeLog()
        self.name = None
        self.started = time.time()

def _restore_session(sess, payload):
    global current_model, TODOS
    model = payload.get("model")
    if model:
        current_model = model
    sess.messages = [{"role": "system", "content": build_system_prompt()}] + payload.get("messages", [])
    sess.changes = ChangeLog(payload.get("changes") or [])
    sess.name = payload.get("name")
    u = payload.get("usage") or {}
    USAGE["prompt"] = u.get("prompt", 0)
    USAGE["completion"] = u.get("completion", 0)
    TODOS = payload.get("todos") or []

# ── Git helpers ───────────────────────────────────────────────────────────────
def git_branch():
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""

# ════════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ════════════════════════════════════════════════════════════════════════════════

def cmd_help(sess, args):
    print_help()

def cmd_save(sess, args):
    name = args[0] if args else (sess.name or auto_session_name())
    name = re.sub(r"[^\w.-]", "_", name)
    sess.name = name
    try:
        path = save_session(sess, name)
        print(f"  {GRN}✔ Session '{name}' saved → {path}{R}")
    except Exception as e:
        print(f"  {RED}✖ {e}{R}")

def cmd_load(sess, args):
    if not args:
        print(f"  {RED}Usage: /load <session_name>{R}")
        return
    try:
        payload = load_session(args[0])
    except Exception as e:
        print(f"  {RED}✖ {e}{R}")
        return
    _restore_session(sess, payload)
    turns = sum(1 for m in sess.messages if m.get("role") == "user")
    print(f"  {GRN}✔ Loaded '{args[0]}' ({turns} user turns) — model: {current_model}{R}")

def cmd_sessions(sess, args):
    saved = list_sessions()
    if not saved:
        print(f"  {DIM}No saved sessions for this project.{R}")
        return
    print(f"\n  {B}Saved sessions:{R}  ({sessions_dir()})")
    print_separator()
    for s in saved:
        ts = str(s['timestamp'])[:16] if s['timestamp'] != "?" else "?"
        print(f"  {c(s['name'], CYN):<35} {DIM}{ts}  {s['turns']} turns  {s['model']}{R}")
    print_separator()
    print()

def cmd_delete(sess, args):
    if not args:
        print(f"  {RED}Usage: /delete <session_name>{R}")
        return
    print(f"  {delete_session(args[0])}")

def cmd_new(sess, args):
    global TODOS
    sess.messages = [{"role": "system", "content": build_system_prompt()}]
    sess.changes = ChangeLog()
    sess.name = None
    sess.started = time.time()
    USAGE["prompt"] = 0
    USAGE["completion"] = 0
    TODOS = []
    print(f"  {GRN}✔ New session (context, changes and stats reset — memory preserved).{R}")

def cmd_clear(sess, args):
    sess.messages = [{"role": "system", "content": build_system_prompt()}]
    print(f"  {GRN}✔ Context cleared (memory and change history preserved).{R}")

def cmd_retry(sess, args):
    for i in range(len(sess.messages) - 1, -1, -1):
        if sess.messages[i].get("role") == "user":
            content = sess.messages[i]["content"]
            del sess.messages[i:]
            print(f"  {GRN}↻ Re-running last prompt…{R}\n")
            try:
                agent_turn(sess)
            except KeyboardInterrupt:
                _repair_dangling_tool_calls(sess)
                print(f"\n{DIM}(interrupted){R}")
            return
    print(f"  {RED}Nothing to retry.{R}")

def cmd_compact(sess, args):
    compact(sess, " ".join(args))

def cmd_export(sess, args):
    path = Path(args[0]) if args else project_kkey_dir() / f"export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = ["# KodeKey Session Export",
             f"*model: {current_model} · {datetime.datetime.now().isoformat(timespec='seconds')}*", ""]
    for m in sess.messages[1:]:
        role = m.get("role")
        if role == "user":
            lines += ["## 🧑 User", "", m.get("content", ""), ""]
        elif role == "assistant":
            lines += ["## 🤖 Assistant", "", m.get("content") or "", ""]
            for tc in m.get("tool_calls") or []:
                lines += [f"*🔧 `{tc['function']['name']}`({(tc['function'].get('arguments') or '')[:200]}…)*", ""]
        elif role == "tool":
            lines += [f"<details><summary>🔧 {m.get('name')} result</summary>", "", "```",
                      (m.get("content") or "")[:1500], "```", "</details>", ""]
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  {GRN}✔ Exported → {path}{R}")
    except Exception as e:
        print(f"  {RED}✖ {e}{R}")

def cmd_memory(sess, args):
    if args and args[0].lower() == "edit":
        which = args[1].lower() if len(args) > 1 else "project"
        path = GLOBAL_MEMORY if which.startswith("g") else project_memory_path()
        editor = os.environ.get("EDITOR", "nano")
        print(f"  {DIM}Opening {path} in {editor}…{R}")
        subprocess.call([editor, str(path)])
        sess.messages[0] = {"role": "system", "content": build_system_prompt()}
        print(f"  {GRN}✔ Memory reloaded into context.{R}")
        return
    scope = args[0].lower() if args else "both"
    scope = scope if scope in ("global", "project") else "both"
    content = tool_read_memory(scope)
    if RICH:
        console.print(Markdown(content))
    else:
        print(f"\n{content}\n")

INIT_PROMPT = """Analyze this codebase and update the project memory file for future sessions.

1. Explore the project: list_directory, read key files (README, package.json / pyproject.toml / requirements.txt / Cargo.toml / go.mod, config files, main entry points).
2. Then call update_memory with scope='project', writing a memory file containing:
   - **Overview**: what this project is / does
   - **Tech stack**: languages, frameworks, key dependencies
   - **Structure**: important directories/files and their roles
   - **Commands**: how to install, build, test, lint, run
   - **Conventions**: code style and patterns you observed
Keep it under 80 lines. Only include what you verified by reading files."""

def cmd_init(sess, args):
    print(f"  {DIM}Analyzing project…{R}\n")
    sess.messages.append({"role": "user", "content": INIT_PROMPT})
    try:
        agent_turn(sess)
    except KeyboardInterrupt:
        _repair_dangling_tool_calls(sess)
        print(f"\n{DIM}(interrupted){R}")

def cmd_models(sess, args):
    print_model_menu()

def cmd_model(sess, args):
    if not args:
        print_model_menu()
        return
    if resolve_model(args[0]):
        print(f"  {GRN}✔ Model → {current_model}{R}")

def cmd_refresh(sess, args):
    fetch_live_models()
    print(f"  {GRN}✔ Model list refreshed ({len(AVAILABLE_MODELS)} models).{R}")
    print_model_menu()

def cmd_undo(sess, args):
    if not sess.changes.entries:
        print("  Nothing to undo.")
        return
    msg = sess.changes.undo()
    print(f"  {GRN}{msg}{R}")
    sess.messages.append({"role": "user",
                          "content": f"[system note] The user ran /undo: {msg} — adjust accordingly."})

def cmd_diff(sess, args):
    diffs = list(sess.changes.iter_diffs())
    if not diffs:
        print("  No tracked file changes this session.")
        return
    for path, d in diffs:
        print(f"\n  {B}{path}{R}")
        for line in d.splitlines()[:200]:
            if line.startswith("+") and not line.startswith("+++"):
                print(f"  {GRN}{line}{R}")
            elif line.startswith("-") and not line.startswith("---"):
                print(f"  {RED}{line}{R}")
            elif line.startswith("@@"):
                print(f"  {CYN}{line}{R}")
            else:
                print(f"  {DIM}{line}{R}")
    print()

def cmd_todos(sess, args):
    if not TODOS:
        print("  No todos — the agent tracks multi-step work here.")
        return
    render_todos()

def cmd_stats(sess, args):
    dur = fmt_duration(time.time() - sess.started)
    user_turns = sum(1 for m in sess.messages if m.get("role") == "user")
    tool_counts = {}
    for m in sess.messages:
        for tc in m.get("tool_calls") or []:
            n = tc["function"]["name"]
            tool_counts[n] = tool_counts.get(n, 0) + 1
    pt, ct = USAGE["prompt"], USAGE["completion"]
    cost = estimate_cost(current_model, pt, ct)
    print(f"\n  {B}Session stats{R}")
    print_separator()
    print(f"  Duration      {dur}")
    print(f"  User turns    {user_turns}")
    if pt or ct:
        print(f"  Tokens        {pt:,} in · {ct:,} out")
        print(f"  Est. cost     " + (f"~${cost:.4f}" if cost is not None else "n/a (no pricing data)"))
    else:
        print(f"  Tokens        (provider does not report usage)")
    if tool_counts:
        top = sorted(tool_counts.items(), key=lambda kv: -kv[1])[:8]
        print(f"  Tool calls    " + ", ".join(f"{k} ×{v}" for k, v in top))
    print(f"  File changes  {len(sess.changes.entries)} tracked — /undo, /diff available")
    print_separator()
    print()

def _coerce(v):
    if v.lower() in ("on", "true", "yes"):
        return True
    if v.lower() in ("off", "false", "no"):
        return False
    try:
        return json.loads(v)
    except Exception:
        return v

def cmd_config(sess, args):
    if not args:
        print(f"\n  {B}Configuration{R}  ({CONFIG_PATH})")
        print_separator()
        for k, v in CONFIG.items():
            print(f"  {c(k, CYN):<16} {v}")
        print_separator()
        print(f"  {DIM}Change with: /config set <key> <value>{R}\n")
        return
    if args[0].lower() == "set" and len(args) >= 3:
        key, val = args[1], args[2]
        if key not in DEFAULT_CONFIG:
            print(f"  {RED}Unknown key '{key}'. Valid: {', '.join(DEFAULT_CONFIG)}{R}")
            return
        CONFIG[key] = _coerce(val)
        save_config()
        print(f"  {GRN}✔ {key} = {CONFIG[key]!r}{R}")
    else:
        print(f"  {RED}Usage: /config  or  /config set <key> <value>{R}")

def cmd_plan(sess, args):
    global PLAN_MODE
    arg = args[0].lower() if args else ("off" if PLAN_MODE else "on")
    PLAN_MODE = arg in ("on", "true", "1", "yes")
    sess.messages[0] = {"role": "system", "content": build_system_prompt()}
    state = c("ON — agent is read-only until /plan off", YLW) if PLAN_MODE else c("OFF", GRN)
    print(f"  {GRN}✔ Plan mode: {state}{R}")

COMMANDS = {
    "help": cmd_help, "?": cmd_help,
    "save": cmd_save, "load": cmd_load, "sessions": cmd_sessions, "delete": cmd_delete,
    "new": cmd_new, "clear": cmd_clear, "retry": cmd_retry, "compact": cmd_compact,
    "export": cmd_export, "memory": cmd_memory, "init": cmd_init,
    "models": cmd_models, "model": cmd_model, "refresh": cmd_refresh,
    "undo": cmd_undo, "diff": cmd_diff, "todos": cmd_todos, "stats": cmd_stats,
    "config": cmd_config, "plan": cmd_plan,
}

# ── Readline (history + completion) ──────────────────────────────────────────
def completer(text, state):
    line = readline.get_line_buffer()
    # complete session names for /load and /delete
    m = re.match(r"(\S+)\s+(\S*)$", line)
    if m and m.group(1) in ("/load", "/delete") and m.group(2) == text:
        names = [s["name"] for s in list_sessions()]
        opts = [n + " " for n in names if n.startswith(text)]
    elif line.lstrip().startswith("/") and " " not in line.lstrip():
        opts = ["/" + c_ + " " for c_ in all_command_names() if ("/" + c_).startswith(text)]
    elif text.startswith("@"):
        prefix = text[1:]
        d, base = os.path.split(prefix)
        base_dir = d if d else "."
        try:
            names = os.listdir(base_dir)
        except Exception:
            names = []
        opts = []
        for n in names:
            if n.startswith(base):
                full = os.path.join(d, n) if d else n
                sep = "/" if os.path.isdir(os.path.join(base_dir, n)) else " "
                opts.append("@" + full + sep)
    else:
        opts = []
    return opts[state] if state < len(opts) else None

def setup_readline():
    if not readline:
        return
    try:
        readline.read_history_file(HISTORY_FILE)
    except Exception:
        pass
    try:
        readline.set_history_length(2000)
        readline.set_completer(completer)
        if "libedit" in (getattr(readline, "__doc__", "") or ""):
            readline.parse_and_bind("bind ^I rl_complete")   # macOS
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:
        pass
    atexit.register(_save_history)

def _save_history():
    if readline:
        try:
            readline.write_history_file(HISTORY_FILE)
        except Exception:
            pass

# ── Misc ──────────────────────────────────────────────────────────────────────
def offer_save(sess):
    if not any(m.get("role") == "user" for m in sess.messages):
        return
    try:
        ans = input(f"  {YLW}Save session before exit? [y/N]:{R} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if ans in ("y", "yes"):
        try:
            name = input(f"  Session name [{auto_session_name()}]: ").strip() or auto_session_name()
            name = re.sub(r"[^\w.-]", "_", name)
            sess.name = name
            path = save_session(sess, name)
            print(f"  {GRN}✔ Saved → {path}{R}")
        except Exception as e:
            print(f"  {RED}✖ {e}{R}")

def go_agent(sess, text):
    maybe_autocompact(sess)
    expanded = expand_mentions(text)
    sess.messages.append({"role": "user", "content": expanded})
    print()
    try:
        agent_turn(sess)
    except KeyboardInterrupt:
        _repair_dangling_tool_calls(sess)
        print(f"\n{DIM}(turn interrupted){R}")
    except Exception as e:
        _repair_dangling_tool_calls(sess)
        import traceback
        print(f"\n{RED}✖ Error: {e}{R}")
        if os.environ.get("KKEY_DEBUG"):
            traceback.print_exc()
    print()

# ════════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════════

def main():
    global client, current_model, CUSTOM_COMMANDS

    parser = argparse.ArgumentParser(prog="kkey", description="KodeKey — terminal AI coding agent")
    parser.add_argument("prompt", nargs="*", help="one-shot prompt (omit for interactive mode)")
    parser.add_argument("-c", "--continue", dest="continue_last", action="store_true",
                        help="continue the most recent session")
    parser.add_argument("-s", "--session", help="load a named session")
    parser.add_argument("-m", "--model", help="model to use (index, name, or fuzzy)")
    parser.add_argument("--no-stream", action="store_true", help="disable streaming output")
    parser.add_argument("-V", "--version", action="version", version=f"KodeKey {VERSION}")
    args = parser.parse_args()

    if args.no_stream:
        CONFIG["stream"] = False

    if not api_key:
        print(f"{RED}✖  No API key found.{R}")
        print("   Set one with:  export KODEKEY_API_KEY='your_key_here'")
        print(f"   Or add {{\"api_key\": \"...\"}} to {CONFIG_PATH}")
        sys.exit(1)

    KKEY_GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)

    fetch_live_models()
    if str(CONFIG.get("default_model") or "") in AVAILABLE_MODELS.values():
        current_model = str(CONFIG["default_model"])
    if args.model:
        resolve_model(args.model, fatal=True)

    ensure_memory_files()
    CUSTOM_COMMANDS = load_custom_commands()
    setup_readline()

    sess = Session()

    # ── load a session (for -c / --session) ────────────────────────────────
    if args.session or args.continue_last:
        name = args.session or latest_session_name()
        if not name:
            print(f"{RED}✖ No saved sessions found in {sessions_dir()}{R}")
            sys.exit(1)
        try:
            payload = load_session(name)
            _restore_session(sess, payload)
        except Exception as e:
            print(f"{RED}✖ {e}{R}")
            sys.exit(1)

    # ── one-shot mode ─────────────────────────────────────────────────────
    if args.prompt:
        prompt_text = " ".join(args.prompt)
        if not sys.stdin.isatty():
            try:
                piped = sys.stdin.read()
            except Exception:
                piped = ""
            if piped.strip():
                prompt_text += "\n\n<input from stdin>\n" + piped[:100_000] + "\n</input>"
        go_agent(sess, prompt_text)
        if args.continue_last or args.session:
            try:
                name = re.sub(r"[^\w.-]", "_", sess.name or auto_session_name())
                p = save_session(sess, name)
                print(f"  {DIM}Session saved → {p}{R}")
            except Exception:
                pass
        return

    # ── interactive mode ──────────────────────────────────────────────────
    print_header()

    while True:
        try:
            branch = git_branch()
            ps1 = (rl("kkey", B + CYN)
                   + (f" {rl(branch, MGT)}" if branch else "")
                   + f" {rl('❯', YLW)} ")
            prompt = input(ps1).strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print(f"\n{DIM}(interrupted — type 'exit' or press Ctrl-D to quit){R}")
            continue

        if not prompt:
            continue

        low = prompt.lower()
        if low in ("exit", "quit", "q"):
            break

        if prompt.startswith("/"):
            parts = prompt.split()
            name = parts[0][1:].lower()
            rest = parts[1:]
            if name in ("exit", "quit", "q"):
                break
            if name in COMMANDS:
                try:
                    COMMANDS[name](sess, rest)
                except Exception as e:
                    print(f"  {RED}✖ {e}{R}")
                continue
            if name in CUSTOM_COMMANDS:
                expanded = expand_custom_command(CUSTOM_COMMANDS[name], " ".join(rest), rest)
                print(f"  {DIM}▶ custom command /{name}{R}\n")
                go_agent(sess, expanded)
                continue
            print(f"  {RED}Unknown command '/{name}' — /help for the list{R}")
            continue

        go_agent(sess, prompt)

    offer_save(sess)
    print(f"{DIM}Bye.{R}")


if __name__ == "__main__":
    main()