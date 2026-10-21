"""
build_site.py — Western Illinois Commodities LLC market information builder.

Pulls official data, then renders a single self-contained index.html.
Every number on the page comes from these sources. Nothing is typed in by hand.

  Grain Stocks & Acreage  USDA NASS Quick Stats API      (needs NASS_API_KEY)
  WASDE                   USDA WAOB wasdeMMYY.txt         (no key)
  Soybean Crush           USDA NASS Quick Stats API      (Fats & Oils data)
  Livestock               USDA NASS Quick Stats API + USDA AMS LMR Datamart LM_XB403
  Commitments of Traders  CFTC Disaggregated COT, Futures Only (managed money)
                          + Yahoo Finance weekly front-month price (charts in cot/)
  Report Calendar         USDA ESMIS publication API (upcoming release dates)
                          + USDA WAOB WASDE page (WASDE release dates)
  Publications            publications/*.pdf (our own articles; covers in publications/covers/)

Fail-closed rules
  * Each section is built independently.
  * A section that cannot be fetched, parsed, or fails a sanity check is NOT
    written. The page keeps that section's last successful data, with its dates.
  * Exit code 0 = all sections updated or unchanged; 2 = at least one failed.

Run locally:
  pip install -r requirements.txt
  macOS/Linux:  export NASS_API_KEY=yourkey
  Windows PS:   $env:NASS_API_KEY="yourkey"
  python scripts/build_site.py
"""

import base64
import html as html_lib
import json
import os
import re
import sys
import traceback
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DATA_DIR = os.path.join(ROOT, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'market_data.json')
DIAG_FILE = os.path.join(DATA_DIR, 'diagnostics.txt')
TEMPLATE = os.path.join(ROOT, 'site', 'template.html')
OUTPUT = os.path.join(ROOT, 'index.html')
COT_DIR = os.path.join(ROOT, 'cot')
LOGO = os.path.join(ROOT, 'assets', 'wic-mark.png')
PUB_DIR = os.path.join(ROOT, 'publications')
PUB_COVERS = os.path.join(PUB_DIR, 'covers')
SITE_URL = 'https://wicllc.org'
SITEMAP = os.path.join(ROOT, 'sitemap.xml')

QS_URL = 'https://quickstats.nass.usda.gov/api/api_GET/'
WASDE_URL = 'https://www.usda.gov/oce/commodity/wasde/wasde{mm}{yy}.txt'
AMS_BASE = 'https://mpr.datamart.ams.usda.gov/services/v1.1/reports/2453'  # LM_XB403
ESMIS_PUB = 'https://esmis.nal.usda.gov/api/v1/publication/findByIdentifier/{}'
WAOB_WASDE_PAGE = ('https://www.usda.gov/about-usda/general-information/staff-offices/'
                   'office-chief-economist/commodity-markets/wasde-report')
CENTRAL = ZoneInfo('America/Chicago')

# Reports behind the market tabs: (ESMIS identifier, name, agency, tab)
CALENDAR = [
    ('wasde', 'WASDE', 'USDA World Agricultural Outlook Board', 'WASDE Supply & Demand'),
    ('GraiStoc', 'Grain Stocks', 'USDA NASS', 'Grain Stocks & Acreage'),
    ('ProsPlan', 'Prospective Plantings', 'USDA NASS', 'Grain Stocks & Acreage'),
    ('Acre', 'Acreage', 'USDA NASS', 'Grain Stocks & Acreage'),
    ('FatsOils', 'Fats and Oils: Oilseed Crushings', 'USDA NASS', 'Soybean Crush'),
    ('CattOnFe', 'Cattle on Feed', 'USDA NASS', 'Livestock & Beef'),
    ('HogsPigs', 'Quarterly Hogs and Pigs', 'USDA NASS', 'Livestock & Beef'),
]

HEADERS = {'User-Agent': 'WICLLC-MarketInfo/2.0 (+https://wicllc.org)'}
MONTHS = {m: i for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], 1)}
MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
               'August', 'September', 'October', 'November', 'December']

# 2026 WASDE release dates as published by USDA. Add each new year's dates here.
WASDE_RELEASES = {
    (2026, 1): '2026-01-12', (2026, 2): '2026-02-10', (2026, 3): '2026-03-10',
    (2026, 4): '2026-04-09', (2026, 5): '2026-05-12', (2026, 6): '2026-06-11',
    (2026, 7): '2026-07-10', (2026, 8): '2026-08-12', (2026, 9): '2026-09-11',
    (2026, 10): '2026-10-09', (2026, 11): '2026-11-10', (2026, 12): '2026-12-10',
}

DIAG = []  # lines written to diagnostics.txt


class SectionError(Exception):
    pass


def diag(*parts):
    line = ' '.join(str(p) for p in parts)
    DIAG.append(line)


def log(msg):
    print(msg, flush=True)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def get(url, params=None, timeout=60):
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            return r
        except requests.RequestException as e:
            last = e
    key = os.environ.get('NASS_API_KEY', '')
    msg = str(last).replace(key, '***') if key else str(last)
    raise SectionError(f'Could not reach {url}: {msg}')


def num(v):
    """Quick Stats values are strings like '1,234' or '(D)'. Return float or None."""
    if v is None:
        return None
    s = str(v).replace(',', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


# ── Quick Stats ───────────────────────────────────────────────────────────────

def qs(**params):
    key = os.environ.get('NASS_API_KEY', '').strip()
    if not key:
        raise SectionError('NASS_API_KEY is not set')
    q = {'key': key, 'format': 'JSON'}
    q.update(params)
    r = get(QS_URL, params=q)
    shown = {k: v for k, v in params.items()}
    if r.status_code == 400:
        # Quick Stats returns 400 for "no data" as well as bad requests
        try:
            err = r.json().get('error')
        except ValueError:
            err = r.text[:200]
        diag(f'[QS] {shown} -> 400 {err}')
        return []
    if r.status_code != 200:
        raise SectionError(f'Quick Stats HTTP {r.status_code} for {shown}')
    rows = r.json().get('data', [])
    descs = sorted({f"{x.get('short_desc')} | {x.get('domain_desc')} | {x.get('reference_period_desc')}"
                    for x in rows})
    diag(f'[QS] {shown} -> {len(rows)} rows; distinct short_desc|domain|period ({len(descs)}):')
    for d in descs[:120]:
        diag('    ', d)
    return rows


def ref_key(row):
    """(year, month) for 'FIRST OF SEP' or monthly 'AUG'; None otherwise."""
    p = (row.get('reference_period_desc') or '').upper()
    y = int(row.get('year'))
    m = re.match(r'^FIRST OF ([A-Z]{3})$', p)
    if m and m.group(1) in MONTHS:
        return (y, MONTHS[m.group(1)])
    if p in MONTHS:
        return (y, MONTHS[p])
    return None


def load_date(row):
    lt = row.get('load_time') or ''
    return lt[:10] if re.match(r'\d{4}-\d{2}-\d{2}', lt) else None


def is_total(row):
    return (row.get('domain_desc') == 'TOTAL'
            and row.get('class_desc', 'ALL CLASSES') == 'ALL CLASSES'
            and row.get('prodn_practice_desc', 'ALL PRODUCTION PRACTICES') == 'ALL PRODUCTION PRACTICES'
            and row.get('agg_level_desc') == 'NATIONAL')


def check(label, value, lo, hi):
    if value is None or not (lo <= value <= hi):
        raise SectionError(f'Sanity check failed: {label} = {value} outside [{lo}, {hi}]')


# ── Section 1: Grain stocks & acreage ─────────────────────────────────────────

STOCK_BOUNDS = {'CORN': (0.5e9, 16e9), 'SOYBEANS': (0.1e9, 4.5e9), 'WHEAT': (0.3e9, 3.0e9)}
ACRE_BOUNDS = {'CORN': (70e6, 110e6), 'SOYBEANS': (60e6, 100e6), 'WHEAT': (30e6, 60e6)}


def build_grain():
    yr = date.today().year
    out = {'stocks': {}, 'acreage': {}}

    for c in ['CORN', 'SOYBEANS', 'WHEAT']:
        rows = qs(commodity_desc=c, statisticcat_desc='STOCKS', agg_level_desc='NATIONAL',
                  source_desc='SURVEY', year__GE=str(yr - 3))
        series = {}
        for r in rows:
            if not is_total(r) or r.get('unit_desc') != 'BU':
                continue
            k = ref_key(r)
            if not k or not (r.get('reference_period_desc') or '').startswith('FIRST OF'):
                continue
            sd = r.get('short_desc', '')
            kind = 'on' if 'ON FARM' in sd else 'off' if 'OFF FARM' in sd else 'total'
            v = num(r.get('Value'))
            if v is None:
                continue
            slot = series.setdefault(k, {})
            if kind in slot and slot[kind]['value'] != v:
                diag(f'[grain] duplicate {c} {k} {kind}: {slot[kind]["sd"]} vs {sd}')
                continue
            slot[kind] = {'value': v, 'sd': sd, 'loaded': load_date(r)}
        with_total = sorted(k for k, s in series.items() if 'total' in s)
        if not with_total:
            raise SectionError(f'No national {c} stocks found')
        latest = with_total[-1]
        cur = series[latest]
        prior = series.get((latest[0] - 1, latest[1]), {})
        check(f'{c} stocks', cur['total']['value'], *STOCK_BOUNDS[c])
        out['stocks'][c] = {
            'ref': f'{latest[0]}-{latest[1]:02d}-01',
            'loaded': cur['total']['loaded'],
            'total': cur['total']['value'],
            'on_farm': cur.get('on', {}).get('value'),
            'off_farm': cur.get('off', {}).get('value'),
            'prior_total': prior.get('total', {}).get('value'),
            'prior_on_farm': prior.get('on', {}).get('value'),
            'prior_off_farm': prior.get('off', {}).get('value'),
            'history': [{'ref': f'{k[0]}-{k[1]:02d}-01', 'total': series[k]['total']['value']}
                        for k in with_total[-9:]],
        }

    for c in ['CORN', 'SOYBEANS', 'WHEAT']:
        rows = qs(commodity_desc=c, statisticcat_desc='AREA PLANTED', agg_level_desc='NATIONAL',
                  source_desc='SURVEY', year__GE=str(yr - 1))
        rows = [r for r in rows if is_total(r) and r.get('unit_desc') == 'ACRES'
                and r.get('util_practice_desc', 'ALL UTILIZATION PRACTICES') == 'ALL UTILIZATION PRACTICES'
                and num(r.get('Value')) is not None]
        if not rows:
            raise SectionError(f'No national {c} planted acreage found')
        cy = max(int(r['year']) for r in rows)
        cur = [r for r in rows if int(r['year']) == cy]
        by_period = {r['reference_period_desc']: r for r in cur}
        latest = max(cur, key=lambda r: r.get('load_time') or '')
        prior_final = next((r for r in rows if int(r['year']) == cy - 1
                            and r['reference_period_desc'] == 'YEAR'), None)
        check(f'{c} planted acres', num(latest['Value']), *ACRE_BOUNDS[c])
        out['acreage'][c] = {
            'year': cy,
            'prospective': num(by_period.get('YEAR - MAR ACREAGE', {}).get('Value')),
            'june': num(by_period.get('YEAR - JUN ACREAGE', {}).get('Value')),
            'latest': num(latest['Value']),
            'latest_period': latest['reference_period_desc'],
            'latest_loaded': load_date(latest),
            'prior_final': num(prior_final['Value']) if prior_final else None,
        }
    return out


# ── Section 2: WASDE ──────────────────────────────────────────────────────────

ROW_RE = re.compile(r'^\s*(?P<label>\S.*?\S)\s{2,}(?P<nums>(?:-?[\d,]*\.?\d+|NA|\(NA\))(?:\s+(?:-?[\d,]*\.?\d+|NA|\(NA\)))*)\s*$')
YEAR_RE = re.compile(r'\d{4}/\d{2}')


def fetch_wasde_text():
    today = date.today()
    candidates = []
    y, m = today.year, today.month
    for _ in range(3):
        candidates.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    for (y, m) in candidates:
        url = WASDE_URL.format(mm=f'{m:02d}', yy=f'{y % 100:02d}')
        r = get(url)
        diag(f'[WASDE] {url} -> HTTP {r.status_code}, {len(r.text)} chars')
        if r.status_code == 200 and 'WASDE' in r.text and 'Supply and Use' in r.text:
            return y, m, url, r.text
    raise SectionError('No WASDE text file found for the last 3 months')


def parse_block(lines, title_re, commodity=None):
    """Parse one supply/use block from the WASDE text report."""
    start = next((i for i, l in enumerate(lines) if re.search(title_re, l, re.I)), None)
    if start is None:
        raise SectionError(f'WASDE table not found: {title_re}')
    h = next((i for i in range(start, min(start + 10, len(lines))) if len(YEAR_RE.findall(lines[i])) >= 2), None)
    if h is None:
        raise SectionError(f'WASDE column header not found under {title_re}')
    hdr = lines[h]
    years = [m.group(0) for m in YEAR_RE.finditer(hdr)]
    # tag each year with Est./Proj. if it directly follows
    labels = []
    for m in YEAR_RE.finditer(hdr):
        tail = hdr[m.end():m.end() + 7]
        tag = 'Proj.' if 'Proj' in tail else 'Est.' if 'Est' in tail else ''
        labels.append((m.group(0) + ' ' + tag).strip())
    month_line = lines[h + 1] if h + 1 < len(lines) else ''
    months = [t for t in month_line.split() if t[:3].upper() in MONTHS and len(t) <= 4]
    for i, mo in enumerate(months):
        idx = len(labels) - len(months) + i
        if 0 <= idx < len(labels):
            labels[idx] = labels[idx] + ' ' + mo
    ncol = len(labels)

    i = h + 1
    if commodity:
        j = next((k for k in range(h + 1, min(h + 150, len(lines))) if lines[k].strip().upper() == commodity), None)
        if j is None:
            raise SectionError(f'WASDE commodity block not found: {commodity}')
        i = j + 1
    unit = ''
    for k in range(h + 1, i):  # unit line printed above the commodity name
        t = lines[k].strip()
        indent = len(lines[k]) - len(lines[k].lstrip())
        if t and indent >= 15 and not re.search(r'\d', t) and not t.isupper() and not t.startswith(('=', 'Item')):
            unit = t
    rows, prefix = [], ''
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s:
            continue
        if rows and (s.startswith('=') or s.startswith('WASDE') or len(YEAR_RE.findall(s)) >= 2):
            break
        if set(s) <= set('=-') or s.startswith('Item'):
            continue  # header/rule lines above the first row
        if rows and s.isupper() and not re.search(r'\d', s) and len(s) < 40:
            break  # next commodity block
        m = ROW_RE.match(raw)
        if m:
            vals = m.group('nums').split()
            if len(vals) == ncol:
                label = re.sub(r'\s+\d+/\s*$', '', m.group('label')).strip()
                if prefix:
                    label = f'{prefix} {label}'
                    prefix = ''
                rows.append({'label': label, 'unit': 'Price' if 'price' in label.lower() else unit,
                             'values': [None if v in ('NA', '(NA)') else float(v.replace(',', '')) for v in vals]})
                continue
        if not re.search(r'\d', re.sub(r'\d+/', '', s)) and not s.isupper():
            indent = len(raw) - len(raw.lstrip())
            if indent >= 15:
                unit = s  # centered unit line, e.g. "Million Bushels"
            else:
                prefix = re.sub(r'\s+\d+/\s*$', '', s).strip()  # label wrapped onto two lines
    if not rows:
        raise SectionError(f'No rows parsed for WASDE {commodity or title_re}')
    return {'columns': labels, 'rows': rows}


def row_val(block, label_start):
    r = next((r for r in block['rows'] if r['label'].lower().startswith(label_start.lower())), None)
    return r['values'][-1] if r else None


def build_wasde():
    y, m, url, text = fetch_wasde_text()
    lines = text.splitlines()
    num_m = re.search(r'WASDE\s*-\s*(\d{3})', text)
    out = {
        'report_month': f'{MONTH_NAMES[m - 1]} {y}',
        'report_number': num_m.group(1) if num_m else None,
        'released': WASDE_RELEASES.get((y, m)),
        'source_url': url,
        'tables': {},
    }
    specs = [
        ('Corn', r'U\.S\.\s+Feed\s+Grain\s+and\s+Corn\s+Supply\s+and\s+Use', 'CORN', (500, 4000)),
        ('Soybeans', r'U\.S\.\s+Soybeans\s+and\s+Products\s+Supply\s+and\s+Use', 'SOYBEANS', (100, 1200)),
        ('Soybean Oil', r'U\.S\.\s+Soybeans\s+and\s+Products\s+Supply\s+and\s+Use', 'SOYBEAN OIL', None),
        ('Soybean Meal', r'U\.S\.\s+Soybeans\s+and\s+Products\s+Supply\s+and\s+Use', 'SOYBEAN MEAL', None),
        ('Wheat', r'U\.S\.\s+Wheat\s+Supply\s+and\s+Use', None, (300, 1500)),
    ]
    for name, title, block, bounds in specs:
        t = parse_block(lines, title, block)
        if bounds:
            check(f'WASDE {name} ending stocks', row_val(t, 'Ending Stocks'), *bounds)
        out['tables'][name] = t
    return out


# ── Section 3: Soybean crush (USDA NASS Fats & Oils via Quick Stats) ──────────

def build_crush():
    yr = date.today().year
    rows = qs(commodity_desc='SOYBEANS', agg_level_desc='NATIONAL', freq_desc='MONTHLY',
              year__GE=str(yr - 2))
    picks = {
        'crush_tons': lambda sd, u: 'CRUSHED' in sd and u == 'TONS',
        'oil_production_lb': lambda sd, u: 'OIL' in sd and 'CRUDE' in sd and 'PRODUCTION' in sd and u == 'LB',
        'oil_stocks_lb': lambda sd, u: 'OIL' in sd and 'CRUDE' in sd and 'STOCKS' in sd and u == 'LB',
    }
    series = {k: {} for k in picks}
    for r in rows:
        if r.get('domain_desc') != 'TOTAL':
            continue
        k = ref_key(r)
        v = num(r.get('Value'))
        if not k or v is None:
            continue
        sd, u = r.get('short_desc', ''), r.get('unit_desc', '')
        for name, test in picks.items():
            if test(sd, u):
                series[name].setdefault(k, {'value': v, 'sd': sd, 'loaded': load_date(r)})
    if not series['crush_tons']:
        raise SectionError('Soybean crush series not found in Quick Stats (see diagnostics.txt)')
    months = sorted(series['crush_tons'])
    latest = months[-1]
    crush = series['crush_tons'][latest]['value']
    check('Soybean crush (tons)', crush, 3.0e6, 8.5e6)

    def pt(name, k):
        return series[name].get(k, {}).get('value')
    return {
        'month': f'{latest[0]}-{latest[1]:02d}',
        'loaded': series['crush_tons'][latest]['loaded'],
        'crush_tons': crush,
        'crush_tons_prior_year': pt('crush_tons', (latest[0] - 1, latest[1])),
        'crush_tons_prior_month': pt('crush_tons', months[-2]) if len(months) > 1 else None,
        'oil_production_lb': pt('oil_production_lb', latest),
        'oil_stocks_lb': pt('oil_stocks_lb', latest),
        'series_labels': {k: (next(iter(v.values()))['sd'] if v else None) for k, v in series.items()},
        'history': [{'month': f'{k[0]}-{k[1]:02d}', 'crush_tons': series['crush_tons'][k]['value']}
                    for k in months[-13:]],
    }


# ── Section 4: Livestock ──────────────────────────────────────────────────────

def build_cattle():
    yr = date.today().year
    rows = qs(commodity_desc='CATTLE', agg_level_desc='NATIONAL', source_desc='SURVEY',
              year__GE=str(yr - 2))
    feed = [r for r in rows if 'ON FEED' in (r.get('short_desc') or '')]
    diag(f'[cattle] {len(feed)} ON FEED rows (short_desc | domain | domaincat | freq | period | year | load_time | value):')
    for r in sorted(feed, key=lambda r: (r.get('short_desc', ''), r.get('year', 0), r.get('load_time', ''))):
        diag('    ', ' | '.join(str(r.get(k)) for k in ['short_desc', 'domain_desc', 'domaincat_desc', 'freq_desc',
                                                        'reference_period_desc', 'year', 'load_time', 'Value']))
    CAP = 'CAPACITY: (1,000 OR MORE HEAD)'  # monthly Cattle on Feed universe
    inv, plc, mkt = {}, {}, {}
    for r in feed:
        if r.get('domaincat_desc') != CAP or num(r.get('Value')) is None:
            continue
        k, sd, v = ref_key(r), r['short_desc'], num(r['Value'])
        if not k:
            continue
        if sd == 'CATTLE, ON FEED - INVENTORY' and r['reference_period_desc'].startswith('FIRST OF'):
            inv.setdefault(k, (v, load_date(r)))
        elif sd == 'CATTLE, ON FEED - PLACEMENTS, MEASURED IN HEAD' and r.get('freq_desc') == 'MONTHLY':
            plc.setdefault(k, v)
        elif sd == 'CATTLE, ON FEED - SALES FOR SLAUGHTER, MEASURED IN HEAD' and r.get('freq_desc') == 'MONTHLY':
            mkt.setdefault(k, v)  # NASS "marketings" = fed cattle sold for slaughter
    inv_one = inv
    if not inv_one:
        raise SectionError('Cattle on feed (1,000+ head capacity) inventory not found (see diagnostics.txt)')
    ks = sorted(inv_one)
    latest = ks[-1]
    val, loaded = inv_one[latest]
    check('Cattle on feed (head)', val, 8e6, 16e6)
    pm = (latest[0], latest[1] - 1) if latest[1] > 1 else (latest[0] - 1, 12)
    prior = inv_one.get((latest[0] - 1, latest[1]))
    return {
        'ref': f'{latest[0]}-{latest[1]:02d}-01',
        'loaded': loaded,
        'on_feed': val,
        'on_feed_prior_year': prior[0] if prior else None,
        'flow_month': f'{pm[0]}-{pm[1]:02d}',
        'placements': plc.get(pm),
        'placements_prior_year': plc.get((pm[0] - 1, pm[1])),
        'marketings': mkt.get(pm),
        'marketings_prior_year': mkt.get((pm[0] - 1, pm[1])),
        'history': [{'ref': f'{k[0]}-{k[1]:02d}-01', 'on_feed': inv_one[k][0]} for k in ks[-13:]],
    }


def build_hogs():
    yr = date.today().year
    rows = qs(commodity_desc='HOGS', agg_level_desc='NATIONAL', source_desc='SURVEY',
              year__GE=str(yr - 2))
    rows = [r for r in rows if r.get('domain_desc') == 'TOTAL' and num(r.get('Value')) is not None]
    want = {'all': 'HOGS - INVENTORY', 'market': 'HOGS, MARKET - INVENTORY',
            'breeding': 'HOGS, BREEDING - INVENTORY'}
    series = {k: {} for k in want}
    for r in rows:
        k = ref_key(r)
        if not k or not r['reference_period_desc'].startswith('FIRST OF'):
            continue
        for name, sd in want.items():
            if r['short_desc'] == sd:
                series[name].setdefault(k, (num(r['Value']), load_date(r)))
    if not series['all']:
        raise SectionError('Hogs & pigs inventory not found (see diagnostics.txt)')
    latest = max(series['all'])
    val, loaded = series['all'][latest]
    check('All hogs & pigs (head)', val, 60e6, 85e6)
    py = (latest[0] - 1, latest[1])

    def g(name, k):
        v = series[name].get(k)
        return v[0] if v else None
    return {
        'ref': f'{latest[0]}-{latest[1]:02d}-01',
        'loaded': loaded,
        'all': val, 'all_prior_year': g('all', py),
        'market': g('market', latest), 'market_prior_year': g('market', py),
        'breeding': g('breeding', latest), 'breeding_prior_year': g('breeding', py),
    }


def ams_section(section, params=None):
    url = f'{AMS_BASE}/{requests.utils.quote(section)}'
    r = get(url, params=params)
    diag(f'[AMS] {section} {params or ""} -> HTTP {r.status_code}')
    if r.status_code != 200:
        raise SectionError(f'AMS LM_XB403 "{section}" HTTP {r.status_code}')
    j = r.json()
    res = j.get('results') if isinstance(j, dict) else j
    if not res:
        raise SectionError(f'AMS LM_XB403 "{section}" returned no results')
    diag(f'[AMS] {section} keys: {sorted(res[0].keys())}')
    return res


def pick_key(keys, *must):
    for k in keys:
        if all(m in k.lower() for m in must):
            return k
    return None


def as_date(s):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def build_cutout(previous):
    res = ams_section('Current Cutout Values')
    res = [x for x in res if as_date(x.get('report_date', ''))]
    if not res:
        raise SectionError('AMS cutout: no dated records')
    latest_d = max(as_date(x['report_date']) for x in res)
    rec = next(x for x in res if as_date(x['report_date']) == latest_d)
    keys = list(rec.keys())
    ck = pick_key(keys, 'choice', '600', 'current') or pick_key(keys, 'choice', '600')
    sk = pick_key(keys, 'select', '600', 'current') or pick_key(keys, 'select', '600')
    choice, select = num(rec.get(ck)), num(rec.get(sk))
    check('Choice cutout', choice, 150, 700)
    check('Select cutout', select, 150, 700)

    primals = []
    try:
        pr = ams_section('Composite Primal Values')
        pr = [x for x in pr if as_date(x.get('report_date', '')) == latest_d]
        if pr:
            pk = list(pr[0].keys())
            dk = pick_key(pk, 'primal')
            pc = pick_key(pk, 'choice', '600')
            ps = pick_key(pk, 'select', '600')
            for x in pr:
                c, s = num(x.get(pc)), num(x.get(ps))
                if x.get(dk) and c is not None:
                    primals.append({'primal': re.sub(r'^Primal\s+', '', str(x.get(dk)), flags=re.I).title(), 'choice': c, 'select': s})
    except SectionError as e:
        diag(f'[AMS] primal values skipped: {e}')

    # history: 10 years of official values, last report of each week
    try:
        start = latest_d.replace(year=latest_d.year - 10)
    except ValueError:  # Feb 29
        start = latest_d.replace(year=latest_d.year - 10, day=28)
    try:
        hres = ams_section('Current Cutout Values',
                           {'q': f'report_date={start:%m/%d/%Y}:{latest_d:%m/%d/%Y}'})
        weeks = {}
        for x in hres:
            d = as_date(x.get('report_date', ''))
            c, s = num(x.get(ck)), num(x.get(sk))
            if d and c and s and 150 <= c <= 700 and 150 <= s <= 700:
                wk = d.isocalendar()[:2]
                if wk not in weeks or d > as_date(weeks[wk]['date']):
                    weeks[wk] = {'date': d.isoformat(), 'choice': c, 'select': s}
        history = sorted(weeks.values(), key=lambda h: h['date'])
        diag(f'[AMS] cutout history: {len(history)} weeks from {history[0]["date"]}')
    except (SectionError, IndexError) as e:
        diag(f'[AMS] cutout history fetch failed, keeping previous: {e}')
        history = (previous or {}).get('history', [])
    if not history or history[-1]['date'] < latest_d.isoformat():
        history = [h for h in history if as_date(h['date']).isocalendar()[:2] != latest_d.isocalendar()[:2]]
        history.append({'date': latest_d.isoformat(), 'choice': choice, 'select': select})
    return {
        'report_date': latest_d.isoformat(),
        'choice': choice, 'select': select,
        'fields': [ck, sk],
        'primals': primals,
        'history': history,
    }


def build_livestock(previous):
    out, errors = {}, []
    prev = previous or {}
    for name, fn in [('cutout', lambda: build_cutout(prev.get('cutout'))),
                     ('cattle', build_cattle), ('hogs', build_hogs)]:
        try:
            out[name] = fn()
            log(f'    OK {name}')
        except Exception as e:
            errors.append(f'{name}: {e}')
            diag(f'[livestock:{name}] {traceback.format_exc()}')
            if prev.get(name):
                out[name] = prev[name]
                log(f'    FAIL {name}: {e}  (keeping previous data)')
            else:
                log(f'    FAIL {name}: {e}')
    if not out:
        raise SectionError('; '.join(errors))
    return out, errors


def esmis_upcoming(ident):
    r = get(ESMIS_PUB.format(ident))
    diag(f'[ESMIS] {ident} -> HTTP {r.status_code}')
    if r.status_code != 200:
        raise SectionError(f'ESMIS {ident} HTTP {r.status_code}')
    j = r.json()
    pubs = j.get('results', j) if isinstance(j, dict) else j
    pub = next(p for p in (pubs if isinstance(pubs, list) else [pubs]) if ident in (p.get('identifier') or []))
    return [datetime.strptime(u, '%Y-%m-%dT%H:%M:%S%z') for u in pub.get('upcoming_releases') or []]


def waob_wasde_dates():
    """WASDE release dates from WAOB's WASDE page, e.g.
    '2027 WASDE Release Dates (12:00pm ET) In 2027 the WASDE report will be
    released on Jan. 12, Feb. 10, ... and Dec. 10.'  Reads every year listed."""
    r = get(WAOB_WASDE_PAGE)
    diag(f'[WAOB] WASDE page -> HTTP {r.status_code}')
    if r.status_code != 200:
        raise SectionError(f'WAOB WASDE page HTTP {r.status_code}')
    text = re.sub(r'\s+', ' ', html_lib.unescape(re.sub(r'<[^>]+>', ' ', r.text)))
    eastern = ZoneInfo('America/New_York')
    out = []
    for yr, hh, mm, ap, dates in re.findall(
            r'(\d{4}) WASDE Release Dates \((\d{1,2}):(\d{2}) ?([ap]m) ET\).*?released on (.*?\d)\.(?:\s|$)', text, re.I):
        hour = int(hh) % 12 + (12 if ap.lower() == 'pm' else 0)
        found = re.findall(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? +(\d{1,2})\b', dates)
        if not 10 <= len(found) <= 12:
            raise SectionError(f'WAOB {yr}: expected ~12 WASDE dates, found {len(found)}')
        for mon, day in found:
            out.append(datetime(int(yr), MONTHS[mon.upper()], int(day), hour, int(mm), tzinfo=eastern))
        diag(f'[WAOB] {yr} WASDE dates: {len(found)}')
    if not out:
        raise SectionError('WAOB WASDE page: no release-date list found')
    return out


# Where each report's upcoming dates come from; results are merged.
CALENDAR_SOURCES = {'wasde': [lambda: esmis_upcoming('wasde'), waob_wasde_dates]}


def build_calendar(previous):
    prev = {r['id']: r for r in (previous or {}).get('reports', [])}
    now = datetime.now(CENTRAL)
    reports, errors = [], []
    for ident, name, agency, tab in CALENDAR:
        row = {'id': ident, 'name': name, 'agency': agency, 'tab': tab}
        sources = CALENDAR_SOURCES.get(ident, [lambda i=ident: esmis_upcoming(i)])
        times, failed = set(), 0
        for fetch in sources:
            try:
                times.update(t.astimezone(CENTRAL).strftime('%Y-%m-%dT%H:%M') for t in fetch())
            except Exception as e:
                failed += 1
                errors.append(f'{name}: {e}')
                diag(f'[calendar:{ident}] {traceback.format_exc()}')
        if failed == len(sources):
            times = set((prev.get(ident) or {}).get('upcoming', []))
        row['upcoming'] = sorted(t for t in times if t > now.strftime('%Y-%m-%dT%H:%M'))[:3]
        diag(f'[calendar] {ident} upcoming: {row["upcoming"]}')
        reports.append(row)
    if len(errors) == len(CALENDAR):
        raise SectionError('; '.join(errors))
    return {'checked': now.strftime('%Y-%m-%dT%H:%M'), 'reports': reports}, errors


def build_cot(previous):
    """One chart per contract. Redrawn only when CFTC has a newer report."""
    import cot_charts
    prev = {c['slug']: c for c in (previous or {}).get('contracts', [])}
    out, errors = [], []
    for contract in cot_charts.CONTRACTS:
        slug, name, sym, code = contract[:4]
        old = prev.get(slug)
        try:
            latest = cot_charts.latest_report_date(code, get=get)
            diag(f'[CFTC] {sym} latest report {latest}')
            if old and old.get('report_date') == latest and os.path.exists(os.path.join(COT_DIR, f'{slug}.png')):
                out.append(old)
                continue
            stats = cot_charts.build_chart(contract, COT_DIR, get=get, log=log)
            check(f'{sym} MM long', stats['mm_long'], 0, 2e6)
            check(f'{sym} MM short', stats['mm_short'], 0, 2e6)
            out.append(stats)
            log(f'    NEW {sym} {stats["report_date"]}  net {stats["mm_net"]:,}  {stats["percentile"]}th pct')
        except Exception as e:
            errors.append(f'{name}: {e}')
            diag(f'[cot:{slug}] {traceback.format_exc()}')
            if old:
                out.append(old)
                log(f'    FAIL {sym}: {e}  (keeping previous chart)')
            else:
                log(f'    FAIL {sym}: {e}')
    if not out:
        raise SectionError('; '.join(errors))
    return {'contracts': out}, errors


# ── Publications ──────────────────────────────────────────────────────────────

PUB_DATE_RE = re.compile(r'^([A-Z]{3}) (\d{1,2}), (\d{4})$')
PUB_FILE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})-')


def pub_cover(doc, path):
    """First wide chart in the article, saved whole (not cropped, so source
    credits stay visible). Falls back to the top of page 1."""
    from PIL import Image
    import io
    import pymupdf
    img = None
    for page in doc:
        for im in page.get_images():
            w, h = im[2], im[3]
            if w >= 800 and 1.3 <= w / h <= 2.6:
                pix = pymupdf.Pixmap(doc, im[0])
                if pix.alpha or pix.n > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                img = Image.open(io.BytesIO(pix.tobytes('png')))
                break
        if img:
            break
    if img is None:
        page = doc[0]
        clip = pymupdf.Rect(0, 0, page.rect.width, page.rect.width * 0.6)
        img = Image.open(io.BytesIO(page.get_pixmap(dpi=110, clip=clip).tobytes('png')))
    img = img.convert('RGB')
    img.thumbnail((720, 720))
    img.save(path, 'JPEG', quality=84, optimize=True)


def pub_meta(doc, name):
    """Title, subtitle, author, date and opening lines from page 1. Substack's
    PDF layout: title / subtitle / AUTHOR / MON DD, YYYY / body."""
    lines = [l.strip() for l in doc[0].get_text().splitlines() if l.strip()]
    m = PUB_FILE_RE.match(name)
    when = date(int(m[1]), int(m[2]), int(m[3])) if m else None
    title, subtitle, author, body = (lines[0] if lines else name), '', '', lines[1:]
    for i, l in enumerate(lines[:6]):
        d = PUB_DATE_RE.match(l)
        if d:
            when = date(int(d[3]), MONTHS[d[1]], int(d[2]))
            head = lines[1:i]
            subtitle = head[0] if len(head) >= 2 else ''
            author = head[-1].title() if head else ''
            body = lines[i + 1:]
            break
    excerpt = ''
    for l in body:
        if len(excerpt) > 260:
            break
        excerpt += (' ' if excerpt else '') + l
    if len(excerpt) > 260:
        excerpt = excerpt[:260].rsplit(' ', 1)[0].rstrip(',;:') + '…'
    if when is None:
        raise ValueError(f'{name}: no date in file name or on page 1')
    return {'title': title, 'subtitle': subtitle, 'author': author,
            'date': when.isoformat(), 'excerpt': excerpt, 'pages': doc.page_count}


def wayback(url):
    """Ask the Internet Archive to snapshot url. Returns the snapshot link."""
    r = requests.get('https://web.archive.org/save/' + url, headers=HEADERS, timeout=180)
    r.raise_for_status()
    loc = r.headers.get('Content-Location') or ''
    if loc.startswith('/web/'):
        return 'https://web.archive.org' + loc
    m = re.search(r'/web/(\d{14})/', r.url)
    return r.url if m else 'https://web.archive.org/web/*/' + url


def build_publications(previous):
    """Articles from publications/*.pdf, newest first. Name files
    YYYY-MM-DD-short-title.pdf. Covers are cached in publications/covers/.
    With WIC_ARCHIVE=1 (set in the GitHub workflow), each article that is live
    on the site and not yet archived is sent to the Wayback Machine."""
    import pymupdf
    prev = {p['file']: p for p in (previous or {}).get('items', [])}
    os.makedirs(PUB_COVERS, exist_ok=True)
    items, errors = [], []
    names = sorted((n for n in os.listdir(PUB_DIR) if n.lower().endswith('.pdf')), reverse=True)
    for name in names:
        pdf = os.path.join(PUB_DIR, name)
        stem = os.path.splitext(name)[0]
        cover = os.path.join(PUB_COVERS, stem + '.jpg')
        try:
            with pymupdf.open(pdf) as doc:
                item = pub_meta(doc, name)
                if not os.path.exists(cover) or os.path.getmtime(cover) < os.path.getmtime(pdf):
                    pub_cover(doc, cover)
                    log(f'    NEW cover {stem}')
            item.update(file=name, cover=stem + '.jpg')
            old = prev.get(name) or {}
            for k in ('archived', 'archive_url'):
                if old.get(k):
                    item[k] = old[k]
            if os.environ.get('WIC_ARCHIVE') == '1' and not item.get('archived'):
                url = f'{SITE_URL}/publications/{name}'
                try:
                    if requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True).status_code == 200:
                        item['archive_url'] = wayback(url)
                        item['archived'] = datetime.now(CENTRAL).strftime('%Y-%m-%dT%H:%M')
                        log(f'    ARCHIVED {name} -> {item["archive_url"]}')
                    else:
                        diag(f'[pub] {name} not live yet at {url}; archive later')
                except Exception as e:
                    diag(f'[pub] archive {name}: {e}')
            items.append(item)
            diag(f'[pub] {name}: {item["date"]} {item["title"]!r}')
        except Exception as e:
            errors.append(f'{name}: {e}')
            diag(f'[pub:{name}] {traceback.format_exc()}')
            log(f'    FAIL {name}: {e}')
    items.sort(key=lambda p: (p['date'], p['file']), reverse=True)
    for c in os.listdir(PUB_COVERS):
        if os.path.splitext(c)[0] + '.pdf' not in names:
            os.remove(os.path.join(PUB_COVERS, c))
    return {'items': items}, errors


# ── Render ────────────────────────────────────────────────────────────────────

def render(data):
    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read()
    with open(LOGO, 'rb') as f:
        logo = 'data:image/png;base64,' + base64.b64encode(f.read()).decode()
    html = html.replace('assets/wic-mark.png', logo)
    # Background photos, inlined so index.html stays self-contained.
    assets = os.path.dirname(LOGO)
    for name in sorted(os.listdir(assets)):
        if name.startswith('bg-') and name.endswith('.jpg') and f'assets/{name}' in html:
            with open(os.path.join(assets, name), 'rb') as f:
                uri = 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()
            html = html.replace(f'assets/{name}', uri)
    payload = json.dumps(data, separators=(',', ':')).replace('</', '<\\/')
    marker = '<!--WIC_DATA-->'
    if marker not in html:
        raise RuntimeError('Template missing <!--WIC_DATA--> marker')
    html = html.replace(marker, f'<script>window.WIC_DATA={payload};</script>')
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)


def write_sitemap(data):
    """sitemap.xml: the home page plus every article PDF, for search engines."""
    pubs = ((data.get('publications') or {}).get('data') or {}).get('items', [])
    stamps = [(data.get(k) or {}).get('updated') for k in data]
    home = max([s for s in stamps if s] + [p['date'] for p in pubs] or [date.today().isoformat()])
    urls = [(SITE_URL + '/', home)] + [(f'{SITE_URL}/publications/{p["file"]}', p['date']) for p in pubs]
    body = ''.join(f'  <url><loc>{html_lib.escape(u)}</loc><lastmod>{d}</lastmod></url>\n' for u, d in urls)
    with open(SITEMAP, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + '</urlset>\n')


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass
    os.makedirs(DATA_DIR, exist_ok=True)
    previous = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding='utf-8') as f:
            previous = json.load(f)
    data = {k: previous.get(k) for k in ['grain', 'wasde', 'crush', 'livestock', 'cot', 'calendar', 'publications']}
    failed = []

    builders = [
        ('grain', 'Grain Stocks & Acreage', lambda: build_grain()),
        ('wasde', 'WASDE', lambda: build_wasde()),
        ('crush', 'Soybean Crush', lambda: build_crush()),
        ('livestock', 'Livestock & Meat', None),
        ('cot', 'Commitments of Traders', None),
        ('calendar', 'Report Calendar', None),
        ('publications', 'Publications', None),
    ]
    for key, label, fn in builders:
        log(f'- {label}')
        try:
            if key == 'livestock':
                section, errs = build_livestock((previous.get('livestock') or {}).get('data'))
                failed += [f'Livestock {e}' for e in errs]
            elif key == 'cot':
                section, errs = build_cot((previous.get('cot') or {}).get('data'))
                failed += [f'COT {e}' for e in errs]
            elif key == 'calendar':
                section, errs = build_calendar((previous.get('calendar') or {}).get('data'))
                failed += [f'Calendar {e}' for e in errs]
            elif key == 'publications':
                section, errs = build_publications((previous.get('publications') or {}).get('data'))
                failed += [f'Publication {e}' for e in errs]
            else:
                section = fn()
            old = (previous.get(key) or {}).get('data')
            data[key] = {'data': section,
                         'updated': date.today().isoformat() if section != old
                         else (previous.get(key) or {}).get('updated')}
            log(f'  OK {label}')
        except Exception as e:
            failed.append(f'{label}: {e}')
            diag(f'[{key}] {traceback.format_exc()}')
            log(f'  FAIL {label}: {e}' + ('  (keeping previous data)' if data.get(key) else ''))

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, sort_keys=True)
    with open(DIAG_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(DIAG) + '\n')
    render(data)
    write_sitemap(data)
    log(f'\nWrote {os.path.relpath(OUTPUT, ROOT)}')
    if failed:
        log('\nFAILED SECTIONS:\n  ' + '\n  '.join(failed))
        log(f'Details: {os.path.relpath(DIAG_FILE, ROOT)}')
        sys.exit(2)
    log('All sections OK.')


if __name__ == '__main__':
    main()
