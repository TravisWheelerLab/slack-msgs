#!/usr/bin/env python3
"""
Migrate old-format file attachments in backup JSON files to the new format:
  - New storage: backup/{channel}/attachments/{file_id}-{original_name}
  - New URL in JSON: /channel/{channel}/attachments/{file_id}-{original_name}

For each url_private / thumb_* field that still points at a Slack CDN URL:

  1. Try to download the file fresh from the CDN (requires TOKEN env var).
  2. If the CDN URL is no longer available, look for a locally saved copy from
     the old backup format (backup/{channel}/{filename}, recorded in the JSON
     as url_private_file / thumb_X_file).  If the _file hint is absent, fall
     back to guessing backup/{channel}/{basename_of_url}.
  3. If the file is found neither on the CDN nor locally, leave that JSON
     field unchanged.

When falling back to a local copy we cannot verify that a generic name like
"image.png" truly belongs to this message, but we proceed on the assumption
that it does (the old backup code placed it there for this message).

Usage:
  TOKEN=xoxb-... python fix_links.py
"""

import json, os, pathlib, shutil, urllib.request, urllib.parse, urllib.error

TOKEN        = os.environ.get('TOKEN', '')
ARCHIVE_ROOT = pathlib.Path("backup")


def strip_token(url: str) -> str:
    """Remove any appended ?t=... query string from a Slack CDN URL."""
    return url.split('?')[0]


def try_download(url: str, dest: pathlib.Path, token: str) -> bool:
    """
    Attempt to GET url with a Bearer token and write to dest.
    Returns True on success, False on any HTTP/network error.
    """
    if not token:
        return False
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req) as resp:
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(dest, 'wb') as fh:
                fh.write(resp.read())
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def find_local_copy(file_obj: dict, key: str, channel_dir: pathlib.Path,
                    cdn_url: str) -> pathlib.Path | None:
    """
    Look for a file that the old backup code saved locally.
    Strategy:
      1. Use the old 'key_file' hint written by the previous backup code
         (e.g. url_private_file = "general/image.png").
      2. Guess backup/{channel}/{basename_of_cdn_url}.
    Returns the Path if the file exists, otherwise None.
    """
    # 1. Explicit hint left by old backup code
    hint = file_obj.get(key + '_file')
    if hint:
        candidate = ARCHIVE_ROOT / hint
        if candidate.exists():
            return candidate

    # 2. Guess from URL basename
    basename = os.path.basename(urllib.parse.urlparse(cdn_url).path)
    if basename:
        candidate = channel_dir / basename
        if candidate.exists():
            return candidate

    return None


# ---------------------------------------------------------------------------
# Main migration loop
# ---------------------------------------------------------------------------

for json_path in sorted(ARCHIVE_ROOT.glob("*/all.json")):
    channel_name  = json_path.parent.name
    channel_dir   = json_path.parent
    attachments_dir = channel_dir / "attachments"

    try:
        raw  = json_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as e:
        print(f"Skipping {json_path}: {e}")
        continue

    # Support both plain list and {"messages": [...]} envelope
    msgs = data if isinstance(data, list) else data.get("messages", [])
    if not isinstance(msgs, list):
        continue

    changed = False

    for msg in msgs:
        if not isinstance(msg, dict) or 'files' not in msg:
            continue

        for file_obj in msg['files']:
            if not isinstance(file_obj, dict):
                continue

            # ── Clean up stale from_url left by old fix_links.py ──
            # If url_private has already been migrated to a local /channel/
            # path, any from_url pointing to the old non-file_id path is
            # stale and will shadow the correct url_private in the viewer.
            if (file_obj.get('url_private', '').startswith('/channel/')
                    and 'from_url' in file_obj):
                file_obj.pop('from_url')
                changed = True

            file_id = file_obj.get('id', 'unknown')

            for key, value in list(file_obj.items()):
                # Only process the main URL fields; skip helpers and non-strings
                if not (key.startswith('url_private') or key.startswith('thumb')):
                    continue
                if key.endswith('_download') or key.endswith('_file'):
                    continue
                if not isinstance(value, str):
                    continue

                # Already migrated to a local path — nothing to do
                if value.startswith('/channel/'):
                    continue

                # Must be a Slack CDN https URL (possibly with ?t=... appended)
                if not value.startswith('https://'):
                    continue

                cdn_url        = strip_token(value)
                original_name  = os.path.basename(urllib.parse.urlparse(cdn_url).path)
                local_filename = f'{file_id}-{original_name}'
                dest_path      = attachments_dir / local_filename

                # ── Try 1: download fresh from Slack CDN ──
                # Always attempt this first — it is the only source we can
                # trust to give the correct file for this specific message.
                # The old backup code had a filename-collision bug that means
                # a locally saved file might belong to a different message.
                if try_download(cdn_url, dest_path, TOKEN):
                    file_obj[key] = f'/channel/{channel_name}/attachments/{local_filename}'
                    file_obj.pop('from_url', None)  # remove stale field from old fix_links.py
                    changed = True
                    print(f"  [downloaded]        {channel_name}: {local_filename}")
                    continue

                # ── Try 2a: dest already exists from a previous fix_links run ──
                # The file_id prefix makes this trustworthy even if CDN is gone.
                if dest_path.exists():
                    file_obj[key] = f'/channel/{channel_name}/attachments/{local_filename}'
                    file_obj.pop('from_url', None)  # remove stale field from old fix_links.py
                    changed = True
                    print(f"  [already on disk]   {channel_name}: {local_filename}")
                    continue

                # ── Try 2b: fall back to old-format local copy ──
                # Last resort — the file may have been saved under the old
                # collision-prone naming scheme but it's all we have left.
                local_src = find_local_copy(file_obj, key, channel_dir, cdn_url)
                if local_src:
                    attachments_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.copy2(local_src, dest_path)
                    file_obj[key] = f'/channel/{channel_name}/attachments/{local_filename}'
                    file_obj.pop('from_url', None)  # remove stale field from old fix_links.py
                    changed = True
                    print(f"  [copied from local] {channel_name}: {local_src.name} -> {local_filename}")
                    continue

                # ── Neither source available — leave JSON unchanged ──
                print(f"  [skipped]           {channel_name}: {key} ({cdn_url})")

    if changed:
        # Write back preserving the original envelope (list vs dict)
        out_data = msgs if isinstance(data, list) else data
        json_path.write_text(
            json.dumps(out_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"Patched {json_path}")
