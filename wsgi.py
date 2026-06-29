"""
WSGI entry point for slack-export-viewer under gunicorn.
Run from /home/exouser/slack-msgs with:

    gunicorn --workers 1 --bind 127.0.0.1:5000 wsgi:application
"""

import base64
import json
import os
import time

import flask
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.wrappers import Request, Response

from slackviewer.app import app
from slackviewer.config import Config
from slackviewer.main import configure_app

# ---------------------------------------------------------------------------
# App configuration
# ---------------------------------------------------------------------------

config = Config({
    'archive':                    'backup/',
    'port':                       5000,
    'ip':                         '0.0.0.0',
    'no_browser':                 True,
    'channels':                   None,
    'no_sidebar':                 False,
    'no_external_references':     False,
    'test':                       False,
    'debug':                      False,
    'output_dir':                 'html_output',
    'html_only':                  False,
    'since':                      None,
    'show_dms':                   True,
    'thread_note':                True,
    'skip_channel_member_change': True,
    'hide_channels':              None,
})

configure_app(app, config)

# Prefer templates/ in this directory so we can extend the UI without
# touching the installed package.
app.jinja_env.loader = ChoiceLoader([
    FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')),
    app.jinja_env.loader,
])


def build_thread_groups(messages):
    """
    Group a flat, chronologically-sorted list of Message objects into threads.

    Slack's backup format links replies to their root via thread_ts on each
    message rather than embedding a 'replies' array, so the installed
    _build_threads cannot handle it.  This function does the grouping at
    render time using thread_ts.

    Returns a list of (root_message, [reply_messages]) tuples ordered by the
    timestamp of the root message.  Reply Message objects have is_thread_msg
    set to True.
    """
    from collections import OrderedDict

    roots = OrderedDict()   # ts -> (root_msg, [replies])
    orphans = []            # (thread_ts, reply_msg) where root not yet seen

    for msg in messages:
        raw = msg._message
        ts = raw.get('ts', '')
        thread_ts = raw.get('thread_ts', '')

        if not thread_ts or thread_ts == ts:
            roots[ts] = (msg, [])
        else:
            msg.is_thread_msg = True
            if thread_ts in roots:
                roots[thread_ts][1].append(msg)
            else:
                orphans.append((thread_ts, msg))

    for thread_ts, msg in orphans:
        if thread_ts in roots:
            roots[thread_ts][1].append(msg)
        else:
            roots[msg._message.get('ts', '')] = (msg, [])

    return list(roots.values())


app.jinja_env.globals['build_thread_groups'] = build_thread_groups

# Build channel metadata mapping for Slack deep links (name → {id, team_id})
_channels_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backup', 'channels.json')
_channel_meta = {}
try:
    with open(_channels_json_path, encoding='utf-8') as _f:
        for _ch in json.load(_f):
            _channel_meta[_ch['name']] = {
                'id': _ch.get('id', ''),
                'team_id': _ch.get('context_team_id', ''),
            }
except Exception:
    pass
app.jinja_env.globals['channel_meta'] = _channel_meta


@app.route("/search")
def search():
    q = flask.request.args.get('q', '').strip()
    results = []
    if q:
        q_lower = q.lower()
        ctx = flask._app_ctx_stack
        for bucket_attr, result_type in [('channels', 'channel'), ('groups', 'group')]:
            for name, messages in sorted((getattr(ctx, bucket_attr, None) or {}).items()):
                for msg in messages:
                    if q_lower in (msg._message.get('text') or '').lower():
                        results.append((result_type, name, msg))
                        if len(results) >= 500:
                            break
                if len(results) >= 500:
                    break
            if len(results) >= 500:
                break
        if len(results) < 500:
            for dm_id, messages in sorted((getattr(ctx, 'dms', None) or {}).items()):
                for msg in messages:
                    if q_lower in (msg._message.get('text') or '').lower():
                        results.append(('dm', dm_id, msg))
                        if len(results) >= 500:
                            break
                if len(results) >= 500:
                    break
        if len(results) < 500:
            for mpim_name, messages in sorted((getattr(ctx, 'mpims', None) or {}).items()):
                for msg in messages:
                    if q_lower in (msg._message.get('text') or '').lower():
                        results.append(('mpim', mpim_name, msg))
                        if len(results) >= 500:
                            break
                if len(results) >= 500:
                    break

    return flask.render_template(
        'search_results.html',
        query=q,
        results=results,
        channels=sorted((getattr(flask._app_ctx_stack, 'channels', None) or {}).keys()),
        groups=sorted((getattr(flask._app_ctx_stack, 'groups', None) or {}).keys()),
        dm_users=list(getattr(flask._app_ctx_stack, 'dm_users', None) or []),
        mpim_users=list(getattr(flask._app_ctx_stack, 'mpim_users', None) or []),
        no_sidebar=app.no_sidebar,
        no_external_references=app.no_external_references,
        viewer_css_contents=None,
    )


# ---------------------------------------------------------------------------
# Wiki.js JWT authentication middleware
#
# Wiki.js sets a 'jwt' cookie on tehub.org.  Because browsers send cookies
# to all ports on the same domain, this cookie arrives automatically at
# tehub.org:5000.  The JWT payload contains an 'exp' (expiry) Unix timestamp
# that we decode directly from the base64 payload — no network call needed.
#
# Note: the RS256 signature is NOT verified here.  That is acceptable because
# the jwt cookie can only be set by the tehub.org server itself, so a forged
# token would require the ability to set cookies on that domain.
# ---------------------------------------------------------------------------

def _jwt_is_valid(jwt: str) -> bool:
    """
    Decode the JWT payload and check that the token has not expired.
    Returns True if valid and unexpired, False on any error or expiry.
    """
    try:
        # JWT structure: header.payload.signature
        payload_b64 = jwt.split('.')[1]
        # urlsafe base64 — pad to a multiple of 4 bytes
        padding     = '=' * (4 - len(payload_b64) % 4)
        payload     = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return payload.get('exp', 0) > time.time() - 7200  # 2-hour grace period
    except Exception:
        return False


def _deny(environ, start_response):
    """Explain the session situation and give the user a clear path back."""
    body = (
        '<html><head><meta charset="UTF-8">'
        '<title>Session refresh needed</title>'
        '<style>'
        'body{font-family:sans-serif;padding:3em;max-width:480px;margin:auto}'
        'a.btn{display:inline-block;margin-top:1em;padding:.6em 1.2em;background:#4C9689;'
        'color:#fff;border-radius:4px;text-decoration:none}'
        'a.btn:hover{background:#3d7870}'
        'p{color:#444;line-height:1.5}'
        '</style></head>'
        '<body>'
        '<h2>Session refresh needed</h2>'
        '<p>Your session token has expired. Click below to log in to TE Hub &mdash; '
        "if you are already logged in you will be redirected back automatically. "
        "Then use your browser's back button to return to the archive.</p>"
        '<a class="btn" href="https://tehub.org/login">Log in to TE Hub</a>'
        '</body></html>'
    )
    response = Response(body, status=401, content_type='text/html; charset=utf-8')
    return response(environ, start_response)


ARCHIVE_PREFIX = os.environ.get('ARCHIVE_PREFIX', '/slack_archive')


class PrefixMiddleware:
    """Set SCRIPT_NAME so Flask generates correct URLs when mounted at a subpath."""
    def __init__(self, wsgi_app, prefix):
        self.wsgi_app = wsgi_app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        environ['SCRIPT_NAME'] = self.prefix
        return self.wsgi_app(environ, start_response)


class WikiJSAuthMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        jwt = Request(environ).cookies.get('jwt')
        if not jwt or not _jwt_is_valid(jwt):
            return _deny(environ, start_response)
        return self.wsgi_app(environ, start_response)


application = WikiJSAuthMiddleware(PrefixMiddleware(app, ARCHIVE_PREFIX))
