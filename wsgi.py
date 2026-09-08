"""Production WSGI entry point.

Run with the pinned deployment server configured by the environment, for example:
    gunicorn --config /opt/sar-workbench/deploy/gunicorn.conf.py wsgi:app
"""

from app import app

__all__ = ["app"]
