````markdown
# KodeKey

```
 ██╗  ██╗███████╗ ██████╗ ██████╗  ██████╗██╗  ██╗    ███████╗██╗   ██╗███╗   ██╗
 ██║ ██╔╝██╔════╝██╔═══██╗██╔══██╗██╔════╝██║  ██║    ██╔════╝██║   ██║████╗  ██║
 █████╔╝ ███████╗██║   ██║██████╔╝██║     ███████║    █████╗  ██║   ██║██╔██╗ ██║
 ██╔═██╗ ╚════██║██║   ██║██╔══██╗██║     ██╔══██║    ██╔══╝  ██║   ██║██║╚██╗██║
 ██║  ██╗███████║╚██████╔╝██║  ██║╚██████╗██║  ██║    ███████╗╚██████╔╝██║ ╚████║
 ╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝    ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝
```

**KodeKey** is a Claude Code–style AI coding agent that lives in your terminal.
It reads, writes, edits, searches your code, runs shell commands, browses docs,
and remembers your project across sessions — with streaming responses, file
checkpoints (`/undo`), one-shot mode, `@file` mentions, and more.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation — Linux](#installation--linux-macos)
- [Installation — Windows (Native)](#installation--windows-native)
- [Installation — Windows (WSL)](#installation--windows-wsl)
- [Set Your API Key](#set-your-api-key)
- [Verify Installation](#verify-installation)
- [Usage](#usage)
- [Slash Commands Reference](#slash-commands-reference)
- [Configuration](#configuration)
- [Data Layout](#data-layout)
- [Troubleshooting](#troubleshooting)
- [Updating & Uninstalling](#updating--uninstalling)

---

## Features

- 💬 **Interactive REPL** with streaming output and reasoning-token display
- 🔧 **Agent tools** — read/write/edit/delete files, grep, glob, tree, bash, web fetch
- 📎 **`@file` mentions** — attach files or whole directories to any prompt
- ⏪ **Checkpoints** — every agent file change is snapshotted; `/undo` reverts, `/diff` shows diffs
- 🧠 **Persistent memory** — global (`~/.kkey/memory.md`) + per-project (`.kkey/memory.md`)
- 💾 **Sessions** — save/load/resume conversations (`kkey -c` continues the latest)
- 🤖 **One-shot mode** — `kkey "fix the failing tests"`, with stdin piping support
- 🗜️ **Context management** — `/compact` and automatic compaction on large contexts
- 📋 **Todo tracking** — the agent plans multi-step work and shows progress
- 🗺️ **Plan mode** — read-only exploration until you approve a plan
- ⚡ **Custom slash commands** — drop `.md` files in `.kkey/commands/`
- 📈 **Stats** — token usage, cost estimate, tool-call counts
- ⌨️ **Nice terminal UX** — history, Tab completion, git branch in prompt, colored output

---

## Requirements

| Requirement | Notes |
|---|---|
| **Python 3.9+** | 3.10 or newer recommended |
| **An API key** | Any OpenAI-compatible endpoint (OpenAI, DeepSeek, OpenRouter, local vLLM/LM Studio, …) |
| **Internet** | To reach your model provider |
| *Optional:* `rich` | Prettier markdown/panels — highly recommended |
| *Optional:* `ripgrep` | Much faster code search |
| *Optional:* `pyreadline3` | Windows only — enables ↑/↓ history and Tab completion |
| *Optional:* `git` | Shows branch name in the prompt |

---

## Installation — Linux / macOS

### 1. Install Python

**Ubuntu / Debian:**
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

**Fedora:**
```bash
sudo dnf install -y python3
```

**Arch:**
```bash
sudo pacman -S --needed python
```

**macOS:**
```bash
brew install python
```

### 2. Create the install directory and add the script

```bash
mkdir -p ~/kkey && cd ~/kkey
# Save kkey.py into ~/kkey/  (copy the file there, or download it)
```

### 3. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install openai rich
```

### 4. Create the launcher

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/kkey <<'EOF'
#!/usr/bin/env bash
exec "$HOME/kkey/.venv/bin/python" "$HOME/kkey/kkey.py" "$@"
EOF
chmod +x ~/.local/bin/kkey
```

### 5. Make sure `~/.local/bin` is on your PATH

Most distros already have it. If `kkey` isn't found after install, add:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

> **zsh users:** add the line to `~/.zshrc` instead.
> **fish users:** run `fish_add_path ~/.local/bin`

### 6. (Optional) Install ripgrep for faster search

```bash
sudo apt install ripgrep        # Debian/Ubuntu
sudo dnf install ripgrep        # Fedora
sudo pacman -S ripgrep          # Arch
brew install ripgrep            # macOS
```

---

## Installation — Windows (Native)

### 1. Install Python

1. Download **Python 3.10+** from <https://www.python.org/downloads/windows/>
2. Run the installer and **✔ tick "Add python.exe to PATH"** — this is important
3. Verify in **PowerShell**:

```powershell
python --version    # or: py --version
```

### 2. Create the install directory and add the script

```powershell
mkdir $HOME\kkey
cd $HOME\kkey
# Save kkey.py into C:\Users\<you>\kkey\
```

### 3. Create a virtual environment and install dependencies

```powershell
py -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install openai rich pyreadline3
```

> `pyreadline3` enables ↑/↓ history and Tab completion on Windows. Without it,
> KodeKey still works — you just lose those conveniences.

### 4. Create the launcher

Create a file named `kkey.cmd` inside a new `bin` folder:

```powershell
mkdir $HOME\bin
[IO.File]::WriteAllText("$HOME\bin\kkey.cmd", @"
@echo off
"%USERPROFILE%\kkey\.venv\Scripts\python.exe" "%USERPROFILE%\kkey\kkey.py" %*
"@)
```

Or create `C:\Users\<you>\bin\kkey.cmd` manually with Notepad containing:

```cmd
@echo off
"%USERPROFILE%\kkey\.venv\Scripts\python.exe" "%USERPROFILE%\kkey\kkey.py" %*
```

### 5. Add the bin folder to your PATH (one time)

```powershell
[Environment]::SetEnvironmentVariable(
    "Path",
    [Environment]::GetEnvironmentVariable("Path", "User") + ";$HOME\bin",
    "User")
```

**Close and reopen your terminal** for the change to take effect.

### 6. (Optional) Windows quality-of-life

| Tip | Why |
|---|---|
| Use **Windows Terminal** (free, Microsoft Store) | Proper colors, emoji, and Unicode |
| Run `setx PYTHONUTF8 1` then restart the terminal | Fixes `UnicodeEncodeError` on some systems |
| `winget install BurntSushi.ripgrep.MSVC` | Faster code search |

---

## Installation — Windows (WSL)

If you use WSL, you get the full Linux experience (best readline support,
native tooling). From PowerShell:

```powershell
wsl --install -d Ubuntu
```

Then open the Ubuntu terminal and follow the **[Linux instructions above](#installation--linux-macos)**.
Your files are reachable from Windows Explorer at `\\wsl$\Ubuntu\home\<you>\`.

---

## Set Your API Key

### Option A — Environment variable

**Linux / macOS** (add to `~/.bashrc` or `~/.zshrc`):
```bash
export KODEKEY_API_KEY="sk-your-key-here"
```

**Windows** (permanent, user-level — run in PowerShell, then **restart the terminal**):
```powershell
setx KODEKEY_API_KEY "sk-your-key-here"
```

### Option B — Config file (works everywhere)

**Linux / macOS:**
```bash
mkdir -p ~/.kkey
echo '{"api_key": "sk-your-key-here"}' > ~/.kkey/config.json
```

**Windows (PowerShell):**
```powershell
mkdir $HOME\.kkey -Force
[IO.File]::WriteAllText("$HOME\.kkey\config.json", '{"api_key": "sk-your-key-here"}')
```

### Custom endpoint (optional)

Point KodeKey at any OpenAI-compatible server (OpenRouter, DeepSeek, local vLLM…):

```bash
export KODEKEY_BASE_URL="https://openrouter.ai/api/v1"   # Linux
```
```powershell
setx KODEKEY_BASE_URL "https://openrouter.ai/api/v1"     # Windows
```
or add `"base_url": "..."` to `~/.kkey/config.json`.

> The key is also picked up from `OPENAI_API_KEY` if `KODEKEY_API_KEY` isn't set.

---

## Verify Installation

```bash
kkey --version     # → KodeKey 2.0.0
kkey --help        # CLI flags
```

Then, **from inside a project folder** (sessions and memory are per-project):

```bash
cd ~/my-project
kkey
```

You should see the banner, your workspace path, the active model, and the
`kkey main ❯` prompt. Type `/help` for the full command list.

---

## Usage

```bash
kkey                                # interactive session (REPL)
kkey "explain this repo"            # one-shot prompt, then exit
kkey "fix the failing tests" -m 2   # one-shot with a specific model
kkey -c                             # continue the most recent session
kkey -c "keep going on that bug"    # continue latest + new instruction
kkey -s bugfix-auth                 # load a named session
cat error.log | kkey "what broke?"  # pipe stdin into the prompt
```

### In the REPL

```
kkey main ❯ Add input validation to @src/forms.py and add tests
```

- `@path` **mentions** attach files/directories to your message (Tab-completes).
- The agent streams its answer, calls tools (shown with icons), and tracks every
  file change for `/undo`.
- `Ctrl-C` interrupts safely at any point — the conversation never gets corrupted.

---

## Slash Commands Reference

| Command | Description |
|---|---|
| `/help`, `/?` | Show help |
| `/save [name]` | Save the current conversation |
| `/load <name>` | Load a saved session (Tab completes names) |
| `/sessions` | List saved sessions for this project |
| `/delete <name>` | Delete a saved session |
| `/new` | Fresh session (context + changes + stats reset) |
| `/clear` | Clear conversation only |
| `/retry` | Re-run your last prompt |
| `/compact [note]` | Summarize the conversation to free context |
| `/export [file]` | Export the conversation to Markdown |
| `/memory [global\|project]` | Show memory files |
| `/memory edit [g\|p]` | Edit memory in `$EDITOR` |
| `/init` | Scan the codebase and auto-write project memory |
| `/models` | List available models |
| `/model <n\|name>` | Switch model (fuzzy match supported) |
| `/refresh` | Re-fetch the model list from the provider |
| `/undo` | Revert the agent's last file change |
| `/diff` | Show all session file changes (colored unified diff) |
| `/todos` | Show the agent's todo list |
| `/stats` | Tokens, cost estimate, tool usage, duration |
| `/plan [on\|off]` | Toggle read-only planning mode |
| `/config` · `/config set <k> <v>` | View / change settings |
| `exit` · `quit` · `q` | Quit (offers to save) |

### Custom slash commands

Create `​.kkey/commands/review.md` in your project (or `~/.kkey/commands/` for global):

```markdown
Review the code in $ARGUMENTS for bugs, security issues, and performance
problems. Report findings ranked by severity.
```

Now `/review src/auth.py` runs it with `$ARGUMENTS` → `src/auth.py`
(`$1`…`$9` are also available for positional args).

---

## Configuration

`~/.kkey/config.json` (created automatically; edit or use `/config set`):

```json
{
  "default_model": "claude-sonnet-4-6",
  "stream": true,
  "show_reasoning": true,
  "bash_timeout": 300,
  "context_chars": 600000,
  "max_read_lines": 2000,
  "safe_mode": false
}
```

| Key | Default | Description |
|---|---|---|
| `api_key` | — | API key fallback (env var wins) |
| `base_url` | — | Custom OpenAI-compatible endpoint |
| `default_model` | `""` | Preferred model at startup |
| `stream` | `true` | Stream tokens as they arrive |
| `show_reasoning` | `true` | Show reasoning tokens (dim) if the model sends them |
| `bash_timeout` | `300` | Default bash tool timeout in seconds (max 1800) |
| `context_chars` | `600000` | Auto-compact threshold (~150k tokens) |
| `max_read_lines` | `2000` | `read_file` page size |
| `safe_mode` | `false` | Re-enable the old file/command restrictions (off by default) |

Env overrides: `KODEKEY_API_KEY`, `KODEKEY_BASE_URL`, `OPENAI_API_KEY`,
`KKEY_DEBUG=1` (verbose tracebacks), `KKEY_STREAM=0/1`.

---

## Data Layout

```
~/.kkey/                     # global (user-level)
├── config.json              # settings
├── memory.md                # global memory — applies to all projects
├── history                  # persistent input history (↑/↓)
└── commands/                # global custom slash commands

<project>/.kkey/             # per-project
├── memory.md                # project memory (tech stack, conventions…)
├── sessions/*.json          # saved sessions (messages, changes, todos, usage)
└── commands/                # project custom slash commands
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `✖ No API key found` | Set `KODEKEY_API_KEY` (and restart the terminal on Windows), or put `"api_key"` in `~/.kkey/config.json` |
| `kkey: command not found` (Linux) | Ensure `~/.local/bin` is on your PATH and you relaunched the shell |
| `'kkey' is not recognized` (Windows) | Reopen the terminal after the PATH change; check `kkey.cmd` exists in your bin folder |
| Garbled symbols / `UnicodeEncodeError` (Windows) | Install [Windows Terminal](https://aka.ms/terminal), then run `setx PYTHONUTF8 1` and restart |
| No ↑/↓ history on Windows | `pip install pyreadline3` into the venv (included in the install steps above) |
| API errors / timeouts | Check the key and `KODEKEY_BASE_URL`; KodeKey retries 3× automatically — see `KKEY_DEBUG=1` output |
| Model list is empty or wrong | Run `/refresh` inside KodeKey; if using a custom endpoint, verify `base_url` |
| Old restrictions needed again | `/config set safe_mode on` |
| Colors look wrong in legacy `cmd.exe` | Use Windows Terminal — legacy consoles have poor ANSI support |
| Behind a corporate proxy | `export HTTPS_PROXY=http://proxy:port` (Linux) or `setx HTTPS_PROXY "http://proxy:port"` (Windows) |

---

## Updating & Uninstalling

**Update** — replace `kkey.py`, then refresh dependencies:

```bash
~/kkey/.venv/bin/pip install -U openai rich          # Linux
& "$HOME\kkey\.venv\Scripts\pip.exe" install -U openai rich pyreadline3   # Windows PowerShell
```

**Uninstall:**

```bash
rm -rf ~/kkey ~/.local/bin/kkey ~/.kkey              # Linux
```
```powershell
Remove-Item -Recurse $HOME\kkey, $HOME\bin\kkey.cmd, $HOME\.kkey   # Windows
```

Project `.kkey/` folders stay where they are — delete them per-project if desired.

---

**KodeKey v2.0.0** · built with Python · works with any OpenAI-compatible API
````

---