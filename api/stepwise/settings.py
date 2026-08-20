"""Configuration, and where secrets would live if this app had any.

It does not, and that is worth stating plainly rather than leaving implicit:

* **Overture Maps** is read anonymously from a public S3 bucket.
* **AWS Terrain Tiles** are public and unauthenticated.
* **OpenStreetMap raster tiles** need no key.
* **MapLibre GL** is open source and needs no token — unlike Mapbox GL, which
  is part of why it was chosen.
* The API has no authentication, because it holds no user data: a plan request
  is answered and forgotten.

So there is nothing to leak from this public repository and nothing to rotate.
What this module provides is the *mechanism*, so anything added later has an
obvious correct home instead of becoming a plaintext environment variable
because that was easier on the day.

How configuration reaches the Lambda
------------------------------------
**At deploy time, not at run time.** The deploy workflow reads the parameters
under ``/stepwise/<env>/`` from SSM — with decryption — and passes them to CDK,
which sets them as Lambda environment variables. The function itself never
calls SSM.

That is deliberate. A runtime ``GetParameter`` costs a network round trip on
every cold start, counts against SSM's throughput limits, and adds a dependency
that can fail. Reading ``os.environ`` costs nothing and cannot fail.

Everything this project writes to SSM is stored as a **SecureString**, so it is
KMS-encrypted at rest and access is auditable through CloudTrail.

The caveat, stated rather than buried
-------------------------------------
A value placed in a Lambda environment variable is readable by anyone holding
``lambda:GetFunctionConfiguration``, and appears in the CloudFormation template.
Encryption at rest in SSM does not change that once the value has been copied
out. So this path is correct for *configuration* — endpoints, feature flags,
tuning constants — and wrong for a high-value secret.

If one is ever introduced, it should not travel this way. Use Secrets Manager
or an SSM SecureString read at runtime through the Lambda Parameters and
Secrets extension, which caches locally and keeps the value out of the function
configuration. There is nothing today that needs it.
"""

from __future__ import annotations

import logging
import os
from typing import Any

LOG = logging.getLogger(__name__)

#: Parameter names that must never be echoed by :meth:`Settings.describe`, even
#: though nothing currently populates them. Belt and braces: a future addition
#: should not silently start appearing in a public health endpoint.
SENSITIVE_HINTS = ("secret", "token", "key", "password", "dsn", "credential")


class Settings:
    """Typed access to this deployment's configuration.

    Reads only the environment. Population happens at deploy time — see the
    module docstring — so this class is a pure, dependency-free lookup.
    """

    def __init__(self, environ: dict[str, str] | None = None):
        """Bind an environment mapping, defaulting to the process's own."""
        self.environ = environ if environ is not None else os.environ

    def get(self, name: str, default: str | None = None) -> str | None:
        """Fetch one configuration value by its environment variable name."""
        return self.environ.get(name, default)

    def require(self, name: str) -> str:
        """Fetch a value that must be present, or raise.

        The exception names the variable but never its value, so a
        misconfiguration cannot print a credential into a log.
        """
        value = self.environ.get(name)
        if not value:
            raise RuntimeError(
                f"required configuration {name} is not set; "
                "add it under /stepwise/<env>/ in SSM and redeploy"
            )
        return value

    @property
    def env_name(self) -> str:
        """Which environment this is."""
        return self.environ.get("ENV_NAME", "local")

    @property
    def app_version(self) -> str:
        """The git SHA this was deployed from."""
        return self.environ.get("APP_VERSION", "dev")

    @property
    def log_level(self) -> str:
        """The configured log level."""
        return self.environ.get("LOG_LEVEL", "DEBUG")

    @property
    def region_bucket(self) -> str:
        """Bucket caching on-demand regions; empty when they are disabled."""
        return self.environ.get("REGION_BUCKET", "")

    @property
    def builder_function(self) -> str:
        """Name of the region extractor; empty when it is not deployed."""
        return self.environ.get("BUILDER_FUNCTION_NAME", "")

    @property
    def cors_allow_origin(self) -> str:
        """The single origin permitted to call this API from a browser."""
        return self.environ.get("CORS_ALLOW_ORIGIN", "*")

    @property
    def site_url(self) -> str:
        """Where the browser app lives, for the root redirect.

        Falls back to the CORS origin because that is the same CloudFront
        domain by construction — the stack sets both from one value — so a
        deployment that can talk to the app can always redirect to it, even if
        SITE_URL were ever forgotten.
        """
        configured = self.environ.get("SITE_URL", "").strip()
        if configured:
            return configured
        origin = self.cors_allow_origin
        return origin if origin.startswith("https://") else ""

    @property
    def on_demand_regions(self) -> bool:
        """Whether this deployment can extract new coverage areas."""
        return bool(self.region_bucket and self.builder_function)

    def describe(self) -> dict[str, Any]:
        """Non-sensitive configuration, for the health endpoint.

        Reports *whether* things are configured rather than their values, so the
        endpoint stays safe to expose publicly.
        """
        return {
            "env": self.env_name,
            "version": self.app_version,
            "log_level": self.log_level,
            "on_demand_regions": self.on_demand_regions,
        }

    @staticmethod
    def is_sensitive(name: str) -> bool:
        """Whether a configuration name looks like it holds a secret."""
        lowered = name.lower()
        return any(hint in lowered for hint in SENSITIVE_HINTS)


#: Process-wide settings, shared across invocations on a warm container.
SETTINGS = Settings()
