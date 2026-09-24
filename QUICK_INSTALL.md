# KodeKey — Quick Install

## Quick install

Put `kkey.py` and the installer for your OS in one folder:

**Linux / macOS**
```bash
bash install.sh
```

**Windows** (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Both installers are re-runnable (they double as updaters) and support
`--uninstall` / `-Uninstall`. Then set your API key and run `kkey`.
The manual steps in `README.md` are only needed if you prefer installing by hand.

## How to use

**Linux / macOS** (folder containing `kkey.py` + `install.sh`):
```bash
bash install.sh                    # install / update (safe to re-run)
bash install.sh --uninstall        # remove (asks before deleting data)
bash install.sh --dir ~/tools/kkey --link   # custom location, symlinked
```

**Windows** (folder containing `kkey.py` + `install.ps1`, in PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1          # install / update
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File .\install.ps1 -InstallDir "D:\tools\kkey"
```

The `-ExecutionPolicy Bypass` flag is needed only for this one run — it doesn't change your system policy.

## What they do

| Step | Detail |
|---|---|
| Find Python 3.9+ | Checks `python3`/`py -3`/`python`, verifies version |
| Create venv | `~/kkey/.venv` (Linux) or `~\kkey\.venv` (Windows); reused on re-run |
| Install packages | `openai` required; `rich` + `pyreadline3` best-effort (warn, don't fail) |
| Install launcher | `~/.local/bin/kkey` / `~\bin\kkey.cmd` — no activation needed |
| Fix PATH | Appends to the right rc file (`.bashrc`/`.zshrc`/`.profile`/fish) or user PATH on Windows |
| Self-test | Runs `kkey --version` end-to-end and reports the result |
| Extras | Sets `PYTHONUTF8=1` on Windows; ripgrep install hint on both |

Both are **idempotent** — re-running after replacing `kkey.py` performs an in-place update without touching your `~/.kkey` config, memory, or history.
