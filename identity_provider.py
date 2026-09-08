"""External identity-provider integration boundary.

Provider-specific OIDC/SAML exchange, signature validation, key rotation, and
session mapping remain deployment-owned. This module defines the configuration
contract so an integration cannot silently fall back to local or anonymous
access.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class IdentityProviderConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ExternalIdentityConfiguration:
    protocol: str
    issuer_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    audience: str = ""

    @classmethod
    def from_environment(cls) -> "ExternalIdentityConfiguration":
        return cls(
            protocol=os.environ.get("SAR_EXTERNAL_IDP_PROTOCOL", "oidc").strip().lower(),
            issuer_url=os.environ.get("SAR_EXTERNAL_IDP_ISSUER_URL", "").strip(),
            client_id=os.environ.get("SAR_EXTERNAL_IDP_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("SAR_EXTERNAL_IDP_CLIENT_SECRET", "").strip(),
            redirect_uri=os.environ.get("SAR_EXTERNAL_IDP_REDIRECT_URI", "").strip(),
            audience=os.environ.get("SAR_EXTERNAL_IDP_AUDIENCE", "").strip(),
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("SAR_EXTERNAL_IDP_PROTOCOL", self.protocol),
                ("SAR_EXTERNAL_IDP_ISSUER_URL", self.issuer_url),
                ("SAR_EXTERNAL_IDP_CLIENT_ID", self.client_id),
                ("SAR_EXTERNAL_IDP_CLIENT_SECRET", self.client_secret),
                ("SAR_EXTERNAL_IDP_REDIRECT_URI", self.redirect_uri),
            )
            if not value
        ]
        if missing:
            raise IdentityProviderConfigurationError(
                "External identity provider configuration is incomplete: " + ", ".join(missing)
            )
        if self.protocol not in {"oidc", "saml"}:
            raise IdentityProviderConfigurationError("SAR_EXTERNAL_IDP_PROTOCOL must be oidc or saml")
        issuer = urlparse(self.issuer_url)
        redirect = urlparse(self.redirect_uri)
        if issuer.scheme != "https" or not issuer.netloc:
            raise IdentityProviderConfigurationError("SAR_EXTERNAL_IDP_ISSUER_URL must be an HTTPS URL")
        if redirect.scheme != "https" or not redirect.netloc:
            raise IdentityProviderConfigurationError("SAR_EXTERNAL_IDP_REDIRECT_URI must be an HTTPS URL")


def external_identity_boundary_status() -> dict[str, str | bool]:
    """Return safe status metadata without exposing provider secrets."""
    configuration = ExternalIdentityConfiguration.from_environment()
    try:
        configuration.validate()
    except IdentityProviderConfigurationError as exc:
        return {
            "configured": False,
            "protocol": configuration.protocol or "unknown",
            "status": "not_ready",
            "reason": str(exc),
            "implementation": "deployment_boundary_only",
        }
    return {
        "configured": True,
        "protocol": configuration.protocol,
        "status": "configuration_ready",
        "implementation": "deployment_boundary_only",
    }
