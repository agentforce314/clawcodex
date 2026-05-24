"""OAuth services package.

Real OAuth flow lives in ``src/auth/oauth.py``; this package holds the
service-layer helpers (``client.get_organization_uuid``, etc.) that
mirror ``typescript/src/services/oauth/``.
"""

from src.services.oauth.client import get_organization_uuid

__all__ = ['get_organization_uuid']
