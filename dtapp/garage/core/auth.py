import functools
import logging

import jwt  # PyJWT
from flask import request

from dtapp.garage.core import constants
from dtapp.garage.core.config import settings

logger = logging.getLogger(__name__)

@functools.lru_cache(maxsize=1)
def _jwks_client():
    return jwt.PyJWKClient(constants.OKTA_ISSUER.rstrip('/') + '/v1/keys')


def _verify_bearer():
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth[7:]
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key, algorithms=['RS256'],
            audience=constants.OKTA_AUDIENCE,
            issuer=constants.OKTA_ISSUER,
            options={"verify_aud": settings.is_prod},  # enforce aud in prod; dev/pilot Okta tokens may carry a different aud
        )
        return claims
    except jwt.PyJWTError as e:
        # genuine token-validation failure (expired/bad sig/aud/issuer/etc) -> 401
        logger.warning(f"[kickstart] okta verify failed: {e}")
        return None
    except Exception:
        # JWKS fetch / config / network / unexpected: do NOT mask as 401.
        # let it propagate so the error handlers return a 500 surfacing the real problem.
        logger.exception("[kickstart] okta verify error (non-token); surfacing as server error")
        raise


def require_drivetrainer(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        claims = _verify_bearer()
        if not claims:
            return {'error': 'unauthorized'}, 401
        # prefer 'email' claim; fall back to 'sub' (Okta access tokens often use sub=email)
        email = (claims.get('email') or claims.get('sub') or '').lower()
        if not email.endswith('@drivetrain.ai'):
            logger.warning(f"[kickstart] forbidden: principal={email!r} (claim keys: {list(claims.keys())})")
            return {'error': 'forbidden: drivetrainers only'}, 403
        request.principal = email
        return fn(*args, **kwargs)
    return wrapper
