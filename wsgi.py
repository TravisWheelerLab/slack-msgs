"""
WSGI entry point for slack-export-viewer under gunicorn.
Run from /home/exouser/slack-msgs with:

    gunicorn --workers 1 --bind 0.0.0.0:5000 \
        --certfile /home/exouser/tehub/caddy/caddy/certificates/acme-v02.api.letsencrypt.org-directory/tehub.org/tehub.org.crt \
        --keyfile  /home/exouser/tehub/caddy/caddy/certificates/acme-v02.api.letsencrypt.org-directory/tehub.org/tehub.org.key \
        wsgi:application
"""

from slackviewer.app import app
from slackviewer.config import Config
from slackviewer.main import configure_app

config = Config({
    'archive':                  'backup/',
    'port':                     5000,
    'ip':                       '0.0.0.0',
    'no_browser':               True,
    'channels':                 None,
    'no_sidebar':               False,
    'no_external_references':   False,
    'test':                     False,
    'debug':                    False,
    'output_dir':               'html_output',
    'html_only':                False,
    'since':                    None,
    'show_dms':                 True,
    'thread_note':              True,
    'skip_channel_member_change': True,
    'hide_channels':            None,
})

configure_app(app, config)

application = app
