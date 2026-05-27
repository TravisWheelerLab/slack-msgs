#!/usr/bin/env python3
"""
Re-download all attachment files from the Slack CDN, overwriting whatever is
already on disk.  The JSON files are NOT modified — this script is purely a
file download pass.

Use this to ensure local copies are the canonical files, particularly if the
old backup code may have overwritten files with the same name from different
messages (the filename-collision bug).

The destination path is read from url_private (already set to the local
/channel/.../attachments/{file_id}-{name} format).  The CDN URL is read from
url_private_download, which was never rewritten during migration.

Files that can no longer be fetched from the CDN are left as-is on disk.

Usage:
    TOKEN=xoxb-... python redownload_files.py
"""

import json, os, pathlib, urllib.request, urllib.parse, urllib.error

TOKEN        = os.environ.get('TOKEN', '')
ARCHIVE_ROOT = pathlib.Path("backup")


def strip_token(url: str) -> str:
    """Remove any appended ?t=... query string from a Slack CDN URL."""
    return url.split('?')[0]


def try_download(url: str, dest: pathlib.Path, token: str) -> bool:
    """Download url to dest using Bearer auth. Returns True on success."""
    if not token:
        print("  WARNING: TOKEN not set — cannot authenticate with Slack CDN")
        return False
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req) as resp:
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(dest, 'wb') as fh:
                fh.write(resp.read())
        return True
    except urllib.error.HTTPError as e:
        print(f"  [HTTP {e.code}]     {dest.name}")
        return False
    except (urllib.error.URLError, OSError) as e:
        print(f"  [error]       {dest.name}: {e}")
        return False


downloaded = skipped = missing = 0

for json_path in sorted(ARCHIVE_ROOT.glob("*/all.json")):
    channel_name = json_path.parent.name

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Skipping {json_path}: {e}")
        continue

    msgs = data if isinstance(data, list) else data.get("messages", [])
    if not isinstance(msgs, list):
        continue

    for msg in msgs:
        if not isinstance(msg, dict) or 'files' not in msg:
            continue

        for file_obj in msg['files']:
            if not isinstance(file_obj, dict):
                continue

            local_url = file_obj.get('url_private', '')
            if not local_url.startswith('/channel/'):
                continue  # not yet migrated — skip

            # Reconstruct local filesystem path from /channel/{ch}/attachments/{name}
            parts = local_url.lstrip('/').split('/')   # ['channel', ch, 'attachments', name]
            local_path = ARCHIVE_ROOT / pathlib.Path(*parts[1:])  # backup/{ch}/attachments/{name}

            # CDN download URL — was never rewritten, still points to Slack
            cdn_raw = file_obj.get('url_private_download', '')
            if not cdn_raw.startswith('https://'):
                print(f"  [no CDN URL]  {channel_name}: {local_path.name}")
                skipped += 1
                continue

            cdn_url = strip_token(cdn_raw)

            if try_download(cdn_url, local_path, TOKEN):
                print(f"  [downloaded]  {channel_name}: {local_path.name}")
                downloaded += 1
            else:
                if local_path.exists():
                    print(f"  [CDN gone, kept local]  {channel_name}: {local_path.name}")
                    skipped += 1
                else:
                    print(f"  [CDN gone, no local copy]  {channel_name}: {local_path.name}")
                    missing += 1

print(f"\nDone. Downloaded: {downloaded}  CDN unavailable: {skipped}  Missing entirely: {missing}")
