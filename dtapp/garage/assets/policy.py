"""Per-purpose upload policy — allowed content-types + max size.

A purpose MUST be registered here to be uploadable; presign/register reject unknown purposes
(closed registry, like WeWork OMS's FILE_UPLOAD_PURPOSE enum). New workflow → add an entry.
On top of WeWork's per-purpose routing we also enforce size + content-type (which theirs didn't).
"""
from dataclasses import dataclass
from typing import FrozenSet, Optional

_MB = 1024 * 1024


@dataclass(frozen=True)
class AssetPolicy:
    max_bytes: int
    content_types: FrozenSet[str]
    extensions: FrozenSet[str]  # allowed lower-cased filename extensions (content_type is client-spoofable)


ASSET_POLICIES = {
    # Real order forms in #gtm range ~234 KB–5.4 MB (typical ~250–380 KB; largest seen:
    # front_order_form_v2.pdf at 5.4 MB). 10 MB cap = the observed max + ~2x headroom.
    "order_form": AssetPolicy(max_bytes=10 * _MB, content_types=frozenset({"application/pdf"}),
                              extensions=frozenset({".pdf"})),
}


def get_policy(purpose: str) -> Optional[AssetPolicy]:
    """Policy for a purpose, or None if the purpose isn't registered (→ caller rejects)."""
    return ASSET_POLICIES.get(purpose)
