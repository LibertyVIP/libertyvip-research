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
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
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


class CandleFetchError(RuntimeError):
    pass


def request_json(url: str) -> object:
    req = Request(url, headers={"User-Agent": "LibertyVIP Research/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_request_json(url: str) -> object | None:
    try:
        return request_json(url)
    except Exception:
        return None


def normalize_candles(candles: list[dict[str, float]]) -> list[dict[str, float]]:
    return sorted(candles, key=lambda c: c["time"])


def bybit_interval(interval: str) -> str:
    return {
        "15m": "15",
        "1h": "60",
        "4h": "240",
        "1d": "D",
    }.get(interval, interval)


def okx_interval(interval: str) -> str:
    return {
        "15m": "15m",
        "1h": "1H",
        "4h": "4H",
        "1d": "1D",
    }.get(interval, interval)


def okx_inst_ids(symbol: str) -> list[str]:
    base = symbol.removesuffix("USDT")
    return [f"{base}-USDT-SWAP", f"{base}-USDT"]


def fetch_bybit_klines(symbol: str, interval: str, limit: int = 220) -> list[dict[str, float]]:
    params_base = {"symbol": symbol, "interval": bybit_interval(interval), "limit": min(limit, 1000)}
    errors: list[str] = []
    for category in ("linear", "spot"):
        params = urlencode({"category": category, **params_base})
        raw = request_json(f"{BYBIT}/v5/market/kline?{params}")
        if not isinstance(raw, dict):
            errors.append(f"{category}: unexpected response")
            continue
        if raw.get("retCode") != 0:
            errors.append(f"{category}: {raw.get('retMsg')}")
            continue
        result = raw.get("result")
        rows = result.get("list") if isinstance(result, dict) else None
        if not isinstance(rows, list) or not rows:
            errors.append(f"{category}: empty")
            continue
        candles = []
        for item in rows:
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
        return normalize_candles(candles)
    raise CandleFetchError("; ".join(errors) or "Bybit returned no candles")


def fetch_okx_klines(symbol: str, interval: str, limit: int = 220) -> list[dict[str, float]]:
    errors: list[str] = []
    for inst_id in okx_inst_ids(symbol):
        params = urlencode({"instId": inst_id, "bar": okx_interval(interval), "limit": min(limit, 300)})
        raw = request_json(f"{OKX}/api/v5/market/candles?{params}")
        if not isinstance(raw, dict):
            errors.append(f"{inst_id}: unexpected response")
            continue
        if str(raw.get("code")) != "0":
            errors.append(f"{inst_id}: {raw.get('msg')}")
            continue
        rows = raw.get("data")
        if not isinstance(rows, list) or not rows:
            errors.append(f"{inst_id}: empty")
            continue
        candles = []
        for item in rows:
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
        return normalize_candles(candles)
    raise CandleFetchError("; ".join(errors) or "OKX returned no candles")


def fetch_binance_klines(symbol: str, interval: str, limit: int = 220) -> list[dict[str, float]]:
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
                return normalize_candles(candles)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise CandleFetchError(f"Binance returned no candles: {last_error}")


def fetch_klines(symbol: str, interval: str, limit: int = 220) -> tuple[list[dict[str, float]], str]:
    errors: list[str] = []
    sources = [
        ("Bybit", fetch_bybit_klines),
        ("OKX", fetch_okx_klines),
        ("Binance", fetch_binance_klines),
    ]
    for source_name, fetcher in sources:
        try:
            candles = fetcher(symbol, interval, limit)
            if candles:
                return candles, source_name
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_name}: {exc}")
    raise CandleFetchError(f"Unable to fetch {symbol} {interval}. " + " | ".join(errors))


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * k + current * (1 - k)
    return current


def sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    window = values[-period:] if len(values) >= period else values
    return statistics.mean(window)


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    if len(values) <= period:
        return [None] * len(values)

    rsis: list[float | None] = [None] * period
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = statistics.mean(gains)
    avg_loss = statistics.mean(losses)
    rsis.append(100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss)))

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        rsis.append(100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss)))

    return rsis


def detect_rsi_confirmation(closes: list[float]) -> dict[str, object]:
    rsis = rsi_series(closes, 14)
    valid = [value for value in rsis if value is not None]
    if len(valid) < 23:
        return {
            "rsi": None,
            "rsiSlow": None,
            "rsiCross": "n/a",
            "zookeeper": "n/a",
            "zookeeperDirection": "neutral",
        }

    slow_series: list[float | None] = []
    for i, value in enumerate(rsis):
        if value is None:
            slow_series.append(None)
            continue
        valid_window = [v for v in rsis[: i + 1] if v is not None]
        slow_series.append(sma(valid_window, 21) if len(valid_window) >= 21 else None)

    last_rsi = rsis[-1]
    prev_rsi = rsis[-2]
    last_slow = slow_series[-1]
    prev_slow = slow_series[-2]
    if None in (last_rsi, prev_rsi, last_slow, prev_slow):
        cross = "n/a"
    elif prev_rsi <= prev_slow and last_rsi > last_slow:
        cross = "bullish cross"
    elif prev_rsi >= prev_slow and last_rsi < last_slow:
        cross = "bearish cross"
    else:
        cross = "no cross"

    recent_rsis = [v for v in rsis[-8:] if v is not None]
    recent_low = min(recent_rsis) if recent_rsis else float(last_rsi or 50)
    recent_high = max(recent_rsis) if recent_rsis else float(last_rsi or 50)
    zookeeper = "waiting"
    direction = "neutral"
    if cross == "bullish cross" and recent_low <= 35:
        zookeeper = "bullish impulse setup"
        direction = "bullish"
    elif cross == "bearish cross" and recent_high >= 65:
        zookeeper = "bearish impulse setup"
        direction = "bearish"
    elif recent_low <= 30:
        zookeeper = "oversold armed"
        direction = "bullish"
    elif recent_high >= 70:
        zookeeper = "overbought armed"
        direction = "bearish"

    return {
        "rsi": round(float(last_rsi), 2),
        "rsiSlow": round(float(last_slow), 2),
        "rsiCross": cross,
        "zookeeper": zookeeper,
        "zookeeperDirection": direction,
    }


def detect_mss(candles: list[dict[str, float]], pivot: int = 3, lookback: int = 90) -> dict[str, object]:
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    pivot_highs: list[tuple[int, float]] = []
    pivot_lows: list[tuple[int, float]] = []

    start = max(pivot, len(candles) - lookback)
    for i in range(start, len(candles) - pivot):
        high_window = highs[i - pivot : i + pivot + 1]
        low_window = lows[i - pivot : i + pivot + 1]
        if highs[i] == max(high_window):
            pivot_highs.append((i, highs[i]))
        if lows[i] == min(low_window):
            pivot_lows.append((i, lows[i]))

    events: list[tuple[int, str, float]] = []
    for i in range(1, len(candles)):
        previous_highs = [p for p in pivot_highs if p[0] < i]
        previous_lows = [p for p in pivot_lows if p[0] < i]
        if previous_highs:
            swing_index, swing_high = previous_highs[-1]
            if closes[i - 1] <= swing_high and closes[i] > swing_high:
                events.append((i, "bullish MSS", swing_high))
        if previous_lows:
            swing_index, swing_low = previous_lows[-1]
            if closes[i - 1] >= swing_low and closes[i] < swing_low:
                events.append((i, "bearish MSS", swing_low))

    if not events:
        return {"mss": "none", "mssDirection": "neutral", "mssAgeBars": None, "mssLevel": None}

    index, kind, level = events[-1]
    return {
        "mss": kind,
        "mssDirection": "bullish" if kind.startswith("bullish") else "bearish",
        "mssAgeBars": len(candles) - 1 - index,
        "mssLevel": level,
    }


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
    rsi_confirmation = detect_rsi_confirmation(closes)
    mss_confirmation = detect_mss(candles)

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
    if rsi_confirmation["zookeeperDirection"] == bias:
        score += 10
    elif rsi_confirmation["zookeeperDirection"] not in ("neutral", bias):
        score -= 6
    if rsi_confirmation["rsiCross"].startswith(str(bias)):
        score += 6
    if mss_confirmation["mssDirection"] == bias:
        age = mss_confirmation.get("mssAgeBars")
        score += 8 if isinstance(age, int) and age <= 20 else 4
    elif mss_confirmation["mssDirection"] not in ("neutral", bias):
        score -= 6
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
        **rsi_confirmation,
        **mss_confirmation,
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


def fmt_number(value: object, decimals: int = 2) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value):.{decimals}f}"


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


def load_previous_reports() -> dict[tuple[str, str], dict[str, object]]:
    latest_path = CRYPTO_DIR / "latest.json"
    if not latest_path.exists():
        return {}
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    reports = payload.get("reports") if isinstance(payload, dict) else None
    if not isinstance(reports, list):
        return {}
    previous: dict[tuple[str, str], dict[str, object]] = {}
    for report in reports:
        if not isinstance(report, dict):
            continue
        symbol = str(report.get("symbol", "")).upper()
        timeframe = str(report.get("timeframe", ""))
        if symbol and timeframe:
            previous[(symbol, timeframe)] = report
    return previous


def unavailable_report(symbol: str, timeframe: str, error: str, defillama: dict[str, object]) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": 0,
        "ema20": 0,
        "ema50": 0,
        "ema200": 0,
        "change": 0,
        "volumeRatio": 0,
        "recentHigh": 0,
        "recentLow": 0,
        "distanceHigh": 0,
        "distanceLow": 0,
        "bias": "neutral",
        "setup": "data unavailable",
        "score": 0,
        "lastCandleTime": int(datetime.now(timezone.utc).timestamp() * 1000),
        "rsi": None,
        "rsiSlow": None,
        "rsiCross": "n/a",
        "zookeeper": "n/a",
        "zookeeperDirection": "neutral",
        "mss": "n/a",
        "mssDirection": "neutral",
        "mssAgeBars": None,
        "mssLevel": None,
        "defillama": defillama,
        "dataStatus": "unavailable",
        "dataSource": "none",
        "dataError": error[:180],
    }


def build_reports(config: dict[str, object]) -> list[dict[str, object]]:
    symbols = [str(s).upper() for s in config["symbols"]]  # type: ignore[index]
    timeframes = [str(t) for t in config["timeframes"]]  # type: ignore[index]
    defillama = fetch_defillama_context(symbols)
    previous_reports = load_previous_reports()
    reports = []
    for symbol in symbols:
        for timeframe in timeframes:
            llama_context = defillama.get(symbol, {})
            try:
                candles, data_source = fetch_klines(symbol, timeframe)
                structure = detect_structure(candles)
                reports.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        **structure,
                        "defillama": llama_context,
                        "dataStatus": "fresh",
                        "dataSource": data_source,
                        "dataError": "",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                previous = previous_reports.get((symbol, timeframe))
                if previous:
                    stale = dict(previous)
                    stale["defillama"] = llama_context or stale.get("defillama", {})
                    stale["dataStatus"] = "stale"
                    stale["dataSource"] = f"stale/{stale.get('dataSource', 'previous')}"
                    stale["dataError"] = str(exc)[:180]
                    reports.append(stale)
                    print(f"warning: using stale data for {symbol} {timeframe}: {exc}")
                else:
                    reports.append(unavailable_report(symbol, timeframe, str(exc), llama_context))
                    print(f"warning: unavailable data for {symbol} {timeframe}: {exc}")
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
        data_status = str(r.get("dataStatus", "fresh"))
        data_source = str(r.get("dataSource", "n/a"))
        data_error = str(r.get("dataError", ""))
        status_note = ""
        if data_status == "stale":
            status_note = f'<p class="status stale">Donnée temporairement reprise de la dernière mise à jour. Erreur API : {escape(data_error)}</p>'
        elif data_status == "unavailable":
            status_note = f'<p class="status unavailable">Donnée indisponible pour cette paire/timeframe. Erreur API : {escape(data_error)}</p>'
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
          <div><span>RSI / slow</span><strong>{fmt_number(r.get("rsi"))} / {fmt_number(r.get("rsiSlow"))}</strong></div>
          <div><span>RSI cross</span><strong>{escape(str(r.get("rsiCross", "n/a")))}</strong></div>
          <div><span>Zookeeper</span><strong>{escape(str(r.get("zookeeper", "n/a")))}</strong></div>
          <div><span>MSS</span><strong>{escape(str(r.get("mss", "none")))} <small>{str(r.get("mssAgeBars")) + " bars" if isinstance(r.get("mssAgeBars"), int) else ""}</small></strong></div>
          <div><span>Data source</span><strong>{escape(data_source)}</strong></div>
          <div><span>Vol ratio</span><strong>{float(r["volumeRatio"]):.2f}x</strong></div>
          <div><span>Near high/low</span><strong>{float(r["distanceHigh"]):+.2f}% / {float(r["distanceLow"]):+.2f}%</strong></div>
          <div><span>DeFiLlama chain</span><strong>{escape(chain)}</strong></div>
          <div><span>Chain TVL</span><strong>{fmt_money(llama.get("chainTvl"))}</strong></div>
          <div><span>Stablecoins</span><strong>{fmt_money(llama.get("stablecoins"))}</strong></div>
          <div><span>DEX 24h</span><strong>{fmt_money(llama.get("dexVolume24h"))} <small>{fmt_pct(llama.get("dexChange1d"))}</small></strong></div>
          <div><span>Fees 24h</span><strong>{fmt_money(llama.get("fees24h"))} <small>{fmt_pct(llama.get("feesChange1d"))}</small></strong></div>
        </section>
        {status_note}
        <p class="summary">Lecture : contexte {escape(bias)} sur {escape(timeframe)}. Le RSI/Zookeeper et le MSS servent de confirmations de momentum/structure; DeFiLlama ajoute le contexte fondamental on-chain quand disponible. Ceci est une watchlist, pas un signal automatique.</p>
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
        primary = frames[0] if str(name).lower() == "swing" else frames[0]
        if str(name).lower() == "intraday" and "4h" in frames:
            primary = "4h"
        if str(name).lower() == "active" and "1h" in frames:
            primary = "1h"
        mode_buttons.append(
            f'<button class="mode-button" data-frames="{escape(frames_text)}" data-primary="{escape(str(primary))}">{escape(str(name).title())}</button>'
        )
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
    .toolbar .hint {{ color:#7082a8; font-size:13px; }}
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
    .status {{ border:1px solid #39476b; border-radius:12px; padding:10px 12px; margin:12px 0; color:#d9e4fb; font-size:13px; line-height:1.4; }}
    .status.stale {{ background:rgba(245,158,11,.12); border-color:rgba(245,158,11,.35); }}
    .status.unavailable {{ background:rgba(239,68,68,.12); border-color:rgba(239,68,68,.35); }}
    .note {{ color:#8494b6; margin-top:24px; font-size:13px; }}
  </style>
</head>
<body>
  <h1>LibertyVIP Crypto Watchlist</h1>
  <p class="sub">Généré le {escape(generated)}. Watchlist gratuite basée sur des chandelles publiques multi-sources (Bybit, OKX, Binance en backup) + données gratuites DeFiLlama : TVL, stablecoins, volumes DEX et fees/revenue quand disponibles. Pour éviter les doublons, le dashboard affiche une seule carte par actif selon la timeframe sélectionnée.</p>
  <nav class="toolbar"><span>Mode</span>{''.join(mode_buttons)}<span class="hint">Swing ouvre 1D · Intraday ouvre 4H · Active ouvre 1H</span></nav>
  <nav class="toolbar"><span>Timeframe</span>{''.join(timeframe_buttons)}</nav>
  <main class="cards">{render_cards(reports)}</main>
  <p class="note">Éducatif seulement. Pas un conseil financier personnalisé. Les cryptos sont volatiles : confirmer le contexte, le risque et le plan avant toute entrée.</p>
  <script>
    const defaultMode = {json.dumps(config.get("defaultMode", "intraday"))};
    const modeButtons = [...document.querySelectorAll(".mode-button")];
    const tfButtons = [...document.querySelectorAll(".tf-button")];
    const cards = [...document.querySelectorAll(".card")];
    let activeTf = "";
    function refresh() {{
      cards.forEach(card => {{
        const tf = card.dataset.timeframe;
        card.classList.toggle("hidden", tf !== activeTf);
      }});
    }}
    function selectMode(button) {{
      modeButtons.forEach(b => b.classList.toggle("active", b === button));
      const primary = button.dataset.primary;
      const tfButton = tfButtons.find(b => b.dataset.timeframe === primary) || tfButtons[0];
      localStorage.setItem("liberty-crypto-mode", button.textContent.toLowerCase());
      selectTf(tfButton, false);
    }}
    function selectTf(button, clearMode = true) {{
      tfButtons.forEach(b => b.classList.toggle("active", b === button));
      if (clearMode) modeButtons.forEach(b => b.classList.remove("active"));
      activeTf = button.dataset.timeframe;
      localStorage.setItem("liberty-crypto-timeframe", activeTf);
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
