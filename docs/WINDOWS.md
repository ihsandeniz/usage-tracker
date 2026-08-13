# Windows

This tool was written on Linux, for Linux. It runs on Windows — CI builds a `.exe` and
exercises it on every release — but three of the four surfaces are Linux-shaped, and the
honest version of this page says so before it says anything else.

**Read this first: nobody has run this on a real Windows desktop yet.** Everything below is
either measured on a `windows-latest` CI runner (marked *measured*) or reasoned from the
code (marked *unverified*). The difference matters most in the places a runner cannot
reach: SmartScreen, antivirus, autostart, and whether your Claude data is where the tool
looks for it. If you try it, [open an issue](https://github.com/ihsandeniz/usage-tracker/issues)
with what you found — that is the missing half of this page.

## What you get, and what you do not

| Surface | Linux | Windows |
|---|---|---|
| Web panel (`http://127.0.0.1:8770`) | ✅ | ✅ *measured* |
| CLI — `usage · providers · guard · watch · doctor · config` | ✅ | ✅ *measured* |
| Bar feeder JSON (`usage --format waybar`) | ✅ | ✅ — the JSON is produced; no Windows bar consumes it out of the box |
| waybar badge (`surface/waybar-usage.sh`) | ✅ | ❌ bash + a Wayland bar. Use `usage --format waybar` instead |
| System tray icon | ✅ (needs Qt) | ❌ not in the packaged build — Qt is deliberately excluded |
| Floating widget (`surface/usage-widget`) | ✅ | ❌ bash + `hyprctl` |
| Setup wizard (`setup.sh`, `--ui`) | ✅ | ❌ bash |
| Autostart | `service.sh install` (systemd) | Task Scheduler, by hand — see below |

The panel and the CLI are the whole product on Windows. That is enough to answer "how much
have I used and what has it cost", which is what the tool is for.

## Install

### Option A — the single file (no Python)

1. Download `usage-tracker-<version>-windows-x64.exe` from
   [Releases](https://github.com/ihsandeniz/usage-tracker/releases).
2. Put it somewhere permanent — `%LOCALAPPDATA%\Programs\usage-tracker\` is a good choice.
   It never writes next to itself, so a read-only or protected folder is fine too.
3. Double-click it, or run it from a terminal. It serves the panel on
   **http://127.0.0.1:8770** and prints that address. Nothing opens a browser for you.

**SmartScreen will warn you.** *(unverified — CI cannot see SmartScreen.)* The binary is
unsigned: code-signing certificates cost money this project does not have. Windows shows
"Windows protected your PC" → *More info* → *Run anyway*. Antivirus false positives are
also common for PyInstaller binaries. If you are not comfortable with that — and it is a
reasonable thing not to be comfortable with — use Option B, where you can read every line
you run.

### Option B — from source (Python 3.9+)

```powershell
git clone https://github.com/ihsandeniz/usage-tracker.git
cd usage-tracker
python server.py
```

No dependencies, no build step, no virtualenv needed — the tool is stdlib-only. Every CLI
example that says `usage-tracker <command>` becomes `python server.py <command>`.

`install.sh`, `setup.sh` and `service.sh` are bash and do nothing useful here. Skip them.

## First run

```powershell
usage-tracker.exe doctor
```

`doctor` is the answer to "did it find anything". It prints eight checks — Python, paths,
price catalogue, Claude data, server, local computation, live limits, provider keys — and
never prints the *value* of an API key, only whether one is set. Paste its output into a
bug report without worrying.

The check that matters most on Windows is **Claude data**:

```
✓ Claude data       C:\Users\you\.claude\projects · 214 transcript file(s)
```

If it says the directory is missing, see [No data found](#no-data-found).

## Where things live

*measured on the CI runner via `python -m usage.platform`; the tool prints the real paths on
your machine with the same command.*

| | Path |
|---|---|
| settings, view config | `%APPDATA%\usage-tracker\` |
| calibration, live cache | `%LOCALAPPDATA%\usage-tracker\State\` |
| price catalogue cache | `%LOCALAPPDATA%\usage-tracker\Cache\` |

Nothing is ever written into the installation directory. To uninstall: delete the `.exe`
and those three folders. There is no registry key, no service, no installer.

What it *reads* — read-only, always:

| Source | Path |
|---|---|
| Claude Code transcripts | `%USERPROFILE%\.claude\projects\**\*.jsonl` |
| Claude live limits (OAuth token) | `%USERPROFILE%\.claude\.credentials.json` |
| Codex sessions | `%USERPROFILE%\.codex\sessions\` |
| Aider, Continue, Cody, Windsurf, Jan | under `%USERPROFILE%\` — see `doctor` |

> ⚠️ **If you use Claude Code inside WSL, this will find nothing.** *(unverified, but it follows
> from the paths)* WSL has its own home directory, and a Windows `.exe` looking at
> `%USERPROFILE%\.claude` will not see `\\wsl$\Ubuntu\home\you\.claude`. Run the Linux
> version inside WSL instead — that is the supported path, and the whole tool works there
> except the graphical surfaces.

## Provider API keys

Keys come from environment variables. `setx` writes them permanently:

```powershell
setx OPENROUTER_API_KEY "sk-or-..."
```

Then **open a new terminal** — `setx` does not touch the session you typed it in. Confirm
with `usage-tracker.exe doctor`, which lists key names and set/unset, never values.

The full table of provider → variable is in the [README](../README.md#provider-keys).

## Autostart

*(unverified — no runner can test Task Scheduler.)* There is no service installer for
Windows. Two options, both by hand:

**Startup folder** — press `Win+R`, type `shell:startup`, and put a shortcut to the `.exe`
in the folder that opens. It starts a console window at login.

**Task Scheduler** — no window, and it can restart on failure:

1. Task Scheduler → *Create Task*
2. General → *Run whether user is logged on or not* off; *Hidden* on
3. Triggers → *At log on*
4. Actions → *Start a program* → the full path to `usage-tracker.exe`
5. Settings → *If the task fails, restart every 1 minute*

Then open `http://127.0.0.1:8770` in a browser and pin the tab.

## Troubleshooting

### Port already in use

```powershell
$env:USAGE_PORT = "8771"; usage-tracker.exe
```

The port is always bound to `127.0.0.1` — never to a network interface, on any platform,
regardless of this variable.

### No data found

`doctor` reports "Claude data: not found". In order of likelihood:

1. **You use Claude Code in WSL** — see the warning above.
2. **You have never run Claude Code on this machine.** The tool reads transcripts; without
   them there is nothing to count.
3. **Claude Code stores its data somewhere else on your Windows install.** This is the one
   the author cannot check without a Windows machine. If `%USERPROFILE%\.claude` does not
   exist but you do use Claude Code natively on Windows, that is a real bug and worth an
   issue — include where the directory actually is.

### Live limit percentages are missing

The real (uncalibrated) limits come from Claude's OAuth token in
`%USERPROFILE%\.claude\.credentials.json`, read-only and never refreshed. *(unverified:
whether Claude Code on Windows keeps the token in that file or in Credential Manager.)* If
the file is not there, the panel falls back to calibrated estimates and says so — the number
is marked, not faked.

### Prices show as $0.00 or "unknown"

The price catalogue ships inside the binary (~5,800 models), so this should not happen on a
fresh install; `doctor` names the source it used. An unknown model is reported as unknown
rather than counted as zero — a missing price is not free.

### The panel loads but shows nothing

Check the API directly:

```powershell
curl http://127.0.0.1:8770/v1/usage
```

If that returns JSON and the page is still blank, it is a static-file problem, not a data
problem — please open an issue. This exact class of bug shipped in 0.2.1 and was found by
the first packaging run: the wire endpoint was correct while every panel file 404'd, because
a path comparison held one side unresolved and `%TEMP%` on Windows is often an 8.3 short
name. Fixed in 0.2.2; if you see it again the guard has found a third spelling.

## For a first real-machine run

If you are testing this on Windows for the first time, these are the answers worth
recording — they are exactly what the CI runner cannot produce:

- [ ] Does SmartScreen block it, and what does the dialog say?
- [ ] Does any antivirus quarantine the `.exe`?
- [ ] Does `doctor` find `%USERPROFILE%\.claude\projects` — with a native (non-WSL) install?
- [ ] Is `.credentials.json` there, and do live percentages appear?
- [ ] Does the panel render — cards, bars, the spend table — in a real browser?
- [ ] Cold start: how long from double-click to the panel answering?
- [ ] Does Task Scheduler autostart survive a reboot?
- [ ] Does the console show mojibake anywhere (`?` or boxes instead of `◐ █ ⚠ →`)?

The last one has history: before 0.2.2 the packaged tool crashed outright on its startup
banner whenever its output was redirected, because `→` does not exist in the legacy code
page Windows falls back to. It is fixed and pinned by tests, but that class of bug hides in
whichever surface nobody looked at.
