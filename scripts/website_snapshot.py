"""
website_snapshot.py — website snapshot after each deployment (Policy Article 9.I).

  python scripts/website_snapshot.py <deployed site dir> <live site url>

Saves to website-snapshots/<capture time>_<commit>/ in the WIC Documentation Archive:

  site.tar.gz        every file that was deployed (archive package)
  live-index.html    the home page as fetched from the live site after deployment
  manifest.json      capture time, commit, workflow run, SHA-256 of the package
                     and of every file in it, and whether the live page matched

Fails (and the workflow records a failure) if the live home page does not
match the deployed one within about five minutes.
"""

import gzip
import io
import os
import sys
import tarfile
import time

import requests

from archive_common import RUN_URL, log, now_utc, put, put_json, run_record, sha256, stamp

HEADERS = {'User-Agent': 'WICLLC-DocumentationArchive/1.0 (+https://wicllc.org)'}


def package(site):
    """Deterministic tar.gz of the deployed files, plus a per-file hash list."""
    files, buf = [], io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz, tarfile.open(fileobj=gz, mode='w') as tar:
        for d, dirs, names in os.walk(site):
            dirs.sort()
            for n in sorted(names):
                path = os.path.join(d, n)
                rel = os.path.relpath(path, site)
                with open(path, 'rb') as f:
                    body = f.read()
                files.append({'file': rel, 'sha256': sha256(body), 'bytes': len(body)})
                info = tarfile.TarInfo(rel)
                info.size, info.mtime, info.mode = len(body), 0, 0o644
                tar.addfile(info, io.BytesIO(body))
    return buf.getvalue(), files


def live_index(url, expected):
    """Fetch the live home page (cache-busted) until it matches the deployment."""
    body = b''
    for attempt in range(11):
        r = requests.get(url, params={'snapshot': f'{time.time():.0f}'},
                         headers=HEADERS, timeout=60)
        body = r.content
        if r.status_code == 200 and sha256(body) == expected:
            return body, True, dict(r.headers)
        time.sleep(30)
    return body, False, {}


def main():
    site, url = sys.argv[1], sys.argv[2].rstrip('/') + '/'
    t = now_utc()
    full_commit = os.environ.get('SITE_COMMIT') or os.environ.get('GITHUB_SHA', 'local')
    commit = full_commit[:7]
    prefix = f'website-snapshots/{stamp(t)}_{commit}/'

    tar, files = package(site)
    with open(os.path.join(site, 'index.html'), 'rb') as f:
        deployed_index = sha256(f.read())
    live, matched, headers = live_index(url, deployed_index)

    put(prefix + 'site.tar.gz', tar)
    put(prefix + 'live-index.html', live)
    put_json(prefix + 'manifest.json', {
        'captured_at_utc': t.isoformat(timespec='seconds'), 'site_url': url, 'commit': full_commit,
        'workflow_run': RUN_URL, 'package': {'file': 'site.tar.gz', 'sha256': sha256(tar), 'bytes': len(tar)},
        'live_index': {'file': 'live-index.html', 'sha256': sha256(live), 'matches_deployed': matched,
                       'http': {k: v for k, v in headers.items()
                                if k.lower() in ('content-type', 'last-modified', 'etag', 'date', 'age')}},
        'files': files,
    })
    if not matched:
        raise SystemExit(f'Live page at {url} did not match the deployed index.html; snapshot saved, check it.')
    summary = (f'Website snapshot {stamp(t)} of {url} (commit {commit})\n'
               f'{len(files)} files, live page verified against the deployment.\n'
               f'Snapshot: {prefix}')
    run_record('website-snapshot', True, f'WIC Archive: website snapshot OK ({commit})', summary,
               {'prefix': prefix, 'package_sha256': sha256(tar)})
    log(summary)


if __name__ == '__main__':
    main()
