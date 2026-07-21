# surface/ — Linux surface (waybar badge + floating widget)

Two **independent** surfaces — install either, both, or neither; they don't depend on each other.
Both read the server's JSON and run **isolated**: they never touch your existing `~/.config`.

> Prerequisite: the server must be running → `./start.sh` (or `./service.sh install`).
> Run `./install.sh` once first — it generates `surface.conf` and marks the scripts executable.

## Files

| File | What |
|---|---|
| `surface.conf` | your config (server URL + widget size/class). Generated from `surface.conf.example`; git-ignored. |
| `waybar-usage.sh` | waybar custom-module feeder → JSON (`text`/`tooltip`/`class`). Server down → `○ —`. |
| `usage-widget` | floating-widget launcher: `open` / `close` / `toggle` / `status`. |

## Config (`surface.conf`)

```sh
USAGE_URL="http://127.0.0.1:8770"
WIDGET_SIZE="360x520"                 # floating widget initial size (drag/resize after)
WIDGET_CLASS="usage-tracker-widget"
```

## Option A — waybar badge (standalone)

`install.sh` prints the exact snippet with absolute paths. In short — add to
`~/.config/waybar/config.jsonc`:

```jsonc
"custom/usage": {
  "exec": "/ABS/PATH/surface/waybar-usage.sh",
  "return-type": "json",
  "interval": 30,
  "on-click": "/ABS/PATH/surface/usage-widget toggle"
}
```

Add `"custom/usage"` to a `modules-*` list, and to `style.css`:

```css
#custom-usage.ok   { color: #34d399; }
#custom-usage.warn { color: #fbbf24; }
#custom-usage.crit { color: #f87171; }
#custom-usage.off  { color: #7b8ba0; }
```

Test without touching waybar: `./waybar-usage.sh | jq .`

## Option B — floating widget (standalone)

The web panel as a **movable + resizable** always-on-top window (a Chromium-family `--app`
window; the browser remembers its size/position).

```bash
./usage-widget open      # show it
./usage-widget close     # hide it
./usage-widget toggle    # show/hide (waybar on-click)
./usage-widget status    # open / closed
```

On **Hyprland** the launcher floats + pins the window and sizes/places it top-right automatically
(via `hyprctl dispatch` on the mapped window — no config edit). Move it with `Super`+drag, resize
with `Super`+right-drag or the edges. On other compositors, float/size it however you normally do.

Autostart (Hyprland): `exec-once = /ABS/PATH/surface/usage-widget open`.

### How it targets the window

Chromium gives `--app` windows their own class (`chrome-<slug>`) and sets the title only after the
page loads, so map-time window rules don't stick. The launcher instead waits for the window (its
title starts with `usage-tracker`) and applies float/size/move/pin **by address** — reliable
across browsers.

## Option C — proactive notifications (`usage-notify.sh`)

Sends system notifications when Claude usage crosses thresholds (default: **75% warn** · **90% critical**).
Like CodexBar's built-in alerts, but on Linux via `notify-send` (libnotify).

**Install**: nothing — it's a shell script. Just run `./usage-notify.sh once` or set up a timer (see below).

### How it works

- Polls `/api/usage` every check (or on a cron/timer schedule)
- Extracts the **highest Claude limit %** (session + weekly + model-specific)
- When it crosses a threshold → sends a notification with `notify-send`
- **Deduplication**: remembers the last threshold notified in `~/.local/share/usage-tracker/notify-state`
  so you only get **one alert per threshold** until usage drops below it again
- If the server is down, it silently continues (no spam)
- If `notify-send` is missing, prints a warning and exits gracefully

### Usage

```bash
# One-time check (good for cron / systemd timer)
./usage-notify.sh once

# Continuous watch (polls every N seconds; default 300)
./usage-notify.sh watch 300
```

### Thresholds & config

Edit thresholds by setting `USAGE_NOTIFY_THRESHOLDS` before running:

```bash
USAGE_NOTIFY_THRESHOLDS="75,90" ./usage-notify.sh once    # warn at 75%, critical at 90%
USAGE_NOTIFY_THRESHOLDS="50,80" ./usage-notify.sh watch   # custom: warn 50%, critical 80%
```

Or add to `surface.conf`:

```sh
USAGE_NOTIFY_THRESHOLDS="70,85"   # customize thresholds
USAGE_NOTIFY_STATE_DIR="~/.local/share/usage-tracker"  # state file location (default)
```

### Automate with systemd user timer (optional)

Create `~/.config/systemd/user/usage-notify.service`:

```ini
[Unit]
Description=usage-tracker proactive notifications
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/ABS/PATH/surface/usage-notify.sh watch 300
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

Then enable & start:

```bash
systemctl --user enable --now usage-notify
systemctl --user status usage-notify
```

Or as a **one-shot timer** (every 5 min):

Create `~/.config/systemd/user/usage-notify.timer`:

```ini
[Unit]
Description=usage-tracker notification timer
Requires=usage-notify.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Unit=usage-notify.service

[Install]
WantedBy=timers.target
```

And the service (`~/.config/systemd/user/usage-notify.service`):

```ini
[Unit]
Description=usage-tracker notification check

[Service]
Type=oneshot
ExecStart=/ABS/PATH/surface/usage-notify.sh once
```

Enable:

```bash
systemctl --user enable --now usage-notify.timer
systemctl --user list-timers
```

### Dependencies

- `notify-send` (from `libnotify` package) — available on virtually all Linux desktops
- `curl` — for HTTP (already used by `waybar-usage.sh`)
- `bash` + standard tools (`mkdir`, `echo`, etc.)
- **Optional**: `jq` for JSON parsing; if missing, falls back to `python3` (already required by `server.py`)

### State file

Notifications are deduped using `~/.local/share/usage-tracker/notify-state`:

- Contains the last-notified threshold value
- Auto-created on first run
- Reset when usage drops back below all thresholds (so next threshold-cross triggers again)
- Safe to delete anytime (just means next run will re-notify if threshold is still active)
