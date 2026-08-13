# The command line

Every other surface in this project is something you *look at*. The CLI is the one another
program can *ask*:

```bash
usage-tracker guard --threshold 80 || echo "not now — the weekly quota is nearly gone"
```

It needs no server (it computes the numbers in-process when nothing answers on `:8770`),
no bash, no jq and no curl — which is also how Windows gets a usable surface while the
`.sh` feeders stay Linux-only.

## Running it

| You have | Command |
|---|---|
| the repo | `python3 -m usage.cli <command>` — or `python3 server.py <command>` |
| the single-file build | `usage-tracker <command>` (same binary that serves the panel) |

A convenience alias, if you want the short name:

```bash
alias usage-tracker='python3 /path/to/usage-tracker/server.py'
```

Bare `server.py` with no arguments still starts the panel server — the systemd unit and
`start.sh` are unaffected.

## Where the numbers come from

| Flag | Behaviour |
|---|---|
| *(none)* | try `http://127.0.0.1:8770`, fall back to computing locally |
| `--url URL` | that server **only** — no local fallback |
| `--local` | compute here; never touch the server |

Naming a URL turns the fallback off on purpose: if you asked a specific server and got a
locally computed number instead, the output would misreport where it came from. Every
command prints (or reports in JSON) which of the three it used.

Computing locally scans your transcripts and takes a second or two; asking a running server
is instant and shares its caches. That is why the server wins by default.

## `usage` — what is going on

```bash
usage-tracker usage                      # human readable
usage-tracker usage --format json        # the /v1/usage wire document, verbatim
usage-tracker usage --format waybar      # {"text":…,"tooltip":…,"class":…}
usage-tracker usage --provider openrouter
```

```
usage-tracker 0.3.0 · source: server

Claude Code  [live]
     session         7.0%   resets in 4h32m
     weekly         65.0%   resets in 22h32m
   ? weekly model       —   [opus]
     spend        today $32.29 · yesterday $165.44 · 30d $5,032.13
     prices       Hermes CLI cache · 5,608 models
```

`—` means *unknown*, not zero. A percentage the tool cannot stand behind is never rendered
as a number, and `!!`/`!` in the left margin mark the crit/warn levels **as configured on
the server**, not as hardcoded here.

`--format waybar` is the Bash feeder without the Bash: same headline, same `ok`/`warn`/
`crit`/`off` class. It exits 0 even when nothing answers, because a feeder that fails is a
bar module that disappears.

## `providers` — which cards exist and why one is missing

```bash
usage-tracker providers
usage-tracker providers --format json
usage-tracker providers --all          # include cards hidden by your config
```

```
  claude        claude   ok
  ollama        local    offline  Binary installed, service down. Start it with `ollama serve`.
  codex         tokens   ok       ChatGPT subscription — the $ figure is the API-equivalent cost
```

An adapter with no key and no data returns nothing at all ("no dead cards"), so a provider
you expect and cannot find here has not been configured. `doctor` tells you which
environment variable it wants.

## `guard` — the exit code contract

This is the one command other programs depend on. **The exit codes are as public as the
wire format:**

| Exit | Meaning |
|---|---|
| `0` | below the warn threshold |
| `1` | at or above `warn` |
| `2` | at or above `crit` |
| `3` | no usable number (server unreachable, or nothing measured yet) |
| `64` | you called it wrong (bad flag, unknown command) |

64 rather than argparse's default of 2 — because 2 already means *critical* to a script
that only knows this table.

`64` covers the dispatcher too, not just the flags of a known command. `usage-tracker gaurd`
and `usage-tracker --threshold 80` (subcommand forgotten) both exit 64. They used to start
the panel server and exit 0, which made `usage-tracker gaurd || skip_job` hang instead of
skip — the failure the 64 decision exists to prevent, one layer above where it was applied.
Found by running the packaged binary; the unit tests were exercising `python -m usage.cli`,
which never passes through the dispatcher.

```bash
usage-tracker guard                        # server's warn/crit
usage-tracker guard --threshold 80         # one boundary: exit 1 above 80
usage-tracker guard --provider all         # worst card wins
usage-tracker guard --json
```

```json
{"level":"warn","exitCode":1,"pct":80.0,"scope":"claude/session",
 "thresholds":{"warn":75,"crit":90},"crossed":75,"stale":false,"ageSec":null,
 "source":"server","message":"claude/session at 80.0%"}
```

`--threshold N` collapses the two tiers into one boundary and returns 1 above it. It does
not turn N into *critical*: you asked to be told when you pass a line, not to redefine
what an emergency is.

Exit 3 deserves its own sentence: **unknown is not "fine"**. A missing percentage that
exited 0 would silently green-light exactly the expensive job you wanted to hold back.

### A threshold you cannot compare against is refused (64)

`--threshold`, `--warn` and `--crit` take a finite number in `0 < n <= 100`. Anything else —
`nan`, `inf`, `1e400` (which overflows to inf), `0`, `101` — exits **64**, the same answer
`abc` always got.

`nan` used to be accepted, and that was the dangerous case rather than a cosmetic one:
IEEE-754 makes every comparison with NaN false, so `pct >= crit` and `pct >= warn` both
answered "no" and the level fell through to `ok`. Measured at 99 % usage, `guard --threshold
nan` exited **0**. A script written as `guard --threshold "$LIMIT" || skip` inherits that the
day `$LIMIT` is computed wrong. Thresholds arriving from the wire get the same check: a
non-comparable pair falls back to 75/90 instead of disabling the comparison.

### A stale number is unknown, not good news (3)

If the percentage came from the live endpoint and that reading is older than the freshness
window (`limits.*.stale` in the wire), an otherwise-`ok` verdict becomes `unknown` → exit 3,
and `stale` / `ageSec` appear in `--json` and in the message:

```console
$ usage-tracker guard --json
{"level":"unknown","exitCode":3,"pct":5.0,"stale":true,"ageSec":604800.0,
 "message":"claude/session at 5.0% — stale (7d0h old): the last live reading failed"}
```

A stale `warn`/`crit` keeps its level: both block the job, and only the specific one tells
you which wall you are near. This closes a measured hole — a seven-day-old 5 % was reported
as `ok`/0 with no mark on it at all, so the expensive job it was meant to hold back ran.

### Using it

```bash
# Don't start a long agent run when the weekly quota is nearly gone
usage-tracker guard --quiet --threshold 85 || { echo "quota is tight, come back later"; exit 0; }

# Shell prompt segment
usage-tracker guard --quiet; case $? in 1) echo "⚠";; 2) echo "🛑";; esac
```

## `watch` — fire once per crossing

```bash
usage-tracker watch --interval 300 --notify
usage-tracker watch --exec 'notify-send "Claude" "$UT_MESSAGE"'
usage-tracker watch --once            # for cron / systemd timers
```

Edge-triggered: it fires when the level *changes* — `ok → warn`, `warn → crit`, and back
down again — not on every poll. An alert that repeats every five minutes is an alert you
learn to ignore. The last level is remembered in `watch-state.json` under your state
directory, so restarting the watcher does not re-alert you.

An unknown percentage never fires. No number is not the same as a bad number.

`--exec` runs your command through the shell with the data in the environment, never
spliced into the command string:

| Variable | Example |
|---|---|
| `UT_LEVEL` / `UT_PREVIOUS` | `crit` / `warn` |
| `UT_PCT` | `92.4` (empty when unknown) |
| `UT_SCOPE` | `claude/weekly` |
| `UT_PROVIDER` | `claude` |
| `UT_THRESHOLD` | the boundary that was crossed |
| `UT_MESSAGE` | `claude/weekly at 92.4% (over 90)` |
| `UT_SOURCE` | `server` or `local` |

This replaces `surface/usage-notify.sh`, which is kept for now but no longer where new
behaviour goes: it hardcoded its own 75/90 instead of reading the server's thresholds, and
it needs bash, curl and jq to do what one Python process does everywhere.

## `doctor` — before you open an issue

```bash
usage-tracker doctor
usage-tracker doctor --json          # paste-able into a bug report
usage-tracker doctor --probe         # also contact provider APIs (network, uses your keys)
```

```
  ✓ Python            3.13.5
  ✓ Paths             linux · config ~/.config/usage-tracker · state ~/.local/state/usage-tracker
  ✓ Price catalogue   bundled snapshot · 5,857 models · 9 days old
  ✓ Claude data       ~/.claude/projects · 500+ transcript file(s)
  ! Server            http://127.0.0.1:8770/v1/usage unreachable (URLError)
      → Start it with `./service.sh start` — the CLI works without it, surfaces do not.
  ✓ Local computation assembled in 0.42s · session 7.0% · 30d $5,032.13
  ✓ Provider keys     none set · unset: deepseek, elevenlabs, openai, openrouter, …
```

Exit 0 when nothing failed, 1 when something did. Warnings do not fail it: a stopped server
is a normal state for someone who only uses the CLI.

**It never prints a key value** — only the variable's name and whether it is set. A
diagnostic people paste in public must not carry their credentials. By default it also does
not contact any provider API; `--probe` opts into that explicitly.

## `config` — what you want to see, and where the alert line sits

```bash
usage-tracker config                       # cards, thresholds, refresh — and the file paths
usage-tracker config --hide ollama         # any card except claude — see below
usage-tracker config --show ollama
usage-tracker config --reset

usage-tracker config --warn 60 --crit 85   # move the alert line for EVERY surface
usage-tracker config --refresh 60          # how often the panel polls, in seconds
```

Card visibility writes `view_config.json`; thresholds and the refresh interval write
`settings.json`. Both are the same files the panel's ⚙ section writes, so panel, waybar,
tray and CLI never disagree.

Moving a threshold here moves it everywhere, because the pair travels in the wire:

```console
$ usage-tracker config --warn 10 --crit 20
$ usage-tracker guard ; echo $?
crit: claude/weekly at 69.0% (over 20.0)
2
```

The Claude card is the one you cannot hide. `/v1/usage` guarantees `providers[0]` is that
card and the waybar badge, the tray and `guard` all read it, so hiding it does not shrink a
view — it switches the alerts off. The refusal exits 64 and writes nothing; a config file
from an older build that already hides it is ignored rather than obeyed.

Thresholds are validated in one place (`usage/settings.py`): `0 < warn < crit <= 100`.
A rejected write leaves the previous pair untouched and exits 1 with the reason — silently
resetting to 75/90 would only be discovered when an alert failed to fire.

**Keys are not settable here, or in the panel.** Both surfaces show the environment variable
name and whether it is set, never the value. Writing keys belongs to `./setup.sh --ui`
(step `keys`), which runs on a random port behind a one-time token and shuts itself down;
the panel server has none of those properties and stays up all day.

## Design notes

* **Thresholds are the server's.** Every surface used to carry its own 75/90 and they
  diverged silently the day the server's configuration changed (`docs/WIRE.md § Thresholds`).
  The CLI reads `thresholds` from the wire; the constant in `cli.py` is used only when no
  wire could be fetched at all.
* **Half-up rounding**, like the panel, the badge and the tray: `floor(x * 10 + 0.5) / 10`.
  62.45 is 62.5 here too — jq's banker's rounding made it 62.4 in exactly one place once.
* **Output is English** and stays English, like the rest of the terminal output in this
  repo. The panel is the localised surface (TR/EN); a CLI whose output changes shape with
  the user's language is a CLI you cannot grep.

## `setup` — install it, and be able to undo that

```bash
usage-tracker setup              # question by question, in the terminal
usage-tracker setup --ui         # the same steps as a page in your browser
usage-tracker setup --auto       # no questions; never writes a key
usage-tracker setup --uninstall  # remove what the wizard wrote (keys and data stay)
```

Four steps and a check: put the binary somewhere permanent, start it at logon, add a
shortcut that opens the panel, optionally store provider keys, then run `doctor`.

Two rules make it safe to try:

* **It shows the file before it writes it.** `setup --preview autostart` prints the exact
  text, path included. The browser page shows the same thing behind *Show what it writes*.
* **It only takes back what it gave.** Every file it writes carries a marker line. `undo`
  reads that marker: a file the wizard did not write is left alone, and — on Linux — a
  service it did not install is never stopped. Overwriting someone else's file keeps a
  `.bak-usage-tracker` copy, and a second run does not clobber the first backup.

On Linux from a source checkout, `./setup.sh` does more (waybar, tray, the floating widget).
This wizard is what a single binary can do, on either platform.

Machine mode — the browser page and any script speak the same protocol, stdout is JSON only:

```bash
usage-tracker setup --probe            # state of every step
usage-tracker setup --preview <step>   # what that step would write
usage-tracker setup --do <step>        # apply it
usage-tracker setup --undo <step>      # revert it
```

Keys are the exception to argv: `--do keys --set-key NAME` reads the value from **stdin**,
because an argument is visible in `/proc` to every other user and lands in shell history.

## `panel` — open it, starting the server if needed

```bash
usage-tracker panel            # open the browser; start the server first if nothing answers
usage-tracker panel --no-open  # just make sure it is running
```

This is what the desktop shortcut calls. It never starts a second server: if `:8770`
already answers, it only opens the page.
