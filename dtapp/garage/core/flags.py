"""LaunchDarkly server-side gating (mirrors Heimdall's feature_flags pattern).

Single source of truth with the FE LD flags: the kickstart-fe button/view hiding is UX-only;
THIS is the real boundary. Tri-state per the Heimdall model:

- ALLOWED     — LD not configured (local → not enforced) OR flag not found (opt-in: no flag = open)
                OR flag evaluates True for the principal.
- DENIED      — flag exists and evaluates to a definitive False.
- UNAVAILABLE — SDK key set but client failed to init / eval error / non-bool. An infra problem,
                NOT a policy decision — still fail-closed (caller blocks), just with an honest 5xx.

Local posture: no SDK key → ALLOWED everywhere (skip in local env). `ldclient` is imported lazily
inside init so the dependency is only needed where a key is actually configured (prod).
"""
import logging
from enum import Enum
from functools import lru_cache

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ForbiddenError, ServiceError

logger = logging.getLogger(__name__)
logging.getLogger("ldclient").setLevel(logging.WARNING)

@lru_cache(maxsize=1)
def _ld():
    """Lazily init the LD client once (cached — the None result is cached too, so a failed init
    isn't retried). None if no key (local) or init failed."""
    if not settings.launchdarkly_sdk_key:
        return None
    try:
        import ldclient
        ldclient.set_config(ldclient.Config(sdk_key=settings.launchdarkly_sdk_key))
        client = ldclient.get()
        if not client.is_initialized():
            logger.error("[kickstart] LaunchDarkly client failed to initialise")
            return None
        return client
    except Exception as e:  # noqa: BLE001
        logger.error("[kickstart] LaunchDarkly init error: %s", e)
        return None


class Gate(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"  # flag not configured — opt-in (open) for feature flags; 500 for strict gates


def evaluate(flag_key: str, principal: str) -> Gate:
    """Evaluate a per-user access flag for `principal` (an email). Heimdall tri-state."""
    if not settings.launchdarkly_sdk_key:
        return Gate.ALLOWED  # local: not enforced
    client = _ld()
    if client is None:
        return Gate.UNAVAILABLE  # key set but client broken → fail-closed
    try:
        from ldclient.context import Context
        # Okta stamps email as the principal — set key + email so LD email-targeting works.
        context = Context.builder(principal).set("email", principal).build()
        detail = client.variation_detail(flag_key, context, False)
    except Exception as e:  # noqa: BLE001
        logger.error("[kickstart] LD eval error flag=%s: %s", flag_key, e)
        return Gate.UNAVAILABLE
    reason = detail.reason or {}
    if reason.get("kind") == "ERROR" and reason.get("errorKind") == "FLAG_NOT_FOUND":
        return Gate.NOT_FOUND  # no flag configured — callers decide (opt-in vs strict)
    value = detail.value
    if not isinstance(value, bool):
        return Gate.UNAVAILABLE
    return Gate.ALLOWED if value else Gate.DENIED


def allowed(flag_key: str, principal: str) -> bool:
    """True when ALLOWED or NOT_FOUND — for read-gating (include/omit), e.g. debug fields. No flag =
    open (opt-in), matching the previous behaviour where FLAG_NOT_FOUND collapsed to ALLOWED."""
    return evaluate(flag_key, principal) in (Gate.ALLOWED, Gate.NOT_FOUND)


def require_access(flag_key: str, principal: str) -> None:
    """Enforce an access flag for an action endpoint. DENIED → 403; UNAVAILABLE → 502
    (fail-closed). No-op when the flag isn't found or LD isn't configured (opt-in / local)."""
    gate = evaluate(flag_key, principal)
    if gate is Gate.DENIED:
        raise ForbiddenError("Not permitted to perform this action.")
    if gate is Gate.UNAVAILABLE:
        raise ServiceError("LaunchDarkly", "access check unavailable")


def require_access_strict(flag_key: str, principal: str) -> None:
    """Enforce an access flag for a DESTRUCTIVE action where an unknown gate must NOT fail open.
    DENIED → 403; UNAVAILABLE → 502; flag-not-found → 500 (misconfigured gate — nothing is done).
    Missing SDK key (local) still resolves to ALLOWED, so local dev/testing isn't blocked."""
    gate = evaluate(flag_key, principal)
    if gate is Gate.DENIED:
        raise ForbiddenError("Not permitted to perform this action.")
    if gate is Gate.UNAVAILABLE:
        raise ServiceError("LaunchDarkly", "access check unavailable")
    if gate is Gate.NOT_FOUND:
        raise ConfigError(f"access gate '{flag_key}' is not configured")


def feature_on(flag_key: str, default: bool = False) -> bool:
    """Service-context (no user) feature gate — used to enable background features like
    verify-tenant. Local / unconfigured → `default` (verify default False → skipped)."""
    if not settings.launchdarkly_sdk_key:
        return default
    client = _ld()
    if client is None:
        return default
    try:
        from ldclient.context import Context
        context = Context.builder("kickstart-service").kind("service").build()
        value = client.variation(flag_key, context, default)
        return value if isinstance(value, bool) else default
    except Exception as e:  # noqa: BLE001
        logger.error("[kickstart] LD feature eval error flag=%s: %s", flag_key, e)
        return default
