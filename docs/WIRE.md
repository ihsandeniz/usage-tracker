# `/v1/usage` — the wire format

`GET http://127.0.0.1:8770/v1/usage` is a **public contract**, not an internal detail.
Four surfaces in this repo read it (web panel, waybar module, tray icon, floating widget)
and so, as far as we know, do other people's scripts. Treat it the way you would treat a
library API.

`?all=1` skips the user's hidden-provider filter and returns every card.

## Compatibility rules

| Change | Allowed in | Note |
|---|---|---|
| Add a field | any release | Consumers must ignore unknown fields |
| Add an enum value (e.g. a new `status`) | any release | Consumers must not crash on unknown values — treat as the nearest known one |
| Change a field's meaning | major only | Even if the type is unchanged |
| Rename or remove a field | major only | Ship the old name alongside for one minor first |
| Tighten a range (e.g. `pct` now capped) | major only | |

`schema` is `"usage/v1"`. It changes only on a major, breaking revision.

> **Why `used` and `units` are both there:** `used` shipped in v0.2.0 and outside consumers
> read it. `units` is the same number under the name `/api/usage` uses. Collapsing them to
> one name is a v0.3.0 (breaking) job. Do not "clean this up" in a patch release.

## Top level

```jsonc
{
  "schema": "usage/v1",
  "generatedAtMs": 1770000000000,      // when this response was assembled
  "generatedAt": "2026-08-11T22:54:47",
  "providers": [ /* claude first, then the rest */ ]
}
```

`providers[0]` is always the Claude card. Everything after it is an adapter card.

## The Claude card

```jsonc
{
  "id": "claude", "name": "Claude Code",
  "calibrated": true,
  "live": {                            // freshness of the Anthropic OAuth usage endpoint
    "ok": true,
    "error": null,
    "cached": false,
    "fetchedAtMs": 1770000000000,      // when the DATA arrived, not when we replied. null if never
    "ageSec": 107.1,                   // null if there has never been a successful fetch
    "stale": false,                    // ageSec > 600 — see the note below
    "rateLimited": false,
    "rateLimitTier": null
  },
  "limits": {
    "session":     { "pct": 62.5, "used": 1234.5, "units": 1234.5, "budget": 2000,
                     "calibSuspect": false, "resetAtMs": 1770000000000, "resetInSec": 9000 },
    "weekly":      { /* same shape */ },
    "weeklyModel": { /* same shape, plus "name" */ },
    "thresholds":  { "warn": 75, "crit": 90 }   // THE source of truth — see below
  },
  "spend": {
    "currency": "USD",
    "today": 99.55, "yesterday": 41.02, "last30d": 5086.92,
    "byModel": [ { "model": "...", "short": "opus", "usd": 12.3, "source": "catalog" } ],
    "priceComplete": true,
    "estimatedModels": [],             // priced from a tier guess or an override marked 'estimate'
    "unknownPriceModels": [],          // [{model, tokens}] — NOT included in the $ totals
    "catalog": { /* see below */ }
  }
}
```

### `live.stale` changed meaning on 2026-08-11

It used to mean *"this response came from cache because the fetch failed."* It now means
*"this data is older than 600 seconds."* The two are different: a fetch can fail while the
cached number is still only three minutes old, and in that case the number is fine. The
reason a fetch failed is still reported separately in `live.staleReason`.

600 s is roughly 3 % of the shortest quota window (5 h) and five times the 120 s cache TTL:
long enough to ride out a brief outage, short enough that hours-old data never wears a
green "live" badge.

No surface in this repo read `live.stale` before the change, and `live` itself was not in
`/v1/usage` at all until then — so this is a new field in practice, not a silent break.

### `spend.catalog` — where the dollar figures came from

```jsonc
{
  "source": "hermes",                  // "user" | "hermes" | "bundled" | "none"
  "sourceLabel": "Hermes CLI cache",
  "path": "/home/you/.hermes/models_dev_cache.json",
  "generatedAt": "2026-08-03",
  "ageDays": 8,
  "stale": false,                      // ageDays > 45
  "modelCount": 5608,
  "providerCount": 168,
  "warning": null                      // a human sentence when the catalogue is empty or stale
}
```

**A surface that shows dollars must show `warning` when it is non-null.** The whole point of
the field is that a $0.00 with no catalogue behind it used to look identical to a real $0.00.

### `spend.unknownPriceModels`

Tokens whose model is in no catalogue and matches no tier. They are **excluded** from
`today` / `yesterday` / `last30d`, which therefore report a **floor**, not a total. Before
2026-08-11 they were multiplied by a zero price and folded in, which made an incomplete
total look complete.

## Adapter cards

```jsonc
{
  "id": "codex", "name": "Codex",
  "kind": "tokens",                    // "spend" | "tokens" | "local" | "quota"
  "available": true,
  "status": "ok",                      // "ok" | "nodata" | "offline" | "error" | "partial"
  "truncated": false,                  // the scan hit a ceiling; the numbers are a floor
  "truncatedReason": null,             // "files" | "lines"
  "error": null,
  "currency": "USD",                   // READ THIS — do not hardcode "$"
  "auth": "chatgpt",
  "windowDays": 30,
  "sessions": 12,
  "tokens": { "input": 0, "output": 0, "total": 0 },
  "today":   { "tokens": 0, "usd": 0 },
  "total":   { "tokens": 0, "usd": 0 },
  "byModel": [], "byDay": [],
  "usdSource": "catalog",              // "catalog" | "estimate"
  "note": "..."                        // human sentence; carries the truncation warning
}
```

### `status: "partial"` (added 2026-08-11)

The adapter found data but stopped before reading all of it — a file-count or line-count
ceiling. The totals are a floor. Render it like `ok`, but the incompleteness has to be
visible; `note` already carries a sentence saying so.

A consumer that does not know `"partial"` should fall back to treating it as `"ok"` rather
than hiding the card.

### `currency`

Produced by the backend in about twenty places and, until 2026-08-11, read by nothing —
a CNY balance was rendered with a `$`. Every surface formats amounts using this field.

## Thresholds

`providers[0].limits.thresholds` is the **only** source of `warn` / `crit`. Surfaces must
not carry their own copy; a hardcoded 75/90 that happens to match today will silently
diverge the moment the server's config changes. A single fallback constant per surface, used
only when the server cannot be reached, is the one exception.

## Rounding

Percentages are rendered to one decimal, **half-up** (`floor(x * 10 + 0.5) / 10`), on every
surface. jq's `round` is banker's rounding and disagrees with Python and JavaScript at the
midpoint — 62.45 became 62.4 in waybar and 62.5 everywhere else.

## Testing against this contract

`tests/` asserts the shape and the numbers. Adding a field means adding an assertion.
Run: `python3 -m unittest discover -s tests -t .`
