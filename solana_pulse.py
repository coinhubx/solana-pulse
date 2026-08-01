#!/usr/bin/env python3
"""
SolanaPulse — Auto-updating Solana ecosystem report & interactive dashboard.

Pulls live data from:
  - Solana RPC (getSlot, getBlockTime, getEpochInfo, getRecentPerformanceSamples,
    getVoteAccounts, getSupply, getHealth, getSignaturesForAddress)
  - DeFiLlama API (TVL, DEX volume)
  - CoinGecko API (SOL price, market cap)

Outputs:
  - dashboard.html  (dark-theme interactive HTML)
  - report.md       (human-readable markdown)
  - report.json     (machine-readable structured JSON)

No API keys required. Python stdlib + urllib only.
"""
import json, urllib.request, time, datetime, os, sys, math

RPC_URL = "https://api.mainnet-beta.solana.com"
DEFILLAMA_URL = "https://api.llama.fi/v2/chains"
DEFILLAMA_DEX = "https://api.llama.fi/overview/dexs/solana?dataType=dailyVolume&excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/solana?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false&sparkline=false"
SOLANA_FOUNDATION = "2Eo6eR5GD7LfhMms1Ai9oz4b7HnTamsaxaQyqsyUgQ7r"  # for getSignaturesForAddress demo
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 30

def fetch_json(url, headers=None, data=None, method="POST", is_rpc=False):
    h = {"User-Agent": UA, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt == 2:
                return {"error": str(e)}
            time.sleep(2)
    return {"error": "max retries"}

def rpc_call(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        payload["params"] = params
    return fetch_json(RPC_URL, data=payload, method="POST")

def collect_rpc_data():
    """Collect all RPC data points in a single batch."""
    results = {}
    # Batch all RPC calls
    calls = [
        ("health", lambda: rpc_call("getHealth")),
        ("slot", lambda: rpc_call("getSlot")),
        ("epoch_info", lambda: rpc_call("getEpochInfo")),
        ("performance", lambda: rpc_call("getRecentPerformanceSamples", [5])),
        ("vote_accounts", lambda: rpc_call("getVoteAccounts")),
        ("supply", lambda: rpc_call("getSupply")),
    ]
    for name, fn in calls:
        results[name] = fn()
        time.sleep(0.3)  # be nice to RPC
    return results

def collect_defillama():
    results = {}
    # Chain TVL
    chains = fetch_json(DEFILLAMA_URL, method="GET")
    if isinstance(chains, list):
        sol = next((c for c in chains if c.get("name","").lower() == "solana"), None)
        if sol:
            results["tvl"] = sol.get("tvl", 0)
            results["tvl_24h_change"] = sol.get("change_1h", 0)  # closest available
    # DEX volume — fields are top-level: total24h, change_1d
    dex = fetch_json(DEFILLAMA_DEX, method="GET")
    if isinstance(dex, dict) and not dex.get("error"):
        results["dex_volume_24h"] = dex.get("total24h", 0) or 0
        results["dex_volume_change"] = dex.get("change_1d", 0) or 0
    return results

def collect_coingecko():
    data = fetch_json(COINGECKO_URL, method="GET")
    if data.get("error"):
        return data
    md = data.get("market_data", {})
    return {
        "price_usd": md.get("current_price", {}).get("usd", 0),
        "price_change_24h": md.get("price_change_percentage_24h", 0),
        "price_change_7d": md.get("price_change_percentage_7d", 0),
        "market_cap_usd": md.get("market_cap", {}).get("usd", 0),
        "market_cap_rank": md.get("market_cap_rank", 0),
        "volume_24h": md.get("total_volume", {}).get("usd", 0),
        "ath": md.get("ath", {}).get("usd", 0),
        "ath_change_pct": md.get("ath_change_percentage", {}).get("usd", 0),
        "circulating_supply": md.get("circulating_supply", 0),
        "total_supply": md.get("total_supply", 0),
        "fdv": md.get("fully_diluted_valuation", {}).get("usd", 0),
    }

def detect_anomalies(rpc, defi, cg):
    anomalies = []
    perf = rpc.get("performance", {}).get("result", [])
    if perf:
        # Solana RPC returns camelCase keys: numTransactions, samplePeriodSecs, numSlots
        def get_tps(s):
            ntx = s.get("numTransactions", s.get("num_transactions", 0))
            sp = s.get("samplePeriodSecs", s.get("sample_period_secs", 1))
            return ntx / max(sp, 1)
        def get_slot_time(s):
            ns = s.get("numSlots", s.get("num_slots", 0))
            sp = s.get("samplePeriodSecs", s.get("sample_period_secs", 1))
            return sp / max(ns, 1) if ns else 0

        avg_tps = sum(get_tps(s) for s in perf) / len(perf)
        latest_tps = get_tps(perf[0])
        if avg_tps > 0:
            ratio = latest_tps / avg_tps
            if ratio < 0.5:
                anomalies.append({"level": "HIGH", "metric": "TPS Drop", "value": f"{latest_tps:.0f} TPS", "detail": f"Current TPS is {ratio*100:.0f}% of 5-sample avg ({avg_tps:.0f} TPS)"})
            elif ratio > 2.0:
                anomalies.append({"level": "HIGH", "metric": "TPS Spike", "value": f"{latest_tps:.0f} TPS", "detail": f"Current TPS is {ratio*100:.0f}% of 5-sample avg ({avg_tps:.0f} TPS)"})

        avg_slot = sum(get_slot_time(s) for s in perf) / len(perf)
        if avg_slot > 0.6:
            anomalies.append({"level": "MEDIUM", "metric": "Slow Slot Time", "value": f"{avg_slot:.3f}s", "detail": f"Average slot time {avg_slot:.3f}s exceeds 0.6s threshold"})
    
    # Validator delinquency
    va = rpc.get("vote_accounts", {}).get("result", {})
    if va:
        current = len(va.get("current", []))
        delinquent = len(va.get("delinquent", []))
        total = current + delinquent
        if total > 0 and delinquent / total > 0.05:
            anomalies.append({"level": "MEDIUM", "metric": "High Delinquency", "value": f"{delinquent}/{total}", "detail": f"{delinquent*100/total:.1f}% of validators delinquent"})
    
    # Price moves
    if cg.get("price_change_24h") and abs(cg["price_change_24h"]) > 10:
        anomalies.append({"level": "HIGH", "metric": "SOL Price Move", "value": f"{cg['price_change_24h']:+.1f}%", "detail": f"SOL moved {cg['price_change_24h']:+.1f}% in 24h"})
    
    # TVL changes
    if defi.get("dex_volume_change") and abs(defi["dex_volume_change"]) > 30:
        anomalies.append({"level": "MEDIUM", "metric": "DEX Volume Shift", "value": f"{defi['dex_volume_change']:+.1f}%", "detail": f"DEX volume changed {defi['dex_volume_change']:+.1f}% in 24h"})
    
    return anomalies

def generate_json_report(rpc, defi, cg, anomalies):
    now = datetime.datetime.now(datetime.timezone.utc)
    perf = rpc.get("performance", {}).get("result", [])
    va = rpc.get("vote_accounts", {}).get("result", {})
    supply = rpc.get("supply", {}).get("result", {})
    epoch = rpc.get("epoch_info", {}).get("result", {})
    
    tps_samples = []
    slot_times = []
    if perf:
        for s in perf:
            ntx = s.get("numTransactions", s.get("num_transactions", 0))
            sp = s.get("samplePeriodSecs", s.get("sample_period_secs", 1))
            ns = s.get("numSlots", s.get("num_slots", 0))
            tps_samples.append(round(ntx / max(sp, 1), 1))
            slot_times.append(round(sp / max(ns, 1), 4) if ns else 0)
    
    report = {
        "report_metadata": {
            "generated_at": now.isoformat(),
            "generator": "SolanaPulse v1.0",
            "data_sources": ["Solana RPC", "DeFiLlama", "CoinGecko"],
            "auto_refresh_interval": "configurable (default: 5 min)"
        },
        "network_health": rpc.get("health", {}).get("result", "unknown"),
        "network_performance": {
            "current_slot": rpc.get("slot", {}).get("result", 0),
            "current_tps": tps_samples[0] if tps_samples else 0,
            "tps_samples": tps_samples,
            "avg_slot_time_seconds": round(sum(slot_times)/len(slot_times), 4) if slot_times else 0,
            "slot_time_samples": slot_times,
        },
        "epoch": {
            "current_epoch": epoch.get("epoch", 0),
            "slot_index": epoch.get("slotIndex", 0),
            "slots_in_epoch": epoch.get("slotsInEpoch", 0),
            "epoch_progress_pct": round(epoch.get("slotIndex", 0) / max(epoch.get("slotsInEpoch", 1), 1) * 100, 2),
        },
        "validators": {
            "active": len(va.get("current", [])),
            "delinquent": len(va.get("delinquent", [])),
            "total": len(va.get("current", [])) + len(va.get("delinquent", [])),
            "delinquency_rate_pct": round(len(va.get("delinquent", [])) / max(len(va.get("current", [])) + len(va.get("delinquent", [])), 1) * 100, 2),
        },
        "supply": {
            "total_supply_sol": round(supply.get("total", 0) / 1e9, 2),
            "circulating_sol": round(supply.get("circulating", 0) / 1e9, 2),
            "non_circulating_sol": round(supply.get("nonCirculating", 0) / 1e9, 2),
        },
        "economic_indicators": {
            "sol_price_usd": cg.get("price_usd", 0),
            "sol_price_change_24h_pct": cg.get("price_change_24h", 0),
            "sol_price_change_7d_pct": cg.get("price_change_7d", 0),
            "market_cap_usd": cg.get("market_cap_usd", 0),
            "market_cap_rank": cg.get("market_cap_rank", 0),
            "volume_24h_usd": cg.get("volume_24h", 0),
            "ath_usd": cg.get("ath", 0),
            "ath_change_pct": cg.get("ath_change_pct", 0),
            "fdv_usd": cg.get("fdv", 0),
            "circulating_supply_sol": cg.get("circulating_supply", 0),
            "total_supply_sol": cg.get("total_supply", 0),
        },
        "defi_metrics": {
            "tvl_usd": defi.get("tvl", 0),
            "dex_volume_24h_usd": defi.get("dex_volume_24h", 0),
            "dex_volume_change_pct": defi.get("dex_volume_change", 0),
        },
        "anomaly_detection": anomalies,
        "anomaly_count": len(anomalies),
    }
    return report

def fmt_usd(v):
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:.2f}"

def fmt_sol(v):
    if v >= 1e6: return f"{v/1e6:.2f}M SOL"
    if v >= 1e3: return f"{v/1e3:.1f}K SOL"
    return f"{v:.2f} SOL"

def generate_markdown(report):
    lines = []
    lines.append(f"# Solana Ecosystem Report")
    lines.append(f"")
    lines.append(f"**Generated:** {report['report_metadata']['generated_at']}  ")
    lines.append(f"**Generator:** {report['report_metadata']['generator']}  ")
    lines.append(f"**Data Sources:** {', '.join(report['report_metadata']['data_sources'])}")
    lines.append(f"")
    
    if report.get("anomaly_count", 0) > 0:
        lines.append(f"> ⚠️ **{report['anomaly_count']} anomaly(ies) detected** — see below")
        lines.append(f"")
    
    lines.append("## Network Health")
    lines.append(f"- **Status:** {report['network_health']}")
    lines.append(f"- **Current Slot:** {report['network_performance']['current_slot']:,}")
    lines.append(f"- **Current TPS:** {report['network_performance']['current_tps']:.0f}")
    lines.append(f"- **Avg Slot Time:** {report['network_performance']['avg_slot_time_seconds']:.4f}s")
    lines.append(f"")
    
    lines.append("## Epoch Progress")
    e = report['epoch']
    lines.append(f"- **Current Epoch:** {e['current_epoch']}")
    lines.append(f"- **Progress:** {e['epoch_progress_pct']}% ({e['slot_index']:,}/{e['slots_in_epoch']:,} slots)")
    lines.append(f"")
    
    lines.append("## Validator Status")
    v = report['validators']
    lines.append(f"- **Active Validators:** {v['active']}")
    lines.append(f"- **Delinquent Validators:** {v['delinquent']}")
    lines.append(f"- **Total Validators:** {v['total']}")
    lines.append(f"- **Delinquency Rate:** {v['delinquency_rate_pct']}%")
    lines.append(f"")
    
    lines.append("## Supply")
    s = report['supply']
    lines.append(f"- **Total Supply:** {fmt_sol(s['total_supply_sol'])}")
    lines.append(f"- **Circulating:** {fmt_sol(s['circulating_sol'])}")
    lines.append(f"- **Non-Circulating:** {fmt_sol(s['non_circulating_sol'])}")
    lines.append(f"")
    
    lines.append("## Economic Indicators")
    ec = report['economic_indicators']
    lines.append(f"- **SOL Price:** ${ec['sol_price_usd']:.2f} ({ec['sol_price_change_24h_pct']:+.1f}% 24h, {ec['sol_price_change_7d_pct']:+.1f}% 7d)")
    lines.append(f"- **Market Cap:** {fmt_usd(ec['market_cap_usd'])} (Rank #{ec['market_cap_rank']})")
    lines.append(f"- **24h Volume:** {fmt_usd(ec['volume_24h_usd'])}")
    lines.append(f"- **ATH:** ${ec['ath_usd']:.2f} ({ec['ath_change_pct']:+.1f}% from ATH)")
    lines.append(f"- **FDV:** {fmt_usd(ec['fdv_usd'])}")
    lines.append(f"- **Circulating Supply:** {ec['circulating_supply_sol']:,.0f} SOL")
    lines.append(f"")
    
    lines.append("## DeFi Metrics")
    d = report['defi_metrics']
    lines.append(f"- **TVL:** {fmt_usd(d['tvl_usd'])}")
    lines.append(f"- **DEX Volume (24h):** {fmt_usd(d['dex_volume_24h_usd'])}")
    lines.append(f"- **DEX Volume Change:** {d['dex_volume_change_pct']:+.1f}%")
    lines.append(f"")
    
    if report.get("anomaly_detection"):
        lines.append("## ⚠️ Anomaly Detection")
        for a in report['anomaly_detection']:
            lines.append(f"- **[{a['level']}] {a['metric']}** — {a['value']}: {a['detail']}")
        lines.append(f"")
    
    lines.append("---")
    lines.append(f"*This report auto-updates. Last refresh: {report['report_metadata']['generated_at']}*")
    return "\n".join(lines)

def generate_html_dashboard(report):
    ec = report['economic_indicators']
    d = report['defi_metrics']
    v = report['validators']
    s = report['supply']
    np = report['network_performance']
    e = report['epoch']
    anomalies = report.get('anomaly_detection', [])
    ts = report['report_metadata']['generated_at']
    
    # Determine colors
    price_color = "#4ade80" if ec['sol_price_change_24h_pct'] >= 0 else "#f87171"
    delinq_color = "#4ade80" if v['delinquency_rate_pct'] < 3 else "#fbbf24" if v['delinquency_rate_pct'] < 5 else "#f87171"
    
    anomaly_html = ""
    if anomalies:
        anomaly_cards = ""
        for a in anomalies:
            color = "#f87171" if a['level'] == "HIGH" else "#fbbf24"
            anomaly_cards += f"""
            <div class="anomaly-card" style="border-color:{color}">
                <span class="anomaly-level" style="background:{color}">{a['level']}</span>
                <span class="anomaly-metric">{a['metric']}</span>
                <span class="anomaly-value">{a['value']}</span>
                <span class="anomaly-detail">{a['detail']}</span>
            </div>"""
        anomaly_html = f"""
        <div class="anomaly-section">
            <h2>⚠️ Anomaly Detection ({len(anomalies)})</h2>
            <div class="anomaly-grid">{anomaly_cards}</div>
        </div>"""

    tps_chart_data = json.dumps(np.get('tps_samples', [0]))
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SolanaPulse — Ecosystem Dashboard</title>
<meta http-equiv="refresh" content="300">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0a0f; color:#e0e0e0; font-family:'Segoe UI',system-ui,sans-serif; min-height:100vh; }}
.header {{ background:linear-gradient(135deg,#0d0d1a,#1a1a2e); padding:20px 30px; border-bottom:1px solid #2a2a4a; display:flex; justify-content:space-between; align-items:center; }}
.header h1 {{ font-size:24px; color:#9945FF; }}
.header .meta {{ font-size:12px; color:#888; }}
.header .pulse {{ display:inline-block; width:10px; height:10px; background:#4ade80; border-radius:50%; margin-right:8px; animation:pulse 2s infinite; }}
@keyframes pulse {{ 0%,100%{{opacity:1;}} 50%{{opacity:0.3;}} }}
.container {{ max-width:1400px; margin:0 auto; padding:20px; }}
.section-title {{ color:#9945FF; font-size:14px; text-transform:uppercase; letter-spacing:1px; margin:25px 0 10px; padding-bottom:5px; border-bottom:1px solid #2a2a4a; }}
.grid {{ display:grid; gap:15px; margin-bottom:10px; }}
.grid-4 {{ grid-template-columns:repeat(4,1fr); }}
.grid-3 {{ grid-template-columns:repeat(3,1fr); }}
.grid-2 {{ grid-template-columns:repeat(2,1fr); }}
.card {{ background:#12121f; border:1px solid #2a2a4a; border-radius:12px; padding:20px; transition:transform 0.2s,border-color 0.2s; }}
.card:hover {{ transform:translateY(-2px); border-color:#9945FF; }}
.card-label {{ font-size:11px; color:#888; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; }}
.card-value {{ font-size:28px; font-weight:700; color:#fff; }}
.card-sub {{ font-size:13px; margin-top:4px; }}
.green {{ color:#4ade80; }}
.red {{ color:#f87171; }}
.yellow {{ color:#fbbf24; }}
.purple {{ color:#9945FF; }}
.anomaly-section {{ margin-top:25px; }}
.anomaly-section h2 {{ color:#fbbf24; font-size:16px; margin-bottom:12px; }}
.anomaly-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }}
.anomaly-card {{ background:#1a1a2e; border:1px solid #2a2a4a; border-left:4px solid; border-radius:8px; padding:15px; display:grid; grid-template-columns:auto 1fr auto; gap:8px; align-items:center; }}
.anomaly-level {{ font-size:10px; font-weight:700; padding:3px 8px; border-radius:4px; color:#000; }}
.anomaly-metric {{ font-weight:600; color:#fff; }}
.anomaly-value {{ font-size:18px; font-weight:700; color:#fbbf24; }}
.anomaly-detail {{ grid-column:1/-1; font-size:12px; color:#aaa; margin-top:4px; }}
.progress-bar {{ height:8px; background:#2a2a4a; border-radius:4px; margin-top:10px; overflow:hidden; }}
.progress-fill {{ height:100%; background:linear-gradient(90deg,#9945FF,#14F195); border-radius:4px; }}
.chart-container {{ background:#12121f; border:1px solid #2a2a4a; border-radius:12px; padding:20px; margin-top:15px; }}
.chart-title {{ font-size:14px; color:#888; margin-bottom:10px; }}
.bar-chart {{ display:flex; align-items:flex-end; gap:8px; height:120px; padding-top:10px; }}
.bar {{ flex:1; background:linear-gradient(180deg,#14F195,#9945FF); border-radius:4px 4px 0 0; position:relative; min-height:4px; }}
.bar-label {{ position:absolute; bottom:-20px; left:0; right:0; text-align:center; font-size:10px; color:#888; }}
.bar-value {{ position:absolute; top:-18px; left:0; right:0; text-align:center; font-size:10px; color:#ccc; }}
.footer {{ text-align:center; padding:30px; color:#555; font-size:12px; }}
.sources {{ display:flex; gap:15px; justify-content:center; margin-top:10px; }}
.source-badge {{ background:#1a1a2e; padding:4px 12px; border-radius:20px; font-size:11px; color:#888; border:1px solid #2a2a4a; }}
@media(max-width:768px) {{ .grid-4,.grid-3 {{ grid-template-columns:repeat(2,1fr); }} .grid-2 {{ grid-template-columns:1fr; }} .anomaly-grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="header">
    <div><span class="pulse"></span><h1 style="display:inline">SolanaPulse</h1></div>
    <div class="meta">Auto-updating · Last refresh: {ts}<br>Next refresh in 5 min</div>
</div>
<div class="container">
    <div class="section-title">Economic Indicators</div>
    <div class="grid grid-4">
        <div class="card">
            <div class="card-label">SOL Price</div>
            <div class="card-value">${ec['sol_price_usd']:.2f}</div>
            <div class="card-sub {price_color}">{ec['sol_price_change_24h_pct']:+.1f}% (24h) · {ec['sol_price_change_7d_pct']:+.1f}% (7d)</div>
        </div>
        <div class="card">
            <div class="card-label">Market Cap</div>
            <div class="card-value">{fmt_usd(ec['market_cap_usd'])}</div>
            <div class="card-sub purple">Rank #{ec['market_cap_rank']}</div>
        </div>
        <div class="card">
            <div class="card-label">24h Volume</div>
            <div class="card-value">{fmt_usd(ec['volume_24h_usd'])}</div>
            <div class="card-sub green">FDV: {fmt_usd(ec['fdv_usd'])}</div>
        </div>
        <div class="card">
            <div class="card-label">ATH</div>
            <div class="card-value">${ec['ath_usd']:.2f}</div>
            <div class="card-sub red">{ec['ath_change_pct']:+.1f}% from ATH</div>
        </div>
    </div>

    <div class="section-title">Network Performance</div>
    <div class="grid grid-4">
        <div class="card">
            <div class="card-label">Current Slot</div>
            <div class="card-value">{np['current_slot']:,}</div>
            <div class="card-sub green">{report['network_health']}</div>
        </div>
        <div class="card">
            <div class="card-label">Current TPS</div>
            <div class="card-value">{np['current_tps']:.0f}</div>
            <div class="card-sub">transactions/sec</div>
        </div>
        <div class="card">
            <div class="card-label">Avg Slot Time</div>
            <div class="card-value">{np['avg_slot_time_seconds']:.4f}s</div>
            <div class="card-sub">target: ~0.4s</div>
        </div>
        <div class="card">
            <div class="card-label">Epoch</div>
            <div class="card-value">#{e['current_epoch']}</div>
            <div class="card-sub">{e['epoch_progress_pct']}% complete</div>
            <div class="progress-bar"><div class="progress-fill" style="width:{e['epoch_progress_pct']}%"></div></div>
        </div>
    </div>

    <div class="chart-container">
        <div class="chart-title">TPS Samples (Recent 5 periods)</div>
        <div class="bar-chart" id="tps-chart"></div>
    </div>

    <div class="section-title">Validators & Supply</div>
    <div class="grid grid-4">
        <div class="card">
            <div class="card-label">Active Validators</div>
            <div class="card-value green">{v['active']:,}</div>
        </div>
        <div class="card">
            <div class="card-label">Delinquent</div>
            <div class="card-value" style="color:{delinq_color}">{v['delinquent']:,}</div>
            <div class="card-sub" style="color:{delinq_color}">{v['delinquency_rate_pct']}% rate</div>
        </div>
        <div class="card">
            <div class="card-label">Total Supply</div>
            <div class="card-value">{fmt_sol(s['total_supply_sol'])}</div>
        </div>
        <div class="card">
            <div class="card-label">Circulating</div>
            <div class="card-value">{fmt_sol(s['circulating_sol'])}</div>
        </div>
    </div>

    <div class="section-title">DeFi Metrics</div>
    <div class="grid grid-2">
        <div class="card">
            <div class="card-label">Total Value Locked</div>
            <div class="card-value purple">{fmt_usd(d['tvl_usd'])}</div>
        </div>
        <div class="card">
            <div class="card-label">DEX Volume (24h)</div>
            <div class="card-value">{fmt_usd(d['dex_volume_24h_usd'])}</div>
            <div class="card-sub {'green' if d['dex_volume_change_pct']>=0 else 'red'}">{d['dex_volume_change_pct']:+.1f}% change</div>
        </div>
    </div>

    {anomaly_html}

    <div class="footer">
        <p>SolanaPulse v1.0 — Auto-updating Solana Ecosystem Report</p>
        <div class="sources">
            <span class="source-badge">Solana RPC</span>
            <span class="source-badge">DeFiLlama</span>
            <span class="source-badge">CoinGecko</span>
        </div>
        <p style="margin-top:8px">No API keys required · Python stdlib only · Auto-refreshes every 5 minutes</p>
    </div>
</div>
<script>
// Render TPS bar chart
var tpsData = {tps_chart_data};
var maxTps = Math.max.apply(Math, tpsData.concat([1]));
var chart = document.getElementById('tps-chart');
tpsData.forEach(function(val, i) {{
    var bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.height = (val / maxTps * 100) + '%';
    bar.innerHTML = '<span class="bar-value">' + Math.round(val) + '</span><span class="bar-label">T-' + i + '</span>';
    chart.appendChild(bar);
}});
// Countdown to next refresh
var seconds = 300;
setInterval(function() {{
    seconds--;
    if (seconds <= 0) seconds = 300;
}}, 1000);
</script>
</body>
</html>"""
    return html

def main():
    print("[SolanaPulse] Collecting data...")
    rpc = collect_rpc_data()
    defi = collect_defillama()
    cg = collect_coingecko()
    
    print("[SolanaPulse] Detecting anomalies...")
    anomalies = detect_anomalies(rpc, defi, cg)
    
    print("[SolanaPulse] Generating reports...")
    report = generate_json_report(rpc, defi, cg, anomalies)
    
    md = generate_markdown(report)
    html_out = generate_html_dashboard(report)
    
    # Write outputs
    with open(os.path.join(OUTPUT_DIR, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(OUTPUT_DIR, "report.md"), "w") as f:
        f.write(md)
    with open(os.path.join(OUTPUT_DIR, "dashboard.html"), "w") as f:
        f.write(html_out)
    
    print(f"[SolanaPulse] Done! Generated dashboard.html, report.md, report.json")
    print(f"[SolanaPulse] Anomalies detected: {len(anomalies)}")
    return report

if __name__ == "__main__":
    main()
