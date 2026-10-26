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
| `scripts/cot_charts.py` | Draws the CFTC Commitments of Traders charts (a port of the COT_Charts scripts, in site colors). |
| `cot/` | COT chart images, one per contract. Redrawn only when CFTC posts a new report. |
| `publications/` | Articles (PDF) shown in the Publications ribbon. See "Add an article" below. |
| `publications/covers/` | Card images, made automatically from the first wide chart in each PDF. Don't edit. |
| `assets/wic-mark.png` | Logo mark. |
| `assets/og-card.png` | Preview image shown when the site link is shared by text, email or chat. |
| `sitemap.xml` | List of pages and articles for Google/Bing. **Generated** on every build. |
| `robots.txt` | Tells search engines they may index the site and where the sitemap is. |
| `assets/fonts/` | IBM Plex fonts for the COT charts (SIL Open Font License, `OFL.txt`). |
| `.github/workflows/update_market_data.yml` | Runs the build weekdays around USDA/CFTC release times and on every push; deploys when something changed. |
| `.github/workflows/deploy_site.yml` | Deploys to GitHub Pages, then archives a website snapshot. Off until `PAGES_LIVE` = `true`. |
| `scripts/website_snapshot.py`, `scripts/archive_common.py` | Write to the WIC Documentation Archive (S3). |
| `aws/wic-documentation-archive.yml` | One-time AWS setup (CloudFormation) for the archive. |

## Data sources (every number comes from these; nothing is typed in)

- **Grain Stocks & Acreage:** USDA NASS Quick Stats API
- **WASDE:** USDA WAOB `wasdeMMYY.txt`
- **Soybean Crush:** USDA NASS Fats & Oils, via Quick Stats
- **Livestock:** USDA AMS LM_XB403 boxed beef cutout; USDA NASS Cattle on Feed and Hogs & Pigs, via Quick Stats
- **Commitments of Traders:** CFTC Disaggregated COT, Futures Only (publicreporting.cftc.gov, dataset 72hh-3qpy); price from Yahoo Finance weekly front-month
- **Report Calendar:** USDA ESMIS release schedule; WASDE dates from the USDA WAOB WASDE page

If a source can't be read or a value fails its sanity range, that section is **not** changed. The page keeps the last good data with its dates.

## Add an article

1. Use the **clean** PDF, the version without the compliance stamp. Stamped copies and the approval checklist stay in `~/Documents/WICLLC/`, never here; this repo is public.
2. Name it by publish date: `YYYY-MM-DD-short-title.pdf`, e.g. `2026-09-17-weekly-note.pdf`.
3. On GitHub: open the `publications` folder → **Add file → Upload files** → drop the PDF → **Commit changes**.

That's it. The upload starts the "Update market data" workflow, which reads the title, subtitle, date and opening lines from page 1 (Substack's PDF layout), makes the card image, and puts the article first in the ribbon. The site updates in about two minutes.

To remove an article, delete its PDF the same way; its card and cover go away on the next build.

**Archive / timestamp.** Each article's upload is a dated commit in the repo history. Once the site is live, the workflow also sends each new article to the Internet Archive's Wayback Machine (an independent, third-party timestamp) and records the snapshot link in `data/market_data.json` under `publications`. An article uploaded while the site isn't reachable is archived on a later run.

**WIC Documentation Archive (S3).** Required by the WIC Social Media Usage, Approval, Publication and Archival Policy (Articles 7 and 9). Object Lock in compliance mode keeps every file for 5 years; nothing can be changed or deleted.

- `website-snapshots/<time>_<commit>/` after every deployment: `site.tar.gz` (everything deployed), `live-index.html` (fetched from the live site), `manifest.json` (SHA-256 of every file)
- `workflow-runs/` one record per run. AWS emails each one (success and failure) to the Policy Owner and Designated Publisher, and sends an alert (Mon–Fri, 9 PM CDT / 8 PM CST) if no website snapshot succeeded that day.

Published Substack posts are archived as their clean PDFs in `publications/`, which every website snapshot includes. (Automated capture from Substack's RSS feed was dropped: Substack blocks requests from GitHub's servers.)

Approval Records (the draft email and approval reply) are kept in the Exchange email archive, per the Policy, not here.

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

## Turn on automation (once)

1. Repo → Settings → Secrets and variables → Actions → New repository secret: `NASS_API_KEY`.
2. Repo → Actions → "Update market data" → Run workflow (first run).
3. GitHub avatar → Settings → Notifications → Actions → email on failed workflows only.
4. Archive: deploy `aws/wic-documentation-archive.yml` in AWS CloudFormation (US East (Ohio)). Then in the repo, Settings → Secrets and variables → Actions: **secret** `AWS_ARCHIVE_ROLE_ARN` and **variable** `AWS_ARCHIVE_BUCKET`, from the stack's Outputs. The first deploy after go-live writes the first snapshot.

Under the Policy (Article 8.VI), changes to workflow files, archive destinations, retention settings, secrets or notification recipients need written approval from the Policy Owner or Supervisory Approver.
