# latency-bench — who sees the tennis point first

> **STATUS: harness only — no numbers published yet.**
> Nothing in this repository is a measurement. It is the instrument. Numbers appear only
> after a run with an Ultra key, and only with N, date range and a per-tour split.

MIT. One asyncio process, every source stamped on the same host clock.

## What is measured

For each scoring event *e* of a live match — **point, game, break, set, retirement,
walkover** — the wall-clock at which each source **first reflects it**:

| column | source | what it is |
|---|---|---|
| `t_lta_ws` | Live Tennis API push feed (ULTRA) | frame on `match:{id}` / `point:match:{id}`; PointFrame carries `seq`, `ts` |
| `t_lta_poll` | Live Tennis API `GET /matches/{id}` | first poll showing the new state, at the tier's cadence (free/Basic) |
| `t_pm_ws` | Polymarket CLOB WebSocket, market channel (keyless) | **market repriced** — best bid/ask or last trade moved |
| `t_pm_gamma` | Polymarket Gamma REST poll (keyless) | **market repriced** — `outcomePrices`/best bid/ask moved |
| `t_kalshi` | Kalshi `GET /markets/{ticker}/orderbook` (keyless) | **market repriced** — best yes/no level moved |
| `t_sofa` | Sofascore event poll | **private reference clock only** (see truth rules) |

Output: `events.csv` (one row per match × event: `match_id, tour, event_kind, event_id,
wallclock_utc, earliest_source, t_<source>...` where `t_` is **seconds behind the earliest
observer**) and `summary.json` (median / p90 per tour × event kind × source, with N and date
range, plus which source was earliest most often per tour — if a source beats us on a tour,
the markdown summary prints it).

### "market repriced" is not "score arrived"

Polymarket and Kalshi are *markets*. They never publish a score. A price move may be a
trader reacting to the point, to a TV feed, to a bookmaker, or to nothing. The harness
attaches the **first reprice observed at or after the earliest score observation** of an
event, within `--market-window` seconds (default 20), and each reprice serves at most one
event. That is all a reprice delta means. Label it "market repriced" everywhere; never
"score arrived", never "Polymarket saw the point".

### Clock

All deltas use `time.monotonic()` on the one harness host, stamped the instant bytes arrive
(before JSON parsing). Wall-clock (`datetime.now(UTC)`) is recorded for date ranges only.
`latency-bench ntp` / the start of `run` logs the host offset via `chronyc tracking`,
`ntpdate -q` or `ntpq -p` (whichever exists) into `out/run_meta.json`. Venue timestamps
(`ts`, Polymarket `timestamp`, Sofascore) are **never** used for deltas.

## Truth rules (from the plan — binding)

- **Polymarket** ToS (2026-08-11) bans redistributing their data. This harness publishes
  *timing deltas*, not prices. Prices stay in `observations.jsonl` on the bench host. Ben /
  counsel sign-off is still required before any page goes live.
- **Sofascore** is a private reference clock. The adapter is opt-in (`--sofa`), its
  observations are **never persisted** (only the detected events' kind + id + our
  timestamps are), and nothing from it is ever shown as a data product.
- Numbers appear only with **N, date range and per-tour split**. No "sub-second" claim
  unless measured. Per-kind rows with small N are still rows — do not hide them, do not
  headline them.
- Retirement / walkover are detected from `event_status` / `outcome` / `withdrew`, **never**
  from the lifecycle `status` flipping to `completed`.

## What needs an Ultra key

- `lta_ws` (push feed): `GET /ws-token` is ULTRA-gated; the `point:*` channel family only
  appears in the token's `channels` vocabulary for keys whose plan carries the point surface.
- `lta_poll` works on any tier, but the poll cadence *is* the tier's latency; run it at the
  cadence the tier's budget allows and say so next to the number.
- Everything else (Polymarket Gamma + CLOB WS, Kalshi) is keyless and read-only.

Set `LTA_API_KEY` or pass `--lta-key`. Without a key the `lta_*` sources are disabled and
the run measures markets only (useful for plumbing checks, useless as a benchmark).

## Usage

```bash
uv venv && uv pip install -e '.[dev]'
latency-bench ntp
latency-bench run --matches matches.json --duration 3600 --out out/ --lta-ws [--sofa]
latency-bench report --out out/ --market-window 20
```

`matches.json` is a list of `MatchSpec` objects:

```json
[{"match_key": "177256", "tour": "atp", "lta_match_id": 177256,
  "pm_market_id": "3804316", "pm_token_ids": ["8678…", "1234…"],
  "kalshi_ticker": "KXATPMATCH-26AUG23FILTIA-TIA", "sofa_event_id": null}]
```

Matching is out of scope here: Polymarket ids come from `polymarket-tennis`; Kalshi tickers
from the event-ticker date + surname method (`KXATPMATCH-26AUG23FILTIA-TIA`).

## Layout

```
src/latency_bench/
  models.py      Observation / ScoreEvent, source + kind vocabularies, digest()
  events.py      EventDetector: consecutive observations -> point/game/break/set/retirement/walkover, or repriced
  aligner.py     per (match, event): first sighting per source, seconds-behind-earliest, market attach
  report.py      events.csv + summary.json + summary.md
  store.py       JSONL persistence (observations, events)
  clock.py       ntp_check()
  cli.py         run / report / ntp
  sources/       lta_poll, lta_ws, polymarket_gamma, polymarket_ws, kalshi_orderbook, sofascore
tests/           offline fixtures per adapter, synthetic timeline -> known deltas, report formatting
```

## Verified vs assumed (2026-08-23)

**Verified by reading https://docs.livetennisapi.com/reference.html:**
`GET /ws-token` → `{token, expires_in, ws_url, channels}`; Centrifugo v2 JSON protocol —
`{"connect":{"token":…},"id":1}`, `{"subscribe":{"channel":"match:{id}"},"id":2}`,
publications `{"push":{"channel":…,"pub":{"data":<frame>}}}`, heartbeat `{}` answered with
`{}`, newline-batched messages, fresh token on every reconnect; channel families
`match:{id}`, `slate:all`, `point:match:{id}`, `point:slate`, `signal:*`; PointFrame fields
`type, match_id, point, seq (per-match monotonic, never skips), ts (capture time, may be
null), pbp_coverage, quality`; match object fields `players.p1.name`, `sets`, `games`
(player-major), `points`, `server`, `status`, `event_status`, `outcome`, `withdrew`,
`event_status_updated_at`; list endpoints `{data, meta}`, single resources bare.

**Verified by keyless GET (read-only):** Gamma `/markets` and `/events` shapes, including
`outcomePrices` / `outcomes` / `clobTokenIds` as JSON-encoded strings; Kalshi
`/markets?series_ticker=KXATPMATCH` and `/markets/{ticker}/orderbook` → `orderbook_fp`
with `yes_dollars` / `no_dollars` `[price, size]` string levels ascending by price (the last
yes level matched the market's `yes_bid_dollars`).

**Cited from https://docs.polymarket.com/developers/CLOB/websocket/wss-overview:**
URL `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscribe
`{"assets_ids":[…],"type":"market"}`, event types `book`, `price_change`,
`last_trade_price`, `tick_size_change` and their fields.

**Assumed (not verifiable without a key / a live capture):**
1. The exact key layout of a push-feed score frame (we accept `id` or `match_id`, and fall
   back to the channel suffix) and whether `seq`/`ts` sit beside or inside `point` in a
   PointFrame (we read both places). The docs describe fields, not a verbatim example.
2. `ws_url` needs no extra headers / subprotocol.
3. Polymarket WS may batch events as a JSON array and accepts a text `PING` keepalive
   (community-client convention, not on the cited page).
4. Kalshi orderbook level ordering is ascending by price on both sides (observed once).
5. The Sofascore event JSON shape (`homeScore.period1..`, `.point`, `firstToServe`) is from
   memory; no Sofascore call was made. Re-check against a capture before enabling `--sofa`.
6. Break detection = game won by the player who was *not* `server` in the previous
   observation. If a source lags by a whole game, its `server` can be stale and the break is
   attributed from that source's own view — identities still pair because `event_id` is
   built from the resulting score, not from the break flag.
