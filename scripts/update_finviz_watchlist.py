from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
STOCKS_DIR = ROOT / "stocks"
BASE = "https://finviz.com"
FILTERS = "fa_epsqoq_pos,fa_pe_o30,fa_profitmargin_pos,fa_quickratio_o1,fa_roa_o10,fa_roe_o10,fa_roi_o10,fa_salesqoq_pos,sh_avgvol_o500,sh_short_u5"
SCREENER_URL = f"{BASE}/screener.ashx?v=111&f={FILTERS}&o=-marketcap"


def fetch(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_rows(page_html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row_match in re.finditer(r"<tr class=\"styled-row.*?</tr>", page_html, re.S):
        row = row_match.group(0)
        cells = [clean(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 11 and cells[0].isdigit():
            rows.append(
                {
                    "rank": cells[0],
                    "ticker": cells[1],
                    "company": cells[2],
                    "sector": cells[3],
                    "industry": cells[4],
                    "country": cells[5],
                    "market_cap": cells[6],
                    "pe": cells[7],
                    "price": cells[8],
                    "change": cells[9],
                    "volume": cells[10],
                }
            )
    return rows


def get_rows() -> list[dict[str, str]]:
    all_rows: list[dict[str, str]] = []
    for index in range(5):
        start = 1 + index * 20
        url = SCREENER_URL if start == 1 else f"{SCREENER_URL}&r={start}"
        rows = parse_rows(fetch(url))
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 20:
            break
        time.sleep(0.5)
    return all_rows


def render_cards(rows: list[dict[str, str]]) -> str:
    cards: list[str] = []
    for row in rows:
        ticker = row["ticker"]
        quote_url = f"https://finviz.com/quote.ashx?t={ticker}&p=d"
        chart_url = f"https://charts2-node.finviz.com/chart.ashx?cs=l&t={ticker}&tf=d&s=linear&ct=candle_stick"
        cards.append(
            f"""
        <article class="card">
          <header>
            <div>
              <h2><a href="{quote_url}" target="_blank" rel="noopener">{html.escape(ticker)}</a></h2>
              <p>{html.escape(row["company"])} · {html.escape(row["sector"])} · {html.escape(row["industry"])}</p>
            </div>
            <div class="rank">#{html.escape(row["rank"])}</div>
          </header>
          <a href="{quote_url}" target="_blank" rel="noopener"><img src="{chart_url}" alt="{html.escape(ticker)} chart" loading="lazy"></a>
          <div class="grid">
            <div><span>Market Cap</span><b>{html.escape(row["market_cap"])}</b></div>
            <div><span>P/E</span><b>{html.escape(row["pe"])}</b></div>
            <div><span>Price</span><b>{html.escape(row["price"])}</b></div>
            <div><span>Change</span><b>{html.escape(row["change"])}</b></div>
            <div><span>Volume</span><b>{html.escape(row["volume"])}</b></div>
            <div><span>Country</span><b>{html.escape(row["country"])}</b></div>
          </div>
          <p class="flag">À inspecter visuellement : tendance haussière multi-semaines + consolidation serrée / bull flag. Ceci est une watchlist, pas un signal d'achat automatique.</p>
        </article>
        """
        )
    return "\n".join(cards)


def render_html(rows: list[dict[str, str]]) -> str:
    generated = time.strftime("%Y-%m-%d %H:%M:%S UTC")
    finviz_link = f"https://finviz.com/screener.ashx?v=111&f={FILTERS}&o=-marketcap"
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Finviz LibertyVIP Watchlist</title>
  <style>
    :root {{ color-scheme: dark; font-family: Segoe UI, Arial, sans-serif; background:#080b12; color:#edf4ff; }}
    body {{ margin:0; padding:28px; background:radial-gradient(circle at top left,#18294d,#080b12 45%); }}
    h1 {{ margin:0; font-size:34px; }}
    .sub {{ color:#9aabc9; line-height:1.45; max-width:1120px; }}
    a {{ color:#9cc5ff; text-decoration:none; }}
    .criteria {{ display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 24px; }}
    .criteria span {{ background:#121a2b; border:1px solid #253658; border-radius:999px; padding:7px 10px; color:#cbd9f6; }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(390px,1fr)); gap:18px; }}
    .card {{ background:rgba(12,18,31,.88); border:1px solid #223553; border-radius:22px; padding:18px; box-shadow:0 18px 60px rgba(0,0,0,.25); }}
    .card header {{ display:flex; justify-content:space-between; gap:14px; align-items:center; margin-bottom:12px; }}
    h2 {{ margin:0; font-size:28px; }}
    header p {{ margin:5px 0 0; color:#8fa1c2; }}
    .rank {{ min-width:58px; height:58px; border-radius:16px; display:grid; place-items:center; font-size:19px; font-weight:800; background:linear-gradient(135deg,#25314f,#55668c); }}
    img {{ width:100%; min-height:220px; object-fit:contain; border-radius:14px; background:#05070d; border:1px solid #1d2a45; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:12px; }}
    .grid div {{ background:rgba(255,255,255,.035); border:1px solid #223553; border-radius:12px; padding:10px; }}
    .grid span {{ display:block; color:#8ea1c2; font-size:12px; margin-bottom:5px; }}
    .flag {{ color:#dce7ff; line-height:1.45; }}
    .note {{ margin-top:24px; color:#8ea1c2; font-size:13px; max-width:980px; }}
  </style>
</head>
<body>
  <h1>Finviz LibertyVIP Watchlist</h1>
  <p class="sub">Généré le {html.escape(generated)}. Cette page applique la grille fondamentale Finviz LibertyVIP. Les graphiques servent à repérer visuellement les titres en tendance haussière sur plusieurs semaines et en consolidation type flag. Lien screener original : <a href="{finviz_link}" target="_blank" rel="noopener">ouvrir dans Finviz</a>.</p>
  <div class="criteria">
    <span>P/E &gt; 30</span><span>Avg Vol &gt; 500K</span><span>Profit Margin positif</span><span>Quick Ratio &gt; 1</span><span>Sales Q/Q positif</span><span>EPS Q/Q positif</span><span>ROA/ROE/ROIC &gt; 10%</span><span>Short Float &lt; 5%</span>
  </div>
  <section class="cards">
    {render_cards(rows)}
  </section>
  <p class="note">Recherche éducative seulement. Cette watchlist ne constitue pas un conseil financier personnalisé ni une recommandation d'achat ou de vente.</p>
</body>
</html>"""


def main() -> None:
    STOCKS_DIR.mkdir(parents=True, exist_ok=True)
    rows = get_rows()
    if not rows:
        raise SystemExit("Aucun candidat Finviz récupéré. Publication annulée.")
    (STOCKS_DIR / "index.html").write_text(render_html(rows), encoding="utf-8")
    (STOCKS_DIR / "latest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"candidates={len(rows)}")
    print(STOCKS_DIR / "index.html")


if __name__ == "__main__":
    main()
