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
| `.github/workflows/update_market_data.yml` | Runs the build weekdays around USDA/CFTC release times, then archives to S3. |
| `scripts/archive_records.py` | Copies the site, articles and Substack posts to the locked S3 records archive. |
| `aws/records-archive.yml` | One-time AWS setup (CloudFormation) for that archive. |

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

**Records archive (S3).** Every run also copies to the firm's S3 bucket, where Object Lock keeps each file read-only for 5 years:

- `site/YYYY/MM/DD/HHMM-<commit>/` the page, its data and COT charts each time the site changes, with `SHA256SUMS.txt`
- `publications/` each article PDF on the site
- `substack/<date>-<slug>/` each post on mitchclod.substack.com as published (`post.html` and the RSS entry `item.xml`)

Approval records (stamped PDFs, approval emails) are uploaded by hand to `approvals/` in the same bucket, never through this public repo.

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
4. Records archive: AWS console → CloudFormation (US East (Ohio)) → Create stack → upload `aws/records-archive.yml`. Then in the repo, Settings → Secrets and variables → Actions → **Variables**: `AWS_ARCHIVE_ROLE_ARN` and `AWS_ARCHIVE_BUCKET` from the stack's Outputs tab.
5. Pages: **only after compliance approval.** Settings → Pages → deploy from `main` branch root. Put the `CNAME` file back (it's kept as `../CNAME.after-approval`, contents `WICLLC.org`), then set up DNS.

## Before going live

- Written principal approval of the site (NFA Rule 2-29), dated copy kept.
- Check whether ADMIS needs to review it.
- Add NFA ID numbers in `site/template.html` (NFA membership paragraph in the footer).
