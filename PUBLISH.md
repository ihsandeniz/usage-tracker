# Publishing usage-tracker as a public repo

This folder currently lives **inside** the private vault repo. To open-source it cleanly
(without leaking vault history), copy it out into a fresh repo and push that.

## 0. Prep (once)

```bash
gh auth status          # make sure you're logged in as ihsandeniz
```

## 1. Copy to a clean location (no vault history)

```bash
SRC="$HOME/Masaüstü/vibe-cod-organized/projects/usage-tracker"
DEST="$HOME/code/usage-tracker"          # any path outside the vault
mkdir -p "$DEST"
# copy source only — skip machine-specific/generated/caches
rsync -a --exclude='.git' \
         --exclude='__pycache__' --exclude='*.pyc' --exclude='*.log' \
         --exclude='usage_calib.json' \
         --exclude='view_config.json' \
         --exclude='surface/surface.conf' \
         --exclude='CLAUDE.md' \
         "$SRC/" "$DEST/"
cd "$DEST"
```

> `surface.conf` is **generated per-machine** by `./install.sh`, so it must NOT be committed.
> `.gitignore` already lists it; the rsync exclude is belt-and-suspenders.
> `view_config.json` and `usage_calib.json` are the same kind of thing — your own
> state (which cards you hid, your calibration), not the project's.

## 2. Sanity check before pushing

```bash
./install.sh            # should generate config + print snippets, no errors
./start.sh &            # server on :8770
sleep 2
curl -s 127.0.0.1:8770/v1/usage | jq .schema     # → "usage/v1"
./surface/waybar-usage.sh | jq .text             # → "◐ NN%"  (NOT empty)
kill %1
grep -rn "/home/" . --include='*.py' --include='*.js' --include='*.sh' || echo "no hardcoded home paths ✓"

# systemd service template verification
mkdir -p ~/.config/systemd/user
sed "s|@@ROOT@@|$PWD|g; s|@@PYTHON@@|$(command -v python3)|g" \
    packaging/usage-tracker.service.in > /tmp/test-usage-tracker.service
grep -q "@@ROOT@@\|@@PYTHON@@" /tmp/test-usage-tracker.service && \
  { echo "❌ FAIL: systemd template has unsubstituted placeholders"; exit 1; } || \
  echo "✓ systemd service template validates"
```

All of these must pass. Empty feeder output = a regression in the jq guards — do not ship.
Empty template check = placeholders left in service file — do not ship.

## 3. Add a screenshot (recommended)

Drop a popover screenshot at `docs/screenshot.png` and uncomment the image line near the
top of `README.md`. A screenshot dramatically raises adoption on GitHub.

## 4. Create the repo and push

```bash
git init -b main
git add -A
git commit -m "usage-tracker: Pane-for-Linux — spend, live limits, waybar badge + floating widget"
gh repo create usage-tracker --public --source=. --remote=origin \
   --description "A Pane-for-Linux: all your AI usage, rate limits and real \$ spend in a waybar badge + a movable/resizable widget. stdlib Python + Vanilla JS, zero deps." \
   --push
```

## 5. After publishing

- Add topics: `gh repo edit --add-topic linux,hyprland,waybar,claude,ai,usage-tracker,cost-tracking`
- Set the About / homepage.
- Consider a short GIF of the badge → hover → panel flow.

## Not published from here

Nothing is pushed automatically. This file is a checklist; run step 4 yourself when ready.
