"""Typed pydantic models for axle's external API-client responses where the body is STRUCTURED
(multiple fields consumed). Single-scalar references (slack ts, s3 keys, monday item url) stay raw
strings — typing a lone reference string into a model adds no value and risks the stored refs.
"""
from typing import Optional

from pydantic import BaseModel


class LinearIssue(BaseModel):
    """Linear issueCreate → issue {id, identifier, url}. Consumers read .url / .id."""
    id: str
    url: str
    identifier: Optional[str] = None


class ConsultantRow(BaseModel):
    """One roster row from the consultant Google Sheet. department is "" when absent."""
    name: str
    email: str
    department: str = ""
