# SolanaPulse 🟣

**Auto-updating Solana ecosystem report & interactive dashboard.**

One Python file. Zero API keys. Zero dependencies (stdlib only). Pulls live data from three public sources, detects anomalies, and emits three synchronized outputs on every run.

## Live demo

https://solanapulse.surge.sh (auto-refreshes every 5 minutes)

## What it does

Every run, SolanaPulse:

1. **Collects live data** from:
   - **Solana RPC** (`api.mainnet-beta.solana.com`) — health, current slot, epoch info, recent performance samples (TPS / slot times), vote accounts (validator status), supply
   - **DeFiLlama** — chain TVL, 24h DEX volume + change
   - **CoinGecko** — SOL price (24h/7d change), market cap + rank, volume, ATH, FDV, supply

2. **Detects anomalies** with rule-based thresholds:
   | Rule | Level | Trigger |
   |------|-------|---------|
   | TPS Drop | HIGH | current TPS < 50% of 5-sample average |
   | TPS Spike | HIGH | current TPS > 200% of 5-sample average |
   | Slow Slot Time | MEDIUM | avg slot time > 0.6s |
   | High Delinquency | MEDIUM | > 5% of validators delinquent |
   | SOL Price Move | HIGH | |24h price change| > 10% |
   | DEX Volume Shift | MEDIUM | |24h DEX volume change| > 30% |

3. **Generates three outputs**:
   - `dashboard.html` — dark-theme interactive dashboard (responsive, TPS bar chart, epoch progress bar, anomaly cards, auto-refresh meta tag)
   - `report.md` — human-readable markdown report
   - `report.json` — machine-readable structured report (stable schema, ready for downstream automation)

## Quick start

```bash
python3 solana_pulse.py
# -> dashboard.html, report.md, report.json
```

Requires Python 3.8+. Nothing else. No pip install, no API keys, no config.

## Auto-updating deployment

Cron (every 5 minutes):

```cron
*/5 * * * * cd /path/to/solana-pulse && python3 solana_pulse.py
```

Serve `dashboard.html` with anything (nginx, GitHub Pages via Actions, surge, a bucket). The page carries `<meta http-equiv="refresh" content="300">` so viewers always see fresh data without JS frameworks.

### GitHub Actions (included)

`.github/workflows/update.yml` regenerates the report every 15 minutes and commits the refreshed `dashboard.html` / `report.md` / `report.json` back to the repo — the repo itself becomes the auto-updating report.

## Design decisions

- **stdlib only** — `urllib` + `json`; nothing to install, nothing to break, trivially auditable.
- **Retry with backoff** — each fetch retries 3x; a failed source degrades gracefully instead of killing the run.
- **camelCase/snake_case tolerant** — RPC field parsing accepts both, guarding against client/proxy normalization differences.
- **Single file** — copy `solana_pulse.py` anywhere and run it.

## Output schema (report.json)

```
report_metadata { generated_at, generator, data_sources, auto_refresh_interval }
network_health: "ok" | ...
network_performance { current_slot, current_tps, tps_samples[], avg_slot_time_seconds, slot_time_samples[] }
epoch { current_epoch, slot_index, slots_in_epoch, epoch_progress_pct }
validators { active, delinquent, total, delinquency_rate_pct }
supply { total_supply_sol, circulating_sol, non_circulating_sol }
economic_indicators { sol_price_usd, ..., market_cap_usd, fdv_usd }
defi_metrics { tvl_usd, dex_volume_24h_usd, dex_volume_change_pct }
anomaly_detection [ { level, metric, value, detail } ]
```

## License

MIT
