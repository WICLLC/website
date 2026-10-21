"""
archive_records.py — copies WIC promotional material to the S3 records archive.

The bucket has Object Lock on, so every file written here is kept read-only for
the retention period (5 years). Nothing in this script deletes or overwrites.

  site/YYYY/MM/DD/HHMM-<commit>/   the published page, its data, COT charts and
                                   SHA256SUMS. Written when the build changed the
                                   site (SNAPSHOT=1), or if no snapshot exists yet.
  publications/<file>.pdf          each article on wicllc.org, once.
  substack/<date>-<slug>/          each Substack post as published: post.html
                                   (the live page) and item.xml (the RSS entry), once.

Runs in GitHub Actions after the build. Needs:
  ARCHIVE_BUCKET   bucket name (CloudFormation output)
  SNAPSHOT         '1' if this run committed a site change
  SNAPSHOT_SHA     commit the snapshot shows (falls back to GITHUB_SHA)
AWS credentials come from the workflow's OIDC role; no keys are stored.
"""

import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import boto3
import requests
from botocore.exceptions import ClientError

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PUB_DIR = os.path.join(ROOT, 'publications')
SUBSTACK_FEED = 'https://mitchclod.substack.com/feed'
CENTRAL = ZoneInfo('America/Chicago')
HEADERS = {'User-Agent': 'WICLLC-RecordsArchive/1.0 (+https://wicllc.org)'}

TYPES = {'.html': 'text/html; charset=utf-8', '.json': 'application/json', '.xml': 'application/xml',
         '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.txt': 'text/plain'}

s3 = boto3.client('s3')
BUCKET = os.environ['ARCHIVE_BUCKET']


def log(msg):
    print(msg, flush=True)


def exists(key):
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] in ('404', 'NoSuchKey', 'NotFound'):
            return False
        raise


def any_under(prefix):
    return s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1).get('KeyCount', 0) > 0


def put(key, body):
    ext = os.path.splitext(key)[1].lower()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ChecksumAlgorithm='SHA256',
                  ContentType=TYPES.get(ext, 'application/octet-stream'))
    log(f'  + s3://{BUCKET}/{key}')


def snapshot_site():
    sha = (os.environ.get('SNAPSHOT_SHA') or os.environ.get('GITHUB_SHA') or 'local')[:7]
    prefix = f'site/{datetime.now(CENTRAL):%Y/%m/%d/%H%M}-{sha}/'
    files = ['index.html', 'sitemap.xml', 'robots.txt', 'data/market_data.json']
    files += [f'cot/{n}' for n in sorted(os.listdir(os.path.join(ROOT, 'cot'))) if n.endswith('.png')]
    sums = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, 'rb') as f:
            body = f.read()
        sums.append(f'{hashlib.sha256(body).hexdigest()}  {rel}')
        put(prefix + rel, body)
    put(prefix + 'SHA256SUMS.txt', ('\n'.join(sums) + '\n').encode())


def archive_publications():
    for name in sorted(os.listdir(PUB_DIR)):
        if name.lower().endswith('.pdf') and not exists(f'publications/{name}'):
            with open(os.path.join(PUB_DIR, name), 'rb') as f:
                put(f'publications/{name}', f.read())


def archive_substack():
    r = requests.get(SUBSTACK_FEED, headers=HEADERS, timeout=60)
    r.raise_for_status()
    for item in ET.fromstring(r.content).iter('item'):
        link = (item.findtext('link') or '').strip()
        slug = re.sub(r'[^a-z0-9-]', '', link.rstrip('/').rsplit('/', 1)[-1].lower())
        published = parsedate_to_datetime(item.findtext('pubDate')).astimezone(CENTRAL)
        prefix = f'substack/{published:%Y-%m-%d}-{slug}/'
        if not slug or exists(prefix + 'post.html'):
            continue
        page = requests.get(link, headers=HEADERS, timeout=60)
        page.raise_for_status()
        put(prefix + 'item.xml', ET.tostring(item, encoding='utf-8'))
        put(prefix + 'post.html', page.content)


def main():
    failed = []
    steps = [('site snapshot', snapshot_site if os.environ.get('SNAPSHOT') == '1' or not any_under('site/') else None),
             ('publications', archive_publications),
             ('substack', archive_substack)]
    for label, fn in steps:
        if fn is None:
            log(f'- {label}: no site change this run')
            continue
        log(f'- {label}')
        try:
            fn()
        except Exception as e:
            failed.append(f'{label}: {e}')
            log(f'  FAIL {label}: {e}')
    if failed:
        sys.exit(2)


if __name__ == '__main__':
    main()
