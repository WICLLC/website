"""
substack_capture.py — daily Substack capture (Policy Article 9.II).

Reads the Substack RSS feed and saves every new or changed post to
substack-captures/<slug>/<capture time>/ in the WIC Documentation Archive:

  post.html      the public post page as served
  item.xml       the post's RSS entry (full text, title, date)
  api.json       Substack's metadata for the post, when available
  images/        every image in the post body
  manifest.json  capture time, source URLs, SHA-256 of each file, HTTP metadata

A post counts as changed when its title, subtitle, date or body text in the
feed differs from the last capture. Because RSS lists only recent posts
(Article 9.V), Substack's full post list is also checked so an older post is
still captured if it changes or was never captured.

Ends by writing a run record (workflow-runs/substack-capture/), which the
archive emails as the daily confirmation. Any error writes no record; the
workflow's failure step records the failure instead.
"""

import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape

import requests

from archive_common import BUCKET, log, now_utc, put, put_json, run_record, s3, sha256, stamp

PUBLICATION = 'https://mitchclod.substack.com'
FEED = PUBLICATION + '/feed'
POST_LIST = PUBLICATION + '/api/v1/archive?sort=new&offset={}&limit=50'
POST_API = PUBLICATION + '/api/v1/posts/{}'
HEADERS = {'User-Agent': 'WICLLC-DocumentationArchive/1.0 (+https://wicllc.org)'}
CONTENT = '{http://purl.org/rss/1.0/modules/content/}encoded'
IMG_RE = re.compile(r'<img\b[^>]*?\ssrc="([^"]+)"', re.I)


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r


def fingerprint(*parts):
    return hashlib.sha256('\x1f'.join(p or '' for p in parts).encode()).hexdigest()


def last_capture(slug):
    """Manifest of the newest capture of this post, or None."""
    prefix = f'substack-captures/{slug}/'
    keys = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=prefix):
        keys += [o['Key'] for o in page.get('Contents', []) if o['Key'].endswith('/manifest.json')]
    if not keys:
        return None
    return json.loads(s3.get_object(Bucket=BUCKET, Key=max(keys))['Body'].read())


def posts():
    """Every published post: feed entries first, then any older post from the
    full list that the feed no longer carries."""
    feed = get(FEED)
    out = {}
    for item in ET.fromstring(feed.content).iter('item'):
        link = (item.findtext('link') or '').strip()
        slug = link.rstrip('/').rsplit('/', 1)[-1]
        out[slug] = {
            'slug': slug, 'url': link, 'title': item.findtext('title'),
            'subtitle': item.findtext('description'),
            'date': parsedate_to_datetime(item.findtext('pubDate')).isoformat(),
            'body': item.findtext(CONTENT) or '', 'item_xml': ET.tostring(item, encoding='utf-8'),
            'source': 'rss',
        }
    offset = 0
    while True:
        batch = get(POST_LIST.format(offset)).json()
        for p in batch:
            if p['slug'] not in out and p.get('type') == 'newsletter':
                out[p['slug']] = {'slug': p['slug'], 'url': p['canonical_url'], 'title': p.get('title'),
                                  'subtitle': p.get('subtitle'), 'date': p.get('post_date'),
                                  'body': None, 'item_xml': None, 'source': 'post-list'}
        if len(batch) < 50:
            break
        offset += 50
    return feed, list(out.values())


def capture(post, reason, t):
    prefix = f'substack-captures/{post["slug"]}/{stamp(t)}/'
    files = []

    def add(name, body, source_url, headers=None):
        put(prefix + name, body)
        files.append({'file': name, 'sha256': sha256(body), 'bytes': len(body), 'source_url': source_url,
                      'http': {k: v for k, v in (headers or {}).items()
                               if k.lower() in ('content-type', 'last-modified', 'etag', 'date')}})

    page = get(post['url'])
    add('post.html', page.content, post['url'], page.headers)
    api = None
    try:
        r = get(POST_API.format(post['slug']))
        api = r.json()
        add('api.json', r.content, r.url, r.headers)
    except requests.RequestException as e:
        log(f'    (no api.json: {e})')
    if post['item_xml']:
        add('item.xml', post['item_xml'], FEED)
    body = post['body'] or (api or {}).get('body_html') or ''
    for i, src in enumerate(dict.fromkeys(unescape(s) for s in IMG_RE.findall(body)), 1):
        img = get(src)
        ext = {'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp'}.get(
            img.headers.get('content-type', '').split(';')[0], '.jpg')
        add(f'images/{i:02d}{ext}', img.content, src, img.headers)
    put_json(prefix + 'manifest.json', {
        'captured_at_utc': t.isoformat(timespec='seconds'), 'reason': reason,
        'slug': post['slug'], 'url': post['url'], 'title': post['title'], 'subtitle': post['subtitle'],
        'published': post['date'], 'found_via': post['source'], 'content_sha256': post['fingerprint'],
        'files': files,
    })
    return prefix


def main():
    t = now_utc()
    feed, found = posts()
    captured, unchanged = [], 0
    for p in sorted(found, key=lambda p: p['date'] or ''):
        if p['body'] is None:
            api = get(POST_API.format(p['slug'])).json()
            p['body'] = api.get('body_html') or ''
        p['fingerprint'] = fingerprint(p['title'], p['subtitle'], p['date'], p['body'])
        prev = last_capture(p['slug'])
        if prev and prev.get('content_sha256') == p['fingerprint']:
            unchanged += 1
            continue
        reason = 'changed' if prev else 'new'
        log(f'- {reason}: {p["slug"]}')
        captured.append({'slug': p['slug'], 'title': p['title'], 'reason': reason,
                         'prefix': capture(p, reason, t)})

    lines = [f'Substack capture {stamp(t)} from {PUBLICATION}',
             f'Posts found: {len(found)}  |  new or changed, captured: {len(captured)}  |  unchanged: {unchanged}', '']
    lines += [f'  {c["reason"].upper():8} {c["title"]}\n           s3://{BUCKET}/{c["prefix"]}' for c in captured]
    subject = f'WIC Archive: Substack capture OK ({len(captured)} new/changed)'
    run_record('substack-capture', True, subject, '\n'.join(lines),
               {'feed_sha256': sha256(feed.content), 'captured': captured, 'unchanged': unchanged})
    log('\n'.join(lines))


if __name__ == '__main__':
    sys.exit(main())
