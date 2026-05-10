# Derive BTC OTM Straddle Take-Home

Build one Python model that decides whether to be long, short, or flat a harness-selected BTC out-of-the-money straddle package on Derive. The evaluator chooses the option legs, expiry, strikes, fill prices, order timing, fees, and risk limits. Your model only sees public market data and returns a target side.

```python
from derivebench import FLAT, Model, Side, Signal, Tick

class ModelSubmission(Model):
    def on_tick(self, tick: Tick) -> Signal | None:
        if tick.package_spread_pct > 0.20:
            return FLAT
        if tick.package_mid < tick.package_mark * 0.96:
            return Signal(side=Side.LONG_STRADDLE, size=0.5, confidence=0.6)
        if tick.package_mid > tick.package_mark * 1.05:
            return Signal(side=Side.SHORT_STRADDLE, size=0.3, confidence=0.6)
        return FLAT
```

## Candidate API

Submit exactly one file named `model_submission.py` containing `class ModelSubmission(derivebench.Model)`. The only accepted sides are:

- `Side.LONG_STRADDLE`
- `Side.SHORT_STRADDLE`
- `Side.FLAT`

Candidates cannot choose calls, puts, strike, expiry, direct leg exposure, order type, or fill timing.

## Package Rule

For each tick, the harness constructs a deterministic BTC OTM straddle package from Derive public data:

1. Read BTC spot/index from Derive public currency/ticker fields.
2. For the same active expiry, choose the nearest active OTM call with strike above BTC spot and the nearest active OTM put with strike below BTC spot.
3. Require executable bid/ask quotes, minimum visible size, and a configurable spread filter.
4. If the nearest strike fails liquidity, walk outward within the same expiry. If no liquid same-expiry package exists, move to the next active expiry. If live listings are temporarily thin, the tick is skipped rather than filled synthetically.

Each `Tick` includes timestamps, selected call/put names, expiry, strikes, time to expiry, BTC spot/index, call/put bid/ask/mid/mark, package bid/ask/mid/mark, recent BTC/package windows, spread/liquidity fields, feed source, and package id.

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e . -r requirements.txt
```

## Local Replay

```bash
.venv/bin/python scripts/scan_submission.py model_submissions/benchmark_straddle/model_submission.py
.venv/bin/python scripts/run_baseline.py --mode replay --data tests/fixtures/derive_replay.json --output runs/replay_baseline --duration 30
.venv/bin/python scripts/validate_live_run.py --report runs/replay_baseline/report.json --ticks runs/replay_baseline/ticks.parquet --min-duration 1 --min-ticks 1 --allow-replay
.venv/bin/python scripts/audit_accounting.py --report runs/replay_baseline/report.json --ticks runs/replay_baseline/ticks.parquet
```

## Live Public-Data Run

The live runner uses only Derive public endpoints:

- `get_all_instruments`
- `get_all_currencies`
- `get_tickers`

It does not use wallets, private endpoints, approvals, or real orders.

```bash
.venv/bin/python scripts/run_baseline.py --mode live --duration 3600 --output runs/live_baseline
.venv/bin/python scripts/validate_live_run.py --report runs/live_baseline/report.json --ticks runs/live_baseline/ticks.parquet --min-duration 3600 --min-ticks 600
.venv/bin/python scripts/audit_accounting.py --report runs/live_baseline/report.json --ticks runs/live_baseline/ticks.parquet
```

## Scoring

Signals are queued and executed on the next recorded tick. Long straddles buy both legs at executable asks plus slippage and fees. Short straddles sell/short both legs at executable bids minus slippage and fees, are bounded by capital/margin settings, and are marked at buy-to-cover asks. Open exposure is liquidated at the end of the run using executable quotes.

The primary score mirrors the Polymarket take-home:

```text
primary_score = pnl_total * max(sharpe, 0) * (1 - max_drawdown)
```

Reports also include timeout rate, segment hit rate, trade count, spread paid, slippage/fees, long/short exposure, and feed-health metrics.

The validator rejects stale live feeds, non-Derive replay output unless explicitly allowed, invalid sides, non-OTM package rows, unresolved exposure, timeout rates above the configured limit, report/parquet drift, and accounting mismatches from recomputing PnL and score from `ticks.parquet`.
