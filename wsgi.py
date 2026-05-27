"""
WSGI entry point for slack-export-viewer under gunicorn.
Run from /home/exouser/slack-msgs with:

    gunicorn --workers 1 --bind 0.0.0.0:5000 \
        --certfile /home/exouser/tehub/caddy/caddy/certificates/acme-v02.api.letsencrypt.org-directory/tehub.org/tehub.org.crt \
        --keyfile  /home/exouser/tehub/caddy/caddy/certificates/acme-v02.api.letsencrypt.org-directory/tehub.org/tehub.org.key \
        wsgi:application
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
    """Return a 403 with a link back to Wiki.js to log in / refresh session."""
    response = Response(
        '<html><body style="font-family:sans-serif;padding:2em">'
        '<h2>Access denied</h2>'
        '<p>Please <a href="https://tehub.org">log in to tehub.org</a> '
        'first, then return to this page.</p>'
        '</body></html>',
        status=403,
        content_type='text/html',
    )
    return response(environ, start_response)


class WikiJSAuthMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        jwt = Request(environ).cookies.get('jwt')
        if not jwt or not _jwt_is_valid(jwt):
            return _deny(environ, start_response)
        return self.wsgi_app(environ, start_response)


application = WikiJSAuthMiddleware(app)
