from __future__ import annotations

import json
import math
import statistics
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CRYPTO_DIR = ROOT / "crypto"
ASSETS_PATH = CRYPTO_DIR / "assets.json"
BINANCE_FUTURES = "https://fapi.binance.com"
BINANCE_SPOT = "https://api.binance.com"
DEFILLAMA = "https://api.llama.fi"
DEFILLAMA_STABLES = "https://stablecoins.llama.fi"

DEFILLAMA_CHAIN_BY_SYMBOL = {
    "BTCUSDT": "Bitcoin",
    "ETHUSDT": "Ethereum",
    "SOLUSDT": "Solana",
    "SUIUSDT": "Sui",
    "XRPUSDT": "XRPL",
    "BNBUSDT": "BSC",
    "ZECUSDT": None,
}


def request_json(url: str) -> object:
    req = Request(url, headers={"User-Agent": "LibertyVIP Research/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_request_json(url: str) -> object | None:
    try:
        return request_json(url)
    except Exception:
        return None


def fetch_klines(symbol: str, interval: str, limit: int = 220) -> list[dict[str, float]]:
    params = urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    urls = [
        f"{BINANCE_FUTURES}/fapi/v1/klines?{params}",
        f"{BINANCE_SPOT}/api/v3/klines?{params}",
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            raw = request_json(url)
            candles = []
            for item in raw:  # type: ignore[union-attr]
                candles.append(
                    {
                        "time": float(item[0]),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    }
                )
            if candles:
                return candles
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"Unable to fetch {symbol} {interval}: {last_error}")


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * k + current * (1 - k)
    return current


def pct(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100


def detect_structure(candles: list[dict[str, float]]) -> dict[str, object]:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]
    last = candles[-1]
    last_close = last["close"]
    ema20 = ema(closes[-80:], 20)
    ema50 = ema(closes[-120:], 50)
    ema200 = ema(closes, 200)
    change = pct(last_close, closes[-25]) if len(closes) > 25 else 0
    vol_avg = statistics.mean(volumes[-30:]) if len(volumes) >= 30 else statistics.mean(volumes)
    vol_ratio = last["volume"] / vol_avg if vol_avg else 0
    recent_high = max(highs[-30:])
    recent_low = min(lows[-30:])
    distance_high = pct(last_close, recent_high)
    distance_low = pct(last_close, recent_low)

    bullish_score = 0
    bearish_score = 0
    if last_close > ema20:
        bullish_score += 1
    else:
        bearish_score += 1
    if ema20 > ema50:
        bullish_score += 1
    else:
        bearish_score += 1
    if ema50 > ema200:
        bullish_score += 1
    else:
        bearish_score += 1
    if change > 0:
        bullish_score += 1
    elif change < 0:
        bearish_score += 1

    if bullish_score >= 3:
        bias = "bullish"
    elif bearish_score >= 3:
        bias = "bearish"
    else:
        bias = "neutral"

    compression = abs(distance_high) < 4 or abs(distance_low) < 4
    if bias == "bullish" and compression and last_close > ema20:
        setup = "bullish consolidation"
    elif bias == "bearish" and compression and last_close < ema20:
        setup = "bearish consolidation"
    elif bias == "bullish":
        setup = "uptrend"
    elif bias == "bearish":
        setup = "downtrend"
    else:
        setup = "range"

    score = 50
    score += (bullish_score - bearish_score) * 8
    if vol_ratio >= 1.25:
        score += 6
    if compression:
        score += 6
    score = max(0, min(100, score))

    return {
        "price": last_close,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "change": change,
        "volumeRatio": vol_ratio,
        "recentHigh": recent_high,
        "recentLow": recent_low,
        "distanceHigh": distance_high,
        "distanceLow": distance_low,
        "bias": bias,
        "setup": setup,
        "score": round(score),
        "lastCandleTime": int(last["time"]),
    }


def grade(score: int) -> str:
    if score >= 75:
        return "WATCH"
    if score >= 60:
        return "BUILDING"
    return "WAIT"


def fmt_price(value: float) -> str:
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:,.6f}"


def fmt_money(value: object) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "n/a"
    value = float(value)
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.2f}"


def fmt_pct(value: object) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value):+.2f}%"


def numeric(value: object) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def load_config() -> dict[str, object]:
    if ASSETS_PATH.exists():
        return json.loads(ASSETS_PATH.read_text(encoding="utf-8"))
    return {
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "SUIUSDT", "XRPUSDT", "BNBUSDT"],
        "timeframes": ["15m", "1h", "4h", "1d"],
        "defaultMode": "intraday",
        "modes": {"swing": ["1d", "4h", "1h"], "intraday": ["4h", "1h", "15m"], "active": ["1h", "15m"]},
    }


def fetch_defillama_context(symbols: list[str]) -> dict[str, dict[str, object]]:
    chains_raw = safe_request_json(f"{DEFILLAMA}/v2/chains")
    stables_raw = safe_request_json(f"{DEFILLAMA_STABLES}/stablecoinchains")
    chains = {item.get("name"): item for item in chains_raw} if isinstance(chains_raw, list) else {}
    stables = {item.get("name"): item for item in stables_raw} if isinstance(stables_raw, list) else {}
    unique_chains = sorted(
        {DEFILLAMA_CHAIN_BY_SYMBOL.get(symbol) for symbol in symbols if DEFILLAMA_CHAIN_BY_SYMBOL.get(symbol)}
    )
    dex_by_chain: dict[str, dict[str, object]] = {}
    fees_by_chain: dict[str, dict[str, object]] = {}
    for chain in unique_chains:
        dex = safe_request_json(f"{DEFILLAMA}/overview/dexs/{chain}")
        fees = safe_request_json(f"{DEFILLAMA}/overview/fees/{chain}")
        if isinstance(dex, dict):
            dex_by_chain[chain] = dex
        if isinstance(fees, dict):
            fees_by_chain[chain] = fees
        time.sleep(0.15)

    context: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        chain = DEFILLAMA_CHAIN_BY_SYMBOL.get(symbol)
        if not chain:
            context[symbol] = {
                "available": False,
                "chain": None,
                "note": "Pas de chain DeFiLlama pertinente pour cet actif.",
            }
            continue
        chain_data = chains.get(chain, {})
        stable_data = stables.get(chain, {})
        stable_total = None
        if isinstance(stable_data, dict):
            total = stable_data.get("totalCirculatingUSD")
            if isinstance(total, dict):
                stable_total = sum(float(v) for v in total.values() if isinstance(v, (int, float)))
        dex = dex_by_chain.get(chain, {})
        fees = fees_by_chain.get(chain, {})
        context[symbol] = {
            "available": True,
            "chain": chain,
            "chainTvl": numeric(chain_data.get("tvl") if isinstance(chain_data, dict) else None),
            "stablecoins": stable_total,
            "dexVolume24h": numeric(dex.get("total24h")),
            "dexChange1d": numeric(dex.get("change_1d")),
            "dexChange7d": numeric(dex.get("change_7d")),
            "fees24h": numeric(fees.get("total24h")),
            "feesChange1d": numeric(fees.get("change_1d")),
            "feesChange7d": numeric(fees.get("change_7d")),
        }
    return context


def build_reports(config: dict[str, object]) -> list[dict[str, object]]:
    symbols = [str(s).upper() for s in config["symbols"]]  # type: ignore[index]
    timeframes = [str(t) for t in config["timeframes"]]  # type: ignore[index]
    defillama = fetch_defillama_context(symbols)
    reports = []
    for symbol in symbols:
        for timeframe in timeframes:
            candles = fetch_klines(symbol, timeframe)
            structure = detect_structure(candles)
            reports.append({"symbol": symbol, "timeframe": timeframe, **structure, "defillama": defillama.get(symbol, {})})
            time.sleep(0.15)
    return reports


def render_cards(reports: list[dict[str, object]]) -> str:
    cards = []
    for r in reports:
        score = int(r["score"])
        bias = str(r["bias"])
        symbol = str(r["symbol"])
        timeframe = str(r["timeframe"])
        llama = r.get("defillama", {})
        if not isinstance(llama, dict):
            llama = {}
        chain = str(llama.get("chain") or "n/a")
        tradingview = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P"
        cards.append(
            f"""
      <article class="card {escape(bias)}" data-timeframe="{escape(timeframe)}" data-symbol="{escape(symbol)}">
        <header>
          <div>
            <h2><a href="{tradingview}" target="_blank" rel="noopener">{escape(symbol)}</a></h2>
            <p>{escape(timeframe)} · {datetime.fromtimestamp(int(r["lastCandleTime"]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
          </div>
          <div class="score">{score}<small>/100</small><span>{grade(score)}</span></div>
        </header>
        <section class="grid">
          <div><span>Price</span><strong>{fmt_price(float(r["price"]))}</strong></div>
          <div><span>Bias</span><strong>{escape(bias)}</strong></div>
          <div><span>Setup</span><strong>{escape(str(r["setup"]))}</strong></div>
          <div><span>Change</span><strong>{float(r["change"]):+.2f}%</strong></div>
          <div><span>Vol ratio</span><strong>{float(r["volumeRatio"]):.2f}x</strong></div>
          <div><span>Near high/low</span><strong>{float(r["distanceHigh"]):+.2f}% / {float(r["distanceLow"]):+.2f}%</strong></div>
          <div><span>DeFiLlama chain</span><strong>{escape(chain)}</strong></div>
          <div><span>Chain TVL</span><strong>{fmt_money(llama.get("chainTvl"))}</strong></div>
          <div><span>Stablecoins</span><strong>{fmt_money(llama.get("stablecoins"))}</strong></div>
          <div><span>DEX 24h</span><strong>{fmt_money(llama.get("dexVolume24h"))} <small>{fmt_pct(llama.get("dexChange1d"))}</small></strong></div>
          <div><span>Fees 24h</span><strong>{fmt_money(llama.get("fees24h"))} <small>{fmt_pct(llama.get("feesChange1d"))}</small></strong></div>
        </section>
        <p class="summary">Lecture : contexte {escape(bias)} sur {escape(timeframe)}. DeFiLlama ajoute le contexte TVL/stablecoins/DEX/fees quand disponible. Ceci est une watchlist, pas un signal automatique.</p>
      </article>
      """
        )
    return "\n".join(cards)


def render_html(config: dict[str, object], reports: list[dict[str, object]]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    modes = config.get("modes", {})
    mode_buttons = []
    for name, frames in modes.items():  # type: ignore[union-attr]
        frames_text = ",".join(frames)
        mode_buttons.append(f'<button class="mode-button" data-frames="{escape(frames_text)}">{escape(str(name).title())}</button>')
    timeframe_buttons = []
    for timeframe in config["timeframes"]:  # type: ignore[index]
        timeframe_buttons.append(f'<button class="tf-button" data-timeframe="{escape(str(timeframe))}">{escape(str(timeframe))}</button>')
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LibertyVIP Crypto Watchlist</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Segoe UI, Arial, sans-serif; background:#070a10; color:#eef3ff; }}
    body {{ margin:0; padding:28px; background:radial-gradient(circle at top left,#13213d,#070a10 42%); }}
    h1 {{ margin:0; font-size:34px; letter-spacing:-.04em; }}
    .sub {{ color:#93a4c7; line-height:1.45; max-width:1120px; }}
    a {{ color:#9cc5ff; text-decoration:none; }}
    .toolbar {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:18px 0 22px; }}
    .toolbar span {{ color:#93a4c7; font-size:14px; }}
    button {{ border:1px solid #2b3b63; background:rgba(255,255,255,.045); color:#dce7ff; border-radius:999px; padding:9px 14px; font-weight:800; cursor:pointer; }}
    button.active {{ background:linear-gradient(135deg,#145dd8,#22c18a); border-color:transparent; color:white; }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:18px; }}
    .card {{ border:1px solid #22304f; border-radius:22px; padding:20px; background:rgba(11,17,30,.84); box-shadow:0 20px 70px rgba(0,0,0,.28); }}
    .card.hidden {{ display:none; }}
    .card header {{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:18px; }}
    h2 {{ margin:0; font-size:26px; }}
    header p {{ margin:4px 0 0; color:#8798bd; }}
    .score {{ min-width:86px; height:86px; border-radius:20px; display:grid; place-items:center; font-size:30px; font-weight:900; background:#151d31; border:1px solid #2b3b63; }}
    .score small {{ font-size:13px; color:#9aa9c7; margin-left:2px; }}
    .score span {{ display:block; font-size:13px; color:#dce7ff; }}
    .bullish .score {{ background:linear-gradient(135deg,#0d6b4f,#23c18a); }}
    .bearish .score {{ background:linear-gradient(135deg,#7c2536,#d34d5f); }}
    .neutral .score {{ background:linear-gradient(135deg,#28334d,#53617f); }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
    .grid div {{ border:1px solid #22304f; border-radius:14px; padding:12px; background:rgba(255,255,255,.035); }}
    .grid span {{ display:block; color:#8494b6; font-size:12px; margin-bottom:6px; }}
    .grid strong {{ font-size:15px; }}
    .summary {{ color:#d5e0f8; line-height:1.45; }}
    .note {{ color:#8494b6; margin-top:24px; font-size:13px; }}
  </style>
</head>
<body>
  <h1>LibertyVIP Crypto Watchlist</h1>
  <p class="sub">Généré le {escape(generated)}. Watchlist gratuite basée sur les chandelles publiques Binance + données gratuites DeFiLlama : TVL, stablecoins, volumes DEX et fees/revenue quand disponibles. Modes suggérés : Swing = 1D/4H/1H, Intraday = 4H/1H/15m, Active = 1H/15m.</p>
  <nav class="toolbar"><span>Mode</span>{''.join(mode_buttons)}</nav>
  <nav class="toolbar"><span>Timeframe</span>{''.join(timeframe_buttons)}</nav>
  <main class="cards">{render_cards(reports)}</main>
  <p class="note">Éducatif seulement. Pas un conseil financier personnalisé. Les cryptos sont volatiles : confirmer le contexte, le risque et le plan avant toute entrée.</p>
  <script>
    const defaultMode = {json.dumps(config.get("defaultMode", "intraday"))};
    const modeButtons = [...document.querySelectorAll(".mode-button")];
    const tfButtons = [...document.querySelectorAll(".tf-button")];
    const cards = [...document.querySelectorAll(".card")];
    let activeFrames = new Set();
    let activeTf = "";
    function refresh() {{
      cards.forEach(card => {{
        const tf = card.dataset.timeframe;
        card.classList.toggle("hidden", activeTf ? tf !== activeTf : !activeFrames.has(tf));
      }});
    }}
    function selectMode(button) {{
      modeButtons.forEach(b => b.classList.toggle("active", b === button));
      tfButtons.forEach(b => b.classList.remove("active"));
      activeTf = "";
      activeFrames = new Set(button.dataset.frames.split(","));
      localStorage.setItem("liberty-crypto-mode", button.textContent.toLowerCase());
      refresh();
    }}
    function selectTf(button) {{
      tfButtons.forEach(b => b.classList.toggle("active", b === button));
      modeButtons.forEach(b => b.classList.remove("active"));
      activeTf = button.dataset.timeframe;
      refresh();
    }}
    modeButtons.forEach(button => button.addEventListener("click", () => selectMode(button)));
    tfButtons.forEach(button => button.addEventListener("click", () => selectTf(button)));
    const saved = localStorage.getItem("liberty-crypto-mode") || defaultMode;
    selectMode(modeButtons.find(b => b.textContent.toLowerCase() === saved) || modeButtons[0]);
  </script>
</body>
</html>"""


def main() -> None:
    CRYPTO_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    reports = build_reports(config)
    (CRYPTO_DIR / "index.html").write_text(render_html(config, reports), encoding="utf-8")
    (CRYPTO_DIR / "latest.json").write_text(json.dumps({"generatedAt": datetime.now(timezone.utc).isoformat(), "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reports={len(reports)}")
    print(CRYPTO_DIR / "index.html")


if __name__ == "__main__":
    main()
