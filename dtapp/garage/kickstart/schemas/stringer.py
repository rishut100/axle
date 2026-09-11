"""Typed request/response models for the f_stringer fetch-call-details endpoint (no raw dicts)."""
from typing import Optional

from pydantic import BaseModel


class StringerCallRequest(BaseModel):
    """POST /api/internal/fetch-call-details body."""
    query: str
    company_name: Optional[str] = None
    conversation_id: Optional[str] = None
    call_stage: str = "pre_sales"


class StringerCallResponse(BaseModel):
    """fetch-call-details → {response, contextual_id, conversation_id}."""
    response: Optional[str] = ""  # Stringer's error path can send an explicit null; caller coerces to ""
    contextual_id: Optional[str] = None
    conversation_id: Optional[str] = None
