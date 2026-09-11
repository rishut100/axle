import logging
import urllib.parse

from dtapp.garage.core.config import Environment, settings
from dtapp.garage.core.errors import ConfigError
from dtapp.garage.core.http_util import ServiceClient
from dtapp.garage.kickstart.schemas.drive import (
    DriveHealthResponse,
    DriveTenantResponse,
)

logger = logging.getLogger(__name__)

# Drive internal API — Kickstart namespace (axleApiKey-gated).
KICKSTART_API_BASE = "/api/v1/internal/axle/kickstart"
TENANTS_PATH = f"{KICKSTART_API_BASE}/tenants"
CLEANUP_PATH = f"{KICKSTART_API_BASE}/users/cleanup"

# Prod bypasses the public LB by hitting the in-cluster ingress-nginx and routing by Host header
# (ENG-78866; see views/internal_api_check.py).
_GC_URL = "https://400.drivetrain.ai/drive"                                            # prod (Grand Central)
_LOCAL_URL = "http://localhost:8080/drive"                                             # local dev
_INGRESS_URL = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:443"   # in-cluster ingress
_GC = urllib.parse.urlparse(_GC_URL)
# Tenant vhost domain (e.g. "drivetrain.ai"). Cleanup routes by Host: <tenantId>.<domain> so Drive's
# TenantFilter resolves + sets the tenant context from the host (no body / no in-handler tenant switch).
_TENANT_DOMAIN = _GC.netloc.split(".", 1)[1]

# Per-environment Drive endpoint: (base_url, host_override). host_override is set only where the base
# is the in-cluster ingress, which routes to the right vhost by Host header (prod). Behaviour-identical
# to the old is_prod switch: PROD → ingress + Host; every other env → local.
# TODO: staging/preprod Drive URLs unknown — needs SRE. They currently point at local (the old
# is_prod=False behaviour); wire real in-cluster/ingress URLs here once known.
_DRIVE_ENDPOINTS = {
    Environment.PROD: (_INGRESS_URL + _GC.path, _GC.netloc),
    Environment.DEV: (_LOCAL_URL, None),
    Environment.STAGING: (_LOCAL_URL, None),   # TODO: staging Drive URL unknown — needs SRE
    Environment.PREPROD: (_LOCAL_URL, None),   # TODO: preprod Drive URL unknown — needs SRE
}


def _endpoint():
    """(base_url, host_override) for the current env — env unknown falls back to PROD (fail-closed,
    matching settings.environment's default)."""
    return _DRIVE_ENDPOINTS.get(settings.environment, _DRIVE_ENDPOINTS[Environment.PROD])


def _health_path(tenant_id) -> str:
    return f"{TENANTS_PATH}/{int(tenant_id)}/health"


def _connect_base() -> str:
    return _endpoint()[0]


def _headers() -> dict:
    # use-internal: mark every axle→Drive call as internal (env-agnostic — all envs).
    h = {"apikey": settings.axle_api_key, "use-internal": "true"}
    host = _endpoint()[1]
    if host:
        h["Host"] = host  # ingress selects the vhost by Host
    return h


class DriveClient:
    """Drive internal API client (Kickstart namespace). Inject `service_client` for tests."""

    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient("Drive", base_url=_connect_base, headers_fn=_headers, timeout=30)

    def _require_config(self):
        # Base URL is a const now (is_prod-switched) — only the axleApiKey secret is env-driven.
        if not settings.axle_api_key:
            raise ConfigError("Drive: axle_api_key not configured")

    def cleanup_demo_users(self, demo_tenant_id) -> None:
        """Synchronous demo-tenant cleanup, called inside the all-or-nothing register.

        POSTs the kickstart cleanup endpoint on the demo tenant's OWN vhost
        (Host: <tenantId>.drivetrain.ai), axleApiKey-gated. Drive's TenantFilter resolves + sets the
        tenant context from the host (the same path every tenant request uses), then the handler
        soft-deletes non-@drivetrain users in that tenant's schema. No body — the tenant IS the host.
        Raises ServiceError on any non-2xx so the register transaction rolls back. Drive replies
        204 No Content (the soft-deleted count is logged Drive-side), so there's no body to parse.
        """
        self._require_config()
        self._client.post(CLEANUP_PATH, headers={"Host": f"{int(demo_tenant_id)}.{_TENANT_DOMAIN}"})
        logger.info("[kickstart] drive demo cleanup ok for tenant=%s", demo_tenant_id)

    def create_tenant(self, koid, tenant_config: dict) -> DriveTenantResponse:
        """Synchronous REST tenant create, called by Axle's provisioning consumer (Phase 2).

        POSTs the kickstart create endpoint on Drive's internal base URL (axleApiKey-gated);
        Drive runs SignupService.createTenant + startSignupWorkflow and returns
        {tenantId, subdomain, active} synchronously (tenantId = done). Create is idempotent
        on Drive via its subdomain UNIQUE constraint, so a redelivery-driven repeat is safe.
        Raises ServiceError on any non-2xx so the consumer leaves the message on Axle's queue
        for bounded redelivery (no DLQ).

        The body IS Drive's SignupDataContract.Request (admin/subdomain/config) —
        tenant_config is already that shape, so it's posted directly (no wrapper). koid stays
        Axle-side only (logging + idempotency); Drive doesn't need it in the body.
        """
        self._require_config()
        raw = self._client.post(TENANTS_PATH, json=tenant_config)
        logger.info("[kickstart] drive tenant create ok for %s", koid)
        return DriveTenantResponse.model_validate(raw)  # {tenantId, subdomain, active}

    def verify_tenant(self, tenant_id) -> DriveHealthResponse:
        """Tenant-health lookup for the post-provisioning verify (kickstart-verify-tenant).

        GETs Drive's health endpoint on the GC internal base (same proven path as create),
        apikey-gated. Always 200: {found, tenantId, subdomain, active}. A transport/5xx error propagates
        as ServiceError so the verify consumer retries the SAME attempt — GC being down is an infra blip,
        not a tenant verification failure. The healthy/match decision lives in the caller.
        """
        self._require_config()
        return DriveHealthResponse.model_validate(self._client.get(_health_path(tenant_id)))


drive_client = DriveClient()
