# usage-tracker — a "Pane for Linux"

A tiny, dependency-free tool that shows **all your AI usage, rate limits, and real $ spend** in one place on Linux — a native alternative to [Pane](https://github.com/ItsJazii/pane) (Windows-only).

- **Web panel** at `http://127.0.0.1:8770` — spend cards, 30-day chart, per-model table, live limit bars.
- **waybar badge** — `◐ 64%` in your bar, colored by how close you are to the wall.
- **floating widget** — the panel as a movable, resizable, always-on-top window.
- **guided setup** — `./setup.sh`, or `./setup.sh --ui` for the same steps in a browser window.
  Every step shows a real diff of what it is about to write, and can be undone from the same page.

Built with **stdlib Python + Vanilla JS**. No `pip install`, no Node, no Rust. Loopback-only.

> 🇹🇷 Türkçe: [README.tr.md](README.tr.md)

![usage-tracker popover](docs/screenshot.png)

> Demo with sample data; actual spend and limits will reflect your real providers.

---

## Why

If you use Claude Code, Codex, OpenRouter, Ollama etc., your spend and rate-limit status are scattered across dashboards — or invisible. usage-tracker reads what's already on your disk (transcripts) plus the providers' own live endpoints, and surfaces it where you can glance at it.

## Install

```bash
git clone https://github.com/ihsandeniz/usage-tracker.git
cd usage-tracker
./setup.sh            # ★ guided wizard: deps → server → waybar → widget/tray → keys → verify
```

Rather click than type? `./setup.sh --ui` runs the same six steps in a browser window —
each one showing a real diff of what it is about to write, with an Undo button next to it.

![the setup wizard showing the exact diff it will write to your waybar config](docs/setup-wizard.png)

```bash
./setup.sh --ui         # the wizard as a page (opens a window; prints the URL either way)
./setup.sh --auto       # non-interactive: recommended answer for every question
./setup.sh --uninstall  # undo everything the wizard set up (keys and repo stay)
./setup.sh --help
```

The terminal wizard stays the default, because it is the one that works over SSH, on a
headless box, and in a dotfiles script. Both faces call the same code — `setup.sh probe`,
`setup.sh do <step>`, `setup.sh undo <step>` — so they cannot drift apart.

What the wizard will and won't do to your system:

- **Backs up every file it touches** as `<file>.bak-usage-tracker`.
- **Edits your waybar config surgically** — comments and formatting survive. It parses the file
  before and after, and rolls back if the result isn't valid JSONC.
- **Refuses to guess.** Unparseable config, or a `modules-*` list that lives in an `include`?
  It leaves the file alone and hands you the snippet (on your clipboard) to paste yourself.
- **Idempotent.** Re-run it as often as you like; already-done steps are no-ops.
- **Imports API keys you already exported in your shell** into the keys file — because shell
  exports never reach the autostarted service, which used to leave cards silently empty.
- **Verifies instead of promising**: at the end it queries the server, counts resolved provider
  cards, and runs the waybar feeder once to show you its real output.
- No root, no network calls, nothing outside `~/.config` and this repo.

### Why `--ui` is a separate, temporary server

The long-running server (`:8770`) is **read-only**, and that is what lets this tool claim
nothing leaves your machine. Endpoints that edit your waybar config or touch your API keys
do not belong there, because a browser can be tricked into calling a localhost server: any
page you have open can POST to `127.0.0.1` (CORS blocks reading the reply, not sending the
request), and a domain that resolves to `127.0.0.1` becomes same-origin and can read replies
too. So `--ui` starts a **separate** server that

- picks an ephemeral port and mints a one-time token, passed in the URL fragment
  (fragments are never sent to a server, so the token cannot land in a log);
- rejects any request whose `Host` is not `127.0.0.1:<port>` — this is what stops DNS rebinding;
- rejects any request with a foreign `Origin`, and any API call without the token;
- **never returns a key value** — you can set or remove a key, never read one back;
- exits when you press Finish, or after ten idle minutes.

Key values reach `setup.sh` over stdin, never argv, which is world-readable in `/proc`.

Prefer to do it by hand? Skip the wizard:

```bash
./install.sh          # checks deps, generates config + keys file, prints snippets
./start.sh            # → http://127.0.0.1:8770
```

**Requirements:** Python 3.9+ (stdlib). `curl` + `jq` for the waybar feeder. A Chromium-family
browser (optional, for the floating widget), `hyprctl` (optional, auto-floats the widget on
Hyprland), `wl-copy`/`xclip` (optional, lets the wizard copy the waybar snippet). `install.sh`
reports what's missing.

## Autostart (recommended)

So the badge always has data, run the server on login. One command, no root — a **systemd user service** (works on any systemd distro):

```bash
./service.sh install      # generate + enable + start on login
./service.sh status       # check it
./service.sh uninstall    # remove it
```

Only the server needs autostart; the waybar badge and the floating widget attach to it.

<details><summary>Autostart without systemd</summary>

**Hyprland** (`hyprland.conf`):
```
exec-once = /ABS/PATH/start.sh
```

**Generic XDG autostart** — create `~/.config/autostart/usage-tracker.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=usage-tracker
Exec=/ABS/PATH/start.sh
X-GNOME-Autostart-enabled=true
```
</details>

## Provider keys

Claude Code, Codex and local runners (Ollama / LM Studio / Jan) need **no key** — they read files already on your disk. Hosted providers need an API key to show a card. A missing key just hides that card (**no dead cards**) — nothing breaks.

Keys live in **one file**, `~/.config/usage-tracker/env` (created by `install.sh`/`setup.sh`, `chmod 600`). It's loaded by both `./start.sh` and the systemd service, so keys work whether you run the server by hand or on login. Format is `KEY=value`, one per line — uncomment what you use:

| Provider | Env var | Shows | Get a key |
|---|---|---|---|
| OpenRouter | `OPENROUTER_API_KEY` | spend + credit + daily limit | <https://openrouter.ai/keys> |
| OpenAI | `OPENAI_ADMIN_KEY` | Costs API spend — needs an **admin** key (project key → 401) | Platform → *Admin keys* |
| DeepSeek | `DEEPSEEK_API_KEY` | balance *(candidate — not yet live-verified)* | <https://platform.deepseek.com> |
| ElevenLabs | `ELEVENLABS_API_KEY` | character quota + monthly reset | ElevenLabs → *Profile* |
| Together | `TOGETHER_API_KEY` | credit balance *(candidate)* | <https://api.together.xyz/settings/api-keys> |
| Novita | `NOVITA_API_KEY` | credit balance *(candidate)* | <https://novita.ai/settings/key-management> |
| DeepInfra | `DEEPINFRA_API_KEY` | credit balance *(candidate)* | DeepInfra → *API keys* |
| Hugging Face | `HUGGINGFACE_API_KEY` / `HF_TOKEN` | quota *(candidate)* | <https://huggingface.co/settings/tokens> |

After editing keys, restart the server: `./service.sh restart` (or re-run `./start.sh`). Providers marked *candidate* have documented endpoints not yet verified against a live key — if one misbehaves it shows an honest error card, never a wrong number.

## What it tracks

| Dimension | What | Source |
|---|---|---|
| **Spend** | Today / Yesterday / 30-day **$** + per-model + 30-day chart | `~/.claude/projects/**/*.jsonl` tokens × real prices |
| **Limits** | Session (5h) + Weekly usage %, reset countdown | Anthropic's own `/api/oauth/usage` (real), local estimate as fallback |
| **Providers** | 16 adapters: real $ (OpenRouter, OpenAI, DeepSeek), credit/quota (ElevenLabs, HuggingFace, Together, Novita, DeepInfra), local (Ollama, LM Studio, Jan), local-log tokens (Codex, Aider, Continue, Cody, Windsurf) | each provider's API / local files |

### Is the data real?

Trust matters, so every number is labelled by source — nothing is silently made up:

- **Token counts: 100% real** — read from transcripts, not estimated.
- **Prices: real** — Opus/Sonnet/Haiku from the [models.dev](https://models.dev) catalog (`source: catalog`); models not yet in the catalog use official vendor pricing (`source: official`). Anything unverified is flagged `source: estimate`.
- **Limits: real, no calibration** — pulled from the same endpoint Claude Code's `/usage` uses.
- **If you're on a subscription (Max, ChatGPT Plus…),** the $ figure is the **API-equivalent cost** (what this usage would cost pay-as-you-go) = the value you're getting from the subscription. Your actual bill is the flat fee, not this number.
- **Can't be separated:** Opus fast-mode and 1M-context premium pricing don't appear in the transcript's model field, so they're computed at standard rates (real cost may be slightly higher).

## The surface — two independent options

Everything under `surface/` is **isolated** (it never touches your existing `~/.config/waybar` or
`~/.config`) and the two surfaces are **independent** — install the waybar badge, the floating
widget, both, or neither. Neither depends on the other.

### Option A — waybar badge

A compact `◐ NN%` module in your bar; the headline is the highest Claude limit %, the tooltip
lists every provider, and clicking it opens the web panel. `install.sh` prints the ready-to-paste
`custom/usage` snippet + `style.css` colors. Standalone — no browser required.

### Option B — floating widget

The web panel as a **movable + resizable** always-on-top window (a Chromium-family `--app`
window). Unlike a bar widget, you position and size it however you like:

- **Move** it — `Super`+drag (or your compositor's move binding).
- **Resize** it — `Super`+right-drag, or drag the edges. The browser remembers your size/position.

```bash
surface/usage-widget open      # show it
surface/usage-widget close     # hide it
surface/usage-widget toggle    # show/hide (good for the waybar on-click)
surface/usage-widget status    # open / closed
```

On **Hyprland** it's auto-floated, pinned (visible on every workspace), and placed top-right at
`WIDGET_SIZE` — no config edit needed. On other compositors, float/size it with your own controls.
Requires a Chromium-family browser (chromium / chrome / brave / …). Standalone — no waybar required.

#### Desktop compatibility

The **waybar badge** works on any systemd-based Linux (Hyprland, GNOME, Plasma, etc.).  
The **floating widget** requires a Chromium-family browser and works on any Linux:
- **Hyprland:** Auto-float/pin built-in.
- **GNOME / Plasma / Cinnamon:** Float/pin it with your compositor's native controls (usually right-click title bar → Properties).
- **i3 / Openbox:** Floating layers work; consult your config for layering.

The core **web panel** (`http://127.0.0.1:8770`) runs on every Linux and every desktop environment — no special setup needed.

Autostart (Hyprland): `exec-once = /ABS/PATH/surface/usage-widget open`.

## Configuration

`surface/surface.conf` (created by `install.sh`, git-ignored — machine-specific):

```sh
USAGE_URL="http://127.0.0.1:8770"
WIDGET_SIZE="360x520"                 # floating widget initial size (you can drag/resize after)
WIDGET_CLASS="usage-tracker-widget"
```

### Adding providers

Provider adapters live in `usage/providers/`. Each module exposes `collect(days) -> dict | None`; returning `None` means "no card" (a missing/unconfigured provider is silently hidden — **no dead cards**). To add one, drop `usage/providers/<name>.py` and register it in `_ADAPTERS`. OpenRouter reads `$OPENROUTER_API_KEY` from the environment.

## HTTP endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/spend?days=30` | Today/Yesterday/30d $ + byModel + byDay |
| `GET /api/usage` | Claude limit panel (`source: live\|calibration`) |
| `GET /api/live[?force=1]` | Raw Anthropic live usage (verification) |
| `GET /api/providers` | Multi-provider cards |
| `GET /v1/usage` | Stable wire-format (`schema: usage/v1`) — used by the waybar feeder |

## Security & privacy

- Binds **`127.0.0.1` only** — never exposed to the network.
- **Read-only** to your data. OAuth tokens in `~/.claude/.credentials.json` are only *read*, never written or refreshed (writing could drop your active session).
- No telemetry, no external calls except the providers' own APIs you already use.
- Static file serving is path-traversal protected.

## Development

```bash
python3 -m usage.pricing    # price-resolution spot-check
python3 -m usage.engine     # limits + $ summary from real transcripts (no server)
```

## Roadmap

- [x] Spend + real prices, live Claude limits (no calibration)
- [x] Multi-provider adapters — 16 across 4 kinds (spend / tokens / local / quota)
  - real $: OpenRouter, OpenAI (Costs API, admin key), DeepSeek *(balance API — documented, candidate)*
  - credit/quota: ElevenLabs, HuggingFace, Together, Novita, DeepInfra *(some candidate — verify with a live key)*
  - local ($0): Ollama, LM Studio, Jan
  - local-log tokens: Codex, Aider, Continue, Cody, Windsurf
- [x] waybar badge + movable/resizable floating widget
- [x] Per-provider data selection (⚙ View tab → `view_config.json`; waybar/widget/panel obey)
- [x] Optional native system-tray icon (Qt `QSystemTrayIcon`; SNI → waybar tray)
- [ ] Verify candidate endpoints (Together / Novita / DeepInfra / HuggingFace / DeepSeek) against live keys

## Contributing

Issues and PRs welcome. The design constraints are deliberate: **stdlib Python + Vanilla JS, zero runtime dependencies, loopback-only, every number labelled by source.** Please keep them.

## License

[MIT](LICENSE) © 2026 İhsan Deniz
