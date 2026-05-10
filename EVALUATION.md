# Evaluator Notes

Official scoring is live Derive public-market-data paper trading. Replay fixtures are for development, tests, and smoke validation only.

## Submission Contract

- The submitted path must be named `model_submission.py`.
- It must define exactly one class named `ModelSubmission`.
- `ModelSubmission` must subclass `derivebench.Model`.
- Static scanning is fail-closed before import.
- The runtime accepts only `LONG_STRADDLE`, `SHORT_STRADDLE`, and `FLAT`.

## Data Contract

Use Derive public REST endpoints only: `get_all_instruments`, `get_all_currencies`, and `get_tickers`. `get_ticker` is intentionally not used because Derive has documented it as deprecated in favor of `get_tickers`.

No private endpoints, wallets, approvals, transfers, or real orders are used. Live fills are paper fills against Derive executable top-of-book quotes captured in the tick stream.

## Official Run

The host-side official wrapper builds a Docker image and mounts only the candidate file read-only plus a writable output directory:

```bash
python scripts/run_official_evaluation.py \
  --submission /path/to/model_submission.py \
  --output /tmp/derive_eval \
  --duration 3600
```

The container runner then executes `scripts/run_candidate.py --official`, which checks that the official marker environment exists and writes both `report.json` and `ticks.parquet`.

## Acceptance Criteria

Reviewers should require:

- scanner acceptance,
- nonzero live Derive ticks,
- active BTC option legs on each tick,
- fresh Derive timestamps and sane bid/ask/mid package prices,
- no invalid model or benchmark signal sides,
- no synthetic option fills,
- report/ticks consistency,
- low timeout rate,
- final liquidation with no unresolved exposure,
- independent accounting recomputation from `ticks.parquet`,
- model score compared to the shipped benchmark on the same tick stream.

Primary score:

```text
primary_score = pnl_total * max(sharpe, 0) * (1 - max_drawdown)
```

Do not accept replay scores as official live scores.

Run both checks after the official live run:

```bash
python scripts/validate_live_run.py --report /tmp/derive_eval/report.json --ticks /tmp/derive_eval/ticks.parquet --min-duration 3600 --min-ticks 600
python scripts/audit_accounting.py --report /tmp/derive_eval/report.json --ticks /tmp/derive_eval/ticks.parquet
```
