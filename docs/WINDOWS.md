# Installing on Windows

> [Türkçe sürüm](WINDOWS.tr.md) · This page covers Windows 10 and 11.

You do not have to type any commands. Download one file, double-click it, and the program
asks you the rest.

---

## Install — three steps

### 1. Download the file

Go to the [latest release](https://github.com/ihsandeniz/usage-tracker/releases/latest) and
click **`usage-tracker-<version>-windows-x64.exe`**. One file, about 10 MB. There is no
separate installer and no runtime to install first: the wizard, the panel and the command
line all live inside that one file.

Leaving it in your Downloads folder is fine — the program will offer to copy itself
somewhere permanent in a moment.

### 2. Double-click it

**What you will see:** Windows will most likely show a blue warning:

> **Windows protected your PC** — Microsoft Defender SmartScreen prevented an unrecognized
> app from starting.

This does **not** mean a virus was found. It means "I have not seen this file before and it
carries no digital signature". Signing requires a paid certificate this project does not
have. To continue:

1. Click **More info**
2. Click the **Run anyway** button that appears

> If you would rather not do that — a reasonable position — see
> [Running from source](#running-from-source) at the end: there, every line you run is
> readable Python.

### 3. The wizard opens by itself

A **black console window** appears (that is normal — the program lives there) and **the
setup page opens in your browser on its own**. The **TR/EN** button in the top right
switches languages.

The wizard asks about four things. All optional, all undoable:

| Step | What it does | Suggested |
|---|---|---|
| **Put the program somewhere permanent** | Copies the file into `%LOCALAPPDATA%\Programs\usage-tracker\` | ✅ Yes — so the shortcut survives emptying Downloads |
| **Start it when you log in** | Starts with **no window** every time you turn the machine on | ✅ Yes — this is the part that makes it effortless |
| **Panel shortcut** | Adds "usage-tracker" to the Start menu | ✅ Yes |
| **API keys** | Only for *hosted* providers like OpenRouter | ⏭️ Skip for now — Claude usage needs **no key** |

Every step has a **"Show what it writes"** button: it prints the exact path and the exact
contents before anything is written. Any step can be reversed with **"Undo"** from the same
page.

When you are done, click **Finish**. The wizard closes and the panel opens. That is the
whole installation.

---

## After it is installed

- **Opening the panel:** type `usage-tracker` in the Start menu and click it. (Or visit
  <http://127.0.0.1:8770>.)
- **Where it runs:** on your machine only, bound to `127.0.0.1`. It is not reachable from
  the network and it sends your data nowhere.
- **Removing it:** see [Uninstalling](#uninstalling).

---

## If something went wrong

### I double-clicked, a black window opened, and nothing else happened

That was the behaviour of **older versions**, and it is fixed. From `v0.4.0` on, the browser
opens by itself. Check your version: it is the first line in the black window
(`usage-tracker 0.4.0 → http://127.0.0.1:8770`).

If your browser still does not open, type the address from that window yourself:
**http://127.0.0.1:8770**

### I closed the black window and the program stopped

That is correct behaviour: the window *is* the program. Closing it stops it.

The fix is the wizard's **"Start it when you log in"** step. After that the program starts
with no window at all, and you never see that console again.

### Windows deleted the file / my antivirus quarantined it

PyInstaller-packaged programs trigger false positives from time to time. Your options:

1. Mark the downloaded file as an **exception** in your antivirus
2. Or [run from source](#running-from-source) — every line there is readable Python
3. If you want to check the file is the one that was published, every release ships a
   `SHA256SUMS.txt`

### The panel opened but there is no data

The program reads what Claude Code writes on your machine:
`C:\Users\<your-name>\.claude\projects`

If that folder does not exist, there is nothing to show. The usual reasons:

- **You use Claude Code inside WSL.** WSL has its own home directory and a Windows program
  cannot see it. Run the Linux version inside WSL instead.
- You have never run Claude Code on this machine.

To check, run `usage-tracker doctor` — it names every path it looked at.

### "Port already in use"

Something else is holding port 8770. Run it on another port: open the Start menu, type
`cmd`, and paste this (adjust the path if yours differs):

```
set USAGE_PORT=8771
"%LOCALAPPDATA%\Programs\usage-tracker\usage-tracker.exe"
```

---

## Uninstalling

The easiest way to undo everything the wizard wrote is to open the wizard again and press
**Undo** on each step.

By hand, there are three places:

| What | Where |
|---|---|
| The program itself | `%LOCALAPPDATA%\Programs\usage-tracker\` |
| Autostart | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\usage-tracker.vbs` |
| Your settings and data | `%APPDATA%\usage-tracker\` and `%LOCALAPPDATA%\usage-tracker\` |

Nothing is written to the registry — except provider API keys if you added any
(`HKCU\Environment`), which the wizard's key step also removes.

---

## What you get on Windows, and what you do not

| | Available |
|---|---|
| Web panel | ✅ |
| Setup wizard (browser and terminal) | ✅ |
| Command line (`usage`, `guard`, `doctor`…) | ✅ |
| Windowless start at logon | ✅ |
| System tray icon | ❌ needs Qt, deliberately not bundled |
| waybar badge / floating widget | ❌ these are Linux desktop surfaces |

The panel and the command line are the whole product here, and they are enough to answer
"how much have I used and what has it cost".

---

## The command line, if you want it

Everything the wizard does has a command behind it, and the packaged `.exe` *is* that
command:

```powershell
usage-tracker setup            # the wizard, in the terminal
usage-tracker setup --ui       # the wizard, in a browser
usage-tracker setup --auto     # no questions
usage-tracker setup --uninstall
usage-tracker panel            # open the panel, starting the server if needed
usage-tracker doctor           # what is configured, what is not
usage-tracker guard --quiet    # exit code 0/1/2/3 — for your own scripts
```

Full reference: [`CLI.md`](CLI.md).

---

## Running from source

If you would rather not run an unsigned `.exe`:

```powershell
git clone https://github.com/ihsandeniz/usage-tracker.git
cd usage-tracker
python server.py
```

Python 3.9 or newer is enough — no dependencies, no build step. On this path every
`usage-tracker <command>` becomes `python server.py <command>`:

```powershell
python server.py setup --ui     # the wizard, in a browser
python server.py doctor         # what was found, what is missing
python server.py usage          # limits and spend, in the terminal
```

---

## Which claims on this page are measured

This project is developed on Linux; the Windows side is measured by automated runs on
GitHub's `windows-latest` machines. The honest split:

**Measured**, on real Windows, on every release:
- the `.exe` starts and serves the panel and the wire
- prices come from the catalogue inside the package
- the wizard writes to the Startup folder, undoes it, and leaves a file it did not write alone
- on a fresh machine, launching it opens the wizard, and *Finish* hands over to the panel
- the console window explains itself in both languages

**Not measured** — these need a real desktop:
- how SmartScreen and antivirus behave
- whether autostart survives a reboot
- cold start time
- where Claude Code keeps its data on your particular Windows install

If you try any of these, [open an issue](https://github.com/ihsandeniz/usage-tracker/issues).
That list is the missing half of this page.
