"""Typed pydantic models for Drive↔Axle internal-API responses (Kickstart namespace).

Drive returns camelCase JSON; these models alias to snake_case so consumers use typed
attribute access (no raw dict["..."]). populate_by_name lets us also build them by field
name (e.g. the dryrun create shim). Response-shape only — request payloads are unchanged.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _DriveModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class DriveTenantResponse(_DriveModel):
    """POST /tenants → {tenantId, subdomain, active}."""
    tenant_id: int = Field(alias="tenantId")
    subdomain: str
    active: bool


class DriveHealthResponse(_DriveModel):
    """GET /tenants/{id}/health → {found, tenantId, subdomain, active}."""
    found: bool
    tenant_id: Optional[int] = Field(alias="tenantId", default=None)
    subdomain: Optional[str] = None
    active: Optional[bool] = None

