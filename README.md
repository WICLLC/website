# WICLLC.org

Static site for Western Illinois Commodities LLC, served by GitHub Pages.

## Files

| Path | What it is |
|---|---|
| `index.html` | The site. **Generated — don't edit by hand.** Self-contained except the COT charts, which load from `cot/`: open it from this folder. |
| `site/template.html` | Edit this for wording, layout, contact info, disclosures. |
| `scripts/build_site.py` | Pulls USDA data and writes `index.html`. |
| `data/market_data.json` | Last good data for each section (audit trail). |
| `data/diagnostics.txt` | What each source returned on the last run. Send this if a section fails. |
| `scripts/cot_charts.py` | Draws the CFTC Commitments of Traders charts. |
| `cot/` | COT chart images, one per contract. Redrawn only when CFTC posts a new report. |
| `publications/` | Articles (PDF) shown in the Publications ribbon. See "Add an article" below. |
| `publications/covers/` | Card images, made automatically from the first wide chart in each PDF. Don't edit. |
| `assets/` | Logo, link-preview image, and IBM Plex fonts for the COT charts (SIL Open Font License, `OFL.txt`). |
| `sitemap.xml`, `robots.txt` | For search engines. `sitemap.xml` is **generated** on every build. |
| `.github/workflows/` | Rebuilds the site weekdays around USDA/CFTC release times and on every push, and deploys when something changed. |

## Data sources (every number comes from these; nothing is typed in)

- **Grain Stocks & Acreage:** USDA NASS Quick Stats API
- **WASDE:** USDA WAOB `wasdeMMYY.txt`
- **Soybean Crush:** USDA NASS Fats & Oils, via Quick Stats
- **Livestock:** USDA AMS LM_XB403 boxed beef cutout; USDA NASS Cattle on Feed and Hogs & Pigs, via Quick Stats
- **Commitments of Traders:** CFTC Disaggregated COT, Futures Only (publicreporting.cftc.gov, dataset 72hh-3qpy); price from Yahoo Finance weekly front-month
- **Report Calendar:** USDA ESMIS release schedule; WASDE dates from the USDA WAOB WASDE page

If a source can't be read or a value fails its sanity range, that section is **not** changed. The page keeps the last good data with its dates.

## Add an article

1. Use the clean PDF (no compliance stamp).
2. Name it by publish date: `YYYY-MM-DD-short-title.pdf`, e.g. `2026-09-17-weekly-note.pdf`.
3. On GitHub: open the `publications` folder → **Add file → Upload files** → drop the PDF → **Commit changes**.

That's it. The upload starts the "Update market data" workflow, which reads the title, subtitle, date and opening lines from page 1 (Substack's PDF layout), makes the card image, and puts the article first in the ribbon. The site updates in about two minutes.

To remove an article, delete its PDF the same way; its card and cover go away on the next build.

## Run it on your computer

```
cd wicllc
pip install -r requirements.txt
```

Mac:

```
export NASS_API_KEY=your_key_here
python3 scripts/build_site.py
```

Windows PowerShell:

```
$env:NASS_API_KEY="your_key_here"
python scripts/build_site.py
```

Then open `index.html` in a browser.
