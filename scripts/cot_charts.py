"""
cot_charts.py — CFTC Commitments of Traders charts for the Market Information tab.

Port of @mitchclod's COT_Charts scripts (one chart per contract), recolored to
the site palette. Data and math are unchanged:

  COT   : CFTC Disaggregated COT, Futures Only (dataset 72hh-3qpy),
          Managed Money Long minus Managed Money Short = Net
  Price : Yahoo Finance continuous front-month, weekly close, aligned to COT
          report dates (nearest, 7-day tolerance)
  Range : rolling 10 years from today
  Bands : 10th / 90th percentile of MM Net over the window

Nothing is estimated. If a fetch fails, the chart is not redrawn.
"""

import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from datetime import date
from dateutil.relativedelta import relativedelta
from matplotlib import font_manager

CFTC_URL = 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json'

# slug, name, symbol, CFTC code, Yahoo ticker, price unit, group, 52wk corr, watermark
CONTRACTS = [
    ('corn', 'Corn', 'ZC', '002602', 'ZC=F', 'cents/bu', 'Grains & Oilseeds', True, 'center'),
    ('soybeans', 'Soybean', 'ZS', '005602', 'ZS=F', 'cents/bu', 'Grains & Oilseeds', True, 'center'),
    ('soymeal', 'Soybean Meal', 'ZM', '026603', 'ZM=F', 'dollars/ton', 'Grains & Oilseeds', True, 'center'),
    ('soyoil', 'Soybean Oil', 'ZL', '007601', 'ZL=F', 'cents/lb', 'Grains & Oilseeds', True, 'center'),
    ('chicago_wheat', 'Chicago Wheat', 'ZW', '001602', 'ZW=F', 'cents/bu', 'Grains & Oilseeds', True, 'center'),
    ('live_cattle', 'Live Cattle', 'LE', '057642', 'LE=F', 'cents/lb', 'Livestock', True, 'center'),
    ('feeder_cattle', 'Feeder Cattle', 'GF', '061641', 'GF=F', 'cents/lb', 'Livestock', True, 'center'),
    ('lean_hogs', 'Lean Hog', 'HE', '054642', 'HE=F', 'cents/lb', 'Livestock', True, 'center'),
    ('cotton', 'Cotton', 'CT', '033661', 'CT=F', 'cents/lb', 'Softs & Energy', True, 'center'),
    ('sugar', 'Sugar No. 11', 'SB', '080732', 'SB=F', 'cents/lb', 'Softs & Energy', True, 'center'),
    ('crude_oil', 'WTI Crude Oil', 'CL', '067651', 'CL=F', 'dollars/bbl', 'Softs & Energy', False, 'center'),
    ('natural_gas', 'Natural Gas', 'NG', '023651', 'NG=F', 'dollars/MMBtu', 'Softs & Energy', False, 'center'),
]

# Site palette (site/template.html :root)
PAPER, PAPER_2 = '#F4F3EF', '#ECEAE4'
INK, GREY, DIM, RULE = '#1E211E', '#484848', '#6B6E69', '#D6D4CC'
SAGE, SAGE_D, STONE = '#709070', '#4E6A4E', '#9A9C97'

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'fonts')


class COTError(Exception):
    pass


def _fonts():
    for f in os.listdir(FONT_DIR) if os.path.isdir(FONT_DIR) else []:
        if f.endswith('.ttf'):
            font_manager.fontManager.addfont(os.path.join(FONT_DIR, f))
    names = {f.name for f in font_manager.fontManager.ttflist}
    plt.rcParams['font.family'] = 'IBM Plex Sans' if 'IBM Plex Sans' in names else 'DejaVu Sans'
    return 'IBM Plex Mono' if 'IBM Plex Mono' in names else 'monospace'


def fetch_cot(code, start, get=requests.get):
    rows, offset = [], 0
    while True:
        r = get(CFTC_URL, params={
            '$where': f"cftc_contract_market_code='{code}' AND report_date_as_yyyy_mm_dd >= '{start}'",
            '$select': 'report_date_as_yyyy_mm_dd,m_money_positions_long_all,m_money_positions_short_all',
            '$order': 'report_date_as_yyyy_mm_dd ASC',
            '$limit': 5000, '$offset': offset,
        }, timeout=60)
        if r.status_code != 200:
            raise COTError(f'CFTC {code} HTTP {r.status_code}')
        page = r.json()
        rows += page
        offset += len(page)
        if len(page) < 5000:
            break
    if not rows:
        raise COTError(f'CFTC {code} returned no data')
    cot = pd.DataFrame(rows)
    cot['date'] = pd.to_datetime(cot['report_date_as_yyyy_mm_dd'])
    cot['mm_long'] = pd.to_numeric(cot['m_money_positions_long_all'], errors='coerce')
    cot['mm_short'] = pd.to_numeric(cot['m_money_positions_short_all'], errors='coerce')
    cot = cot.dropna(subset=['mm_long', 'mm_short'])
    cot['mm_net'] = cot['mm_long'] - cot['mm_short']
    return cot[['date', 'mm_long', 'mm_short', 'mm_net']].sort_values('date').reset_index(drop=True)


def fetch_prices(ticker, d0, d1):
    raw = yf.download(ticker, start=(d0 - pd.Timedelta(days=14)).strftime('%Y-%m-%d'),
                      end=(d1 + pd.Timedelta(days=14)).strftime('%Y-%m-%d'),
                      interval='1wk', auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        raise COTError(f'Yahoo Finance returned no data for {ticker}')
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    prices = raw[['Close']].copy()
    prices.index = pd.to_datetime(prices.index)
    prices.columns = ['price']
    return prices.dropna()


def build_chart(contract, out_dir, get=requests.get, log=print):
    """Fetch, compute, and draw one contract. Returns the stats dict."""
    slug, name, sym, code, ticker, unit, group, use_corr, wm = contract
    start = (date.today() - relativedelta(years=10)).strftime('%Y-%m-%d')
    cot = fetch_cot(code, start, get)
    prices = fetch_prices(ticker, cot['date'].min(), cot['date'].max())

    cot = cot.set_index('date')
    prices.index.name = 'date'
    cot['price'] = prices['price'].reindex(cot.index, method='nearest', tolerance=pd.Timedelta('7d'))
    missing = int(cot['price'].isna().sum())
    if missing:
        log(f'      {sym}: {missing} COT dates had no price within 7 days (dropped)')
    cot = cot.dropna(subset=['price']).reset_index()
    if len(cot) < 104:
        raise COTError(f'{sym}: only {len(cot)} aligned weeks')

    roll_corr = cot['mm_net'].rolling(52).corr(cot['price']).iloc[-1] if use_corr else None
    net = cot['mm_net']
    p10, p90 = np.percentile(net, 10), np.percentile(net, 90)
    current = net.iloc[-1]
    pct = (net <= current).mean() * 100
    flag = 'EXTREME LONG' if current >= p90 else 'EXTREME SHORT' if current <= p10 else 'Normal range'
    latest = cot.iloc[-1]

    mono = _fonts()
    fig = plt.figure(figsize=(18, 9), facecolor=PAPER)
    ax_price = fig.add_subplot(111)
    ax_net = ax_price.twinx()
    fig.subplots_adjust(top=0.89, bottom=0.09, left=0.07, right=0.93)

    # Extreme zones
    ax_net.axhspan(p90, net.max() * 1.12, alpha=0.16, color=SAGE, lw=0)
    ax_net.axhspan(net.min() * 1.12, p10, alpha=0.16, color=STONE, lw=0)
    ax_net.axhline(p90, color=SAGE_D, lw=0.9, ls='--', alpha=0.8)
    ax_net.axhline(p10, color=GREY, lw=0.9, ls='--', alpha=0.8)
    ax_net.axhline(0, color=STONE, lw=0.8, alpha=0.6)

    ax_net.fill_between(cot['date'], net, 0, where=(net >= 0), alpha=0.08, color=INK, lw=0)
    ax_net.fill_between(cot['date'], net, 0, where=(net < 0), alpha=0.14, color=STONE, lw=0)

    net_line, = ax_net.plot(cot['date'], net, color=INK, lw=1.4, zorder=5)
    price_line, = ax_price.plot(cot['date'], cot['price'], color=SAGE, lw=2.1, zorder=6)
    ax_price.scatter(cot['date'].iloc[-1], cot['price'].iloc[-1], color=SAGE, s=60, zorder=10)
    ax_net.scatter(cot['date'].iloc[-1], current, color=INK, s=60, zorder=10)
    ax_price.set_zorder(ax_net.get_zorder() + 1)
    ax_price.patch.set_visible(False)
    ax_net.patch.set_facecolor(PAPER)

    # Axes
    ax_price.set_ylabel(f'{sym} Nearby Futures Price  ({unit})', fontsize=10, color=SAGE_D, fontweight='semibold')
    ax_price.tick_params(axis='y', colors=SAGE_D, labelsize=9)
    ax_price.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f' if cot['price'].max() >= 20 else '%.2f'))
    ax_net.grid(axis='y', color=RULE, lw=0.6, alpha=0.7)
    ax_net.grid(axis='x', color=RULE, lw=0.4, alpha=0.5)
    for ax in (ax_price, ax_net):
        ax.spines[['top', 'right', 'left', 'bottom']].set_color(RULE)
    ax_net.set_ylabel('Managed Money Net Position  (contracts)', fontsize=10, color=INK, fontweight='semibold')
    ax_net.tick_params(axis='y', colors=INK, labelsize=9)
    ax_net.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{x/1000:.0f}K'))

    ax_price.xaxis.set_major_locator(mdates.YearLocator())
    ax_price.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=[4, 7, 10]))
    ax_price.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax_price.tick_params(axis='x', labelsize=9, colors=GREY)
    ax_price.set_xlabel('COT Report Date  (positions as of Tuesday, released the following Friday)',
                        fontsize=9, color=DIM)

    ax_price.legend(
        [price_line, net_line,
         mpatches.Patch(color=SAGE, alpha=0.45), mpatches.Patch(color=STONE, alpha=0.45)],
        [f'{sym} Nearby Futures (LHS)', 'MM Net Position (RHS)',
         f'Above 90th pct  ({p90/1000:.0f}K)', f'Below 10th pct  ({p10/1000:.0f}K)'],
        loc='upper left', fontsize=8.5, framealpha=0.95, facecolor=PAPER, edgecolor=RULE, labelcolor=GREY)

    report_date = latest['date'].strftime('%b %d, %Y')
    stats = (f"Report date : {report_date}\n"
             f"Price       : {latest['price']:.3f}\n"
             f"MM Long     : {latest['mm_long']:,.0f}\n"
             f"MM Short    : {latest['mm_short']:,.0f}\n"
             f"MM Net      : {current:,.0f}\n"
             f"Percentile  : {pct:.1f}th  --  {flag}")
    if use_corr:
        stats += f"\nCorr (52wk) : {roll_corr:.2f}"
    ax_price.text(0.335, 0.97, stats, transform=ax_price.transAxes, fontsize=8.2, va='top', ha='left',
                  zorder=10, family=mono, color=INK,
                  bbox=dict(boxstyle='round,pad=0.5', facecolor=PAPER_2, edgecolor=RULE, alpha=0.97))

    d0 = cot['date'].iloc[0].strftime('%b %Y')
    fig.text(0.50, 0.955, f'{name} Futures ({sym}) — Managed Money Net Position vs Weekly Price',
             ha='center', fontsize=14, fontweight='semibold', color=INK)
    fig.text(0.50, 0.933,
             f'CFTC Disaggregated COT, Futures Only  |  Managed Money Category  |  Weekly  |  {d0} - {report_date}',
             ha='center', fontsize=9, color=DIM)
    fig.text(0.01, 0.008, f'COT Source: CFTC publicreporting.cftc.gov  |  Price Source: Yahoo Finance ({ticker})',
             ha='left', fontsize=7.5, color=DIM)

    # Watermark — unchanged from the original scripts
    if wm == 'center':
        fig.text(0.50, 0.50, '@mitchclod', ha='center', va='center', fontsize=54, color='#7f8c8d',
                 fontstyle='italic', fontweight='bold', family='DejaVu Sans', alpha=0.08, rotation=0,
                 transform=fig.transFigure, zorder=0)
    else:
        fig.text(0.995, 0.008, '@mitchclod', ha='right', fontsize=9.5, color='#7f8c8d',
                 fontstyle='italic', fontweight='bold', family='DejaVu Sans')

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'{slug}.png')
    fig.savefig(path, dpi=110, bbox_inches='tight', facecolor=PAPER, edgecolor='none')
    plt.close(fig)

    return {
        'slug': slug, 'name': name, 'symbol': sym, 'group': group,
        'report_date': latest['date'].strftime('%Y-%m-%d'),
        'mm_long': int(latest['mm_long']), 'mm_short': int(latest['mm_short']), 'mm_net': int(current),
        'percentile': round(float(pct), 1), 'flag': flag, 'weeks': len(cot),
    }


def latest_report_date(code, get=requests.get):
    r = get(CFTC_URL, params={'$select': 'max(report_date_as_yyyy_mm_dd) as d',
                              '$where': f"cftc_contract_market_code='{code}'"}, timeout=60)
    if r.status_code != 200:
        raise COTError(f'CFTC {code} HTTP {r.status_code}')
    d = (r.json() or [{}])[0].get('d')
    if not d:
        raise COTError(f'CFTC {code}: no report date')
    return d[:10]
