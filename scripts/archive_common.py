"""
archive_common.py — shared pieces for writing to the WIC Documentation Archive
(S3, Object Lock in compliance mode; see aws/wic-documentation-archive.yml).

Every capture goes under a new, timestamped prefix, so nothing archived is
ever replaced. Each capture carries a manifest.json with the capture time,
SHA-256 of every file and the metadata available (Policy Articles 7.III.E,
9.I, 9.II).
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import boto3

BUCKET = os.environ['ARCHIVE_BUCKET']
RUN_URL = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
           f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
TYPES = {'.html': 'text/html; charset=utf-8', '.json': 'application/json', '.xml': 'application/xml',
         '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.gif': 'image/gif', '.webp': 'image/webp', '.txt': 'text/plain', '.gz': 'application/gzip'}

s3 = boto3.client('s3')


def now_utc():
    return datetime.now(timezone.utc)


def stamp(t):
    """Capture id: sortable and unique to the second, e.g. 2026-10-21T171503Z."""
    return t.strftime('%Y-%m-%dT%H%M%SZ')


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def log(msg):
    print(msg, flush=True)


def put(key, body, content_type=None):
    ext = os.path.splitext(key)[1].lower()
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ChecksumAlgorithm='SHA256',
                  ContentType=content_type or TYPES.get(ext, 'application/octet-stream'))
    log(f'  + s3://{BUCKET}/{key}')


def put_json(key, obj):
    put(key, (json.dumps(obj, indent=1, sort_keys=True) + '\n').encode())


def run_record(workflow, ok, subject, summary, detail=None):
    """workflow-runs/<workflow>/<time>_SUCCESS|FAILURE.json. The archive's
    notifier emails each one to the Policy Owner and the Designated Publisher."""
    t = now_utc()
    status = 'SUCCESS' if ok else 'FAILURE'
    put_json(f'workflow-runs/{workflow}/{stamp(t)}_{status}.json', {
        'workflow': workflow, 'status': status, 'time_utc': t.isoformat(timespec='seconds'),
        'subject': subject, 'summary': summary, 'run_url': RUN_URL,
        'commit': os.environ.get('GITHUB_SHA'), 'detail': detail or {},
    })


if __name__ == '__main__':
    # Used by workflow failure steps:
    #   python scripts/archive_common.py <workflow> "<what failed>"
    import sys
    wf, what = sys.argv[1], sys.argv[2]
    run_record(wf, False, f'WIC Archive ALERT: {wf} FAILED',
               f'{what}\n\nTreat as a Failure Event (Policy Article 12): open the run below, fix, '
               f're-run, and verify the capture in S3 the same Business Day.')
