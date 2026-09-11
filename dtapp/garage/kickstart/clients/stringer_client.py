import logging

from dtapp.garage.core.config import Environment, settings
from dtapp.garage.core.errors import ConfigError
from dtapp.garage.core.http_util import ServiceClient
from dtapp.garage.kickstart.schemas.stringer import StringerCallRequest, StringerCallResponse

logger = logging.getLogger(__name__)

# f_stringer internal API (sales-call intelligence). x-api-key == f_stringer's SELF_API_KEY.
FETCH_CALL_DETAILS_PATH = "/api/internal/fetch-call-details"
# 90s: generation is slow (LLM/RAG) but must fit the register handler's SQS visibility-timeout budget.
_TIMEOUT = 90

# Single-env (prod) Stringer: local dev → :9042; every deployed env → the one in-cluster Service.
_LOCAL_URL = "http://localhost:9042"
_STRINGER_URL = "http://stringer-svc.garage.svc.cluster.local"


def _base_url() -> str:
    return _LOCAL_URL if settings.environment == Environment.DEV else _STRINGER_URL


def _headers() -> dict:
    return {"x-api-key": settings.stringer_api_key}


class StringerClient:
    """f_stringer internal-API client. Inject `service_client` for tests."""

    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Stringer", base_url=_base_url, headers_fn=_headers, timeout=_TIMEOUT)

    def fetch_pre_sales_context(self, company_name: str, query: str) -> str:
        """Ask f_stringer's pre-sales agent to build a brief from `company_name`'s sales calls.
        Returns the generated markdown (the agent's `response`); "" if the agent produced nothing.
        Raises ServiceError on any non-2xx (caller treats it as best-effort)."""
        if not settings.stringer_api_key:
            raise ConfigError("Stringer: stringer_api_key not configured")
        req = StringerCallRequest(query=query, company_name=company_name, call_stage="pre_sales")
        logger.info("[kickstart] Stringer fetch-call-details request company=%s", company_name)
        raw = self._client.post(FETCH_CALL_DETAILS_PATH, json=req.model_dump())
        resp = StringerCallResponse.model_validate(raw or {}).response or ""  # tolerate an explicit null
        logger.info("[kickstart] Stringer fetch-call-details response company=%s chars=%d", company_name, len(resp))
        return resp


stringer_client = StringerClient()
