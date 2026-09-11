import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from dtapp.garage.kickstart.enums import (
    ChannelOfCommunication,
    Currency,
    DateFormat,
    FiscalYear,
    IndiaSupportCommitment,
    MigratingFrom,
    PocDone,
    WeekStart,
    YesNo,
)


def _normalize_url(v: str, err: str) -> str:
    # AEs often type a bare domain (acme.com) rather than paste a full URL — accept that by
    # prepending https:// when no scheme is present, then require a valid host. Raises ValueError(err)
    # on a malformed value. Shared by CompanyDetails.company_website_url + poc_model_files items (DRY).
    v = v.strip()
    if "://" not in v:
        v = "https://" + v
    p = urlparse(v)
    try:
        _ = p.port  # property access validates the port — a malformed one (":notaport") raises
    except ValueError:
        raise ValueError(err) from None
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(err)
    return v


def lower_email(v: str) -> str:
    """The single email canonicalizer: trim + lowercase so storage / dedup / Slack-lookup are
    case-insensitive. Used by the LowerEmail field type AND the server-set registered_by / consultant."""
    return v.strip().lower() if isinstance(v, str) else v


# Every register/intake email field uses this so the DB always stores lowercase.
LowerEmail = Annotated[str, AfterValidator(lower_email)]


class _RequestModel(BaseModel):
    # trim leading/trailing whitespace on every str field (blank-only then fails min_length)
    model_config = ConfigDict(str_strip_whitespace=True)


class ConnectorEntry(_RequestModel):
    """One requested connector — free text (not a Drive-catalog pick) so AEs can capture connectors
    that aren't live in Drive yet. Each entry raises its own Linear for the connectors team."""
    connector_name: str = Field(min_length=1)
    context: Optional[str] = None  # optional — specifics for this connector relevant to the tenant


class CompanyDetails(_RequestModel):
    """Register form section 1 — the customer company + its onboarding contact + POC tenant."""
    company_name: str = Field(min_length=1)
    time_zone: str = Field(min_length=1)
    company_website_url: str = Field(min_length=1)       # required, valid http(s) URL
    onboarding_contact_name: str = Field(min_length=1)   # the contact the intake invite greets
    onboarding_contact_email: LowerEmail = Field(min_length=1)  # intake invite is sent here (lowercased)
    poc_tenant_id: Optional[str] = None       # the demo/POC tenant to clean up at register
    # channel_of_communication is the validated app-level enum (Slack / Teams). This is the EXTERNAL
    # customer channel; the internal Slack channel is created post-tenant, not named here.
    channel_of_communication: ChannelOfCommunication
    external_channel_created: YesNo  # has the external customer channel already been created? (Yes/No)
    external_channel_name: Optional[str] = None  # required (non-empty) when external_channel_created is Yes
    requires_eu_hosting: YesNo  # does this customer require EU data hosting? (Yes/No) — flows to Drive tenant-create

    @field_validator("company_website_url")
    @classmethod
    def _valid_website(cls, v: str) -> str:
        return _normalize_url(v, "company_website_url must be a valid website (e.g. acme.com)")

    @model_validator(mode="after")
    def _external_channel_name_consistency(self):
        # Name required when the channel exists; dropped otherwise — no stale name persists against "No".
        if self.external_channel_created == YesNo.YES:
            if not (self.external_channel_name or "").strip():
                raise ValueError("external_channel_name is required when the external channel already exists")
        else:
            self.external_channel_name = None
        return self


class DealDetails(_RequestModel):
    """Register form section 2 — the deal timeline + connectors + order form."""
    hubspot_deal_id: int = Field(gt=0)  # required; the HubSpot deal this kickoff maps to
    kickoff_date: date            # booked kickoff date (past dates allowed — no future-only constraint)
    contract_start_date: date     # contract start date
    connectors: List[ConnectorEntry] = Field(min_length=1)  # ≥1 required; one Linear raised per entry
    hard_commitments: List[str] = []  # optional deal-level commitments (features/timelines/SLAs); 0 allowed


class RegisterPayload(_RequestModel):
    """AE register form — grouped into three readable sections, which is ALSO the stored shape of these
    keys in kickoff.data (company_details / deal_details / sales_handoff). Flat reads go through
    flatten_groups()."""
    company_details: CompanyDetails
    deal_details: DealDetails
    # Sales → CS handoff checklist is mandatory — no register without it.
    sales_handoff: "SalesHandoffChecklist"


class KickoffCreated(BaseModel):
    koid: str


class AcademyUser(_RequestModel):
    """An academy invitee — first/last name + email, all relayed in the Academy Linear."""
    first_name: str = Field(min_length=1)
    last_name: Optional[str] = None
    email: LowerEmail = Field(min_length=1)


class IntakeSubmission(_RequestModel):
    # no time_zone — AE sets it on register; here it'd merge None over the AE's value
    domain_name: str = Field(min_length=1)
    initial_user_email: LowerEmail = Field(min_length=1)  # required — Drive admin.email (lowercased; no POC fallback)
    initial_user_first_name: str = Field(min_length=1)  # required — initial admin's name (Drive admin.firstName)
    initial_user_last_name: Optional[str] = None
    fiscal_year: FiscalYear  # required
    week_start: WeekStart  # required (goes to Drive config.firstDayOfWeek)
    currency: Currency  # required (Drive config.currency; validated against Drive's Currency enum)
    date_format: DateFormat  # required (Drive config.dateFormat; must be an accepted pattern)
    academy_users: List[AcademyUser] = []  # name + email per invitee

    @field_validator("domain_name")
    @classmethod
    def _valid_subdomain(cls, v: str) -> str:
        """Workspace subdomain rules: lowercase letters/digits with single hyphens BETWEEN words
        (acme, acme-inc) — no spaces, no special chars except '-', and no leading/trailing/double
        hyphens. Normalized to lowercase; max 63 chars (DNS label limit)."""
        v = (v or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", v):
            raise ValueError(
                "Workspace address: lowercase letters, numbers and single hyphens only (e.g. acme-inc) "
                "— no spaces or special characters, and no leading/trailing/double hyphens."
            )
        if len(v) > 63:
            raise ValueError("Workspace address must be 63 characters or fewer.")
        return v


class AssignPayload(_RequestModel):
    consultant: str = Field(min_length=1)
    analyst: Optional[str] = None    # optional — same pool; may repeat another role
    pod_lead: Optional[str] = None


class KickoffDateEdit(_RequestModel):
    kickoff_date: date  # only field editable post-register (past dates allowed — can be pushed)


# ── Sales → CS Handoff Checklist (AE register form; persisted in data["handoff_checklist"]) ──
# Per the Notion template: every always-applicable field is required (AE writes "N/A"/"None"
# where genuinely not applicable); conditional sub-fields are optional.

class HandoffKeyCustomerDetails(_RequestModel):
    purchase_reason: str = Field(min_length=1)            # paragraph: pain point, trigger, 3–6mo outcome
    migrating_from: MigratingFrom                         # Excel / Another tool / Not migrating
    migration_details: Optional[str] = None               # if another tool — which + why
    india_support_concerns: str = Field(min_length=1)     # paragraph ("None" if no concern raised)
    india_support_commitment: Optional[IndiaSupportCommitment] = None  # if concerns raised


class HandoffStakeholderMap(_RequestModel):
    economic_buyer: str = Field(min_length=1)
    champion: str = Field(min_length=1)
    primary_poc: str = Field(min_length=1)
    other_users: str = Field(min_length=1)
    demoed_users: str = Field(min_length=1)
    driveway_call_users: str = Field(min_length=1)
    skeptics: str = Field(min_length=1)
    persona_notes: str = Field(min_length=1)


class HandoffPocDetails(_RequestModel):
    poc_done: PocDone                                     # Yes / No
    poc_dt_team: Optional[str] = None                     # (if POC) Drivetrain side
    poc_customer_team: Optional[str] = None               # (if POC) customer side
    poc_outcomes: Optional[str] = None                    # (if POC) paragraph
    poc_model_files: Optional[List[str]] = None           # (if POC) links to model files shared with us; valid URLs — optional
    # NOTE: poc_tenant_link + poc_access_removed dropped in v3 — redundant (poc_tenant_id captured +
    # non-DT users are always removed from the demo/POC tenant during register cleanup).

    @field_validator("poc_model_files", mode="before")
    @classmethod
    def _valid_model_files(cls, v):
        # Back-compat: old records stored a single string — coerce to a one-element list. A list has
        # each item stripped, blanks dropped, and every survivor URL-validated (same lenient rule as
        # company_website_url). Empty → None; None stays None.
        if v is None:
            return None
        items = [v] if isinstance(v, str) else v
        if not isinstance(items, list):
            raise ValueError("poc_model_files must be a list of URLs")
        out = []
        for item in items:
            # Reject non-string items — stringifying (123 → "https://123") would pass the lenient
            # scheme/netloc check and persist a fake "URL".
            if not isinstance(item, str):
                raise ValueError("Each model-file entry must be a URL string")
            s = item.strip()
            if not s:
                continue
            out.append(_normalize_url(s, "Each model-file entry must be a valid URL (e.g. https://…)"))
        return out or None

    @model_validator(mode="after")
    def _poc_details_consistency(self):
        # POC fields required when a POC was done; dropped otherwise — no stale POC data against "No".
        if self.poc_done == PocDone.YES:
            missing = [f for f in ("poc_dt_team", "poc_customer_team", "poc_outcomes")
                       if not (getattr(self, f) or "").strip()]
            if missing:
                raise ValueError("POC details required when a POC was done: " + ", ".join(missing))
        else:
            self.poc_dt_team = self.poc_customer_team = self.poc_outcomes = self.poc_model_files = None
        return self


class HandoffContractCommitments(_RequestModel):
    delayed_kickoff: str = Field(min_length=1)            # paragraph
    opt_out_clause: str = Field(min_length=1)             # paragraph
    soft_commitments: str = Field(min_length=1)           # paragraph


class HandoffRiskFlags(_RequestModel):
    system_migrations: str = Field(min_length=1)          # paragraph ("None" if no risk)
    data_readiness: str = Field(min_length=1)
    relationship_fragility: str = Field(min_length=1)
    org_change_risk: str = Field(min_length=1)
    anything_else: str = Field(min_length=1)


class HandoffSalesNotes(_RequestModel):
    driveway_notes: str = Field(min_length=1)             # paragraph


class SalesHandoffChecklist(_RequestModel):
    key_customer_details: HandoffKeyCustomerDetails
    stakeholder_map: HandoffStakeholderMap
    poc_details: HandoffPocDetails
    contract_commitments: HandoffContractCommitments
    risk_flags: HandoffRiskFlags
    sales_notes: HandoffSalesNotes


# Mirrors Drive's SignupDataContract.Request; field names = Drive JSON keys (no aliasing).
# All fields are required — construction fails loud if any is missing (no silent null to Drive).

class DriveAdmin(BaseModel):
    email: str
    firstName: str  # NotBlank in Drive — collected from the customer on intake
    lastName: Optional[str] = None


class DriveTenantConfig(BaseModel):
    firstDayOfWeek: str
    currency: str
    dateFormat: str


class DriveCreateRequest(BaseModel):
    admin: DriveAdmin
    subdomain: str
    customer_tenant: bool = True
    requires_eu_hosting: bool = False  # part of Drive's SignupDataContract.Request — EU data-hosting flag
    config: DriveTenantConfig


# Resolve RegisterPayload's forward ref to SalesHandoffChecklist (defined after it).
RegisterPayload.model_rebuild()


_REGISTER_GROUPS = ("company_details", "deal_details")


def flatten_groups(data: Optional[dict]) -> dict:
    """Flat READ view of a (grouped) kickoff.data blob: lift the register groups company_details +
    deal_details up to top-level and expose sales_handoff under its flat key handoff_checklist. Top-
    level intake fields (domain_name, academy_users, …) + workflow keys (references, tenant_id, …) pass
    through untouched. Returns a NEW dict; never mutates the input. Tolerant of partial drafts (missing
    groups) and of already-flat legacy blobs (no group keys → returned unchanged)."""
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    for g in _REGISTER_GROUPS:
        grp = out.pop(g, None)
        if isinstance(grp, dict):
            out.update(grp)
    sh = out.pop("sales_handoff", None)
    if isinstance(sh, dict):
        out.setdefault("handoff_checklist", sh)
    return out


# ── Editable fields (detail-page inline edit). Each maps to the stored register group it lives in;
# handoff_checklist replaces the whole sales_handoff group. Anything not here is server-rejected. ──
_EDIT_COMPANY_FIELDS = {
    "company_name", "time_zone", "company_website_url",
    "onboarding_contact_name", "onboarding_contact_email", "poc_tenant_id",
    "channel_of_communication", "external_channel_created", "external_channel_name",
    "requires_eu_hosting",
}
_EDIT_DEAL_FIELDS = {
    "hubspot_deal_id", "kickoff_date", "contract_start_date",
    "connectors", "hard_commitments",
}
EDITABLE_FIELDS = _EDIT_COMPANY_FIELDS | _EDIT_DEAL_FIELDS | {"handoff_checklist"}


def merge_and_validate_edit(current: dict, changes: dict) -> RegisterPayload:
    """Route each edited field into its stored register group, merge onto the current groups, and
    validate the merged whole with RegisterPayload (so all register invariants + cross-field rules
    re-run with no duplicated validation). Raises pydantic ValidationError (→400) on invalid input.
    Caller has already whitelisted `changes` keys against EDITABLE_FIELDS."""
    # Normalize first: flatten_groups tolerates BOTH the grouped shape AND a legacy already-flat blob,
    # so a flat record's existing fields aren't lost here. Re-group via the edit-field maps (they cover
    # every register field), then merge the changes on top.
    flat = flatten_groups(current or {})
    grouped = {
        "company_details": {k: flat[k] for k in _EDIT_COMPANY_FIELDS if k in flat},
        "deal_details": {k: flat[k] for k in _EDIT_DEAL_FIELDS if k in flat},
        "sales_handoff": dict(flat.get("handoff_checklist") or {}),
    }
    for key, val in changes.items():
        if key == "handoff_checklist":
            grouped["sales_handoff"] = val
        elif key in _EDIT_COMPANY_FIELDS:
            grouped["company_details"][key] = val
        elif key in _EDIT_DEAL_FIELDS:
            grouped["deal_details"][key] = val
    return RegisterPayload(**grouped)


# ── Typed view of the Kickoff.data JSONB blob ────────────────────────────────────────────────
# A READ-side projection of everything stored under kickoff.data. The blob is PARTIAL during drafts
# and accreted across the register → intake → provisioning lifecycle, so EVERY field is Optional with
# a safe default and unknown keys are kept (extra="allow") so the round-trip stays byte-compatible.
# Nested blobs (handoff_checklist / references / completed_steps / task_errors) are typed loosely as
# dicts on purpose: they are stored as plain JSON (enums→str, dates→ISO via model_dump(mode="json"))
# and mutated in place by kickoff_repo's JSONB write helpers — this model is for typed READ access,
# not re-validation of those nested shapes.

class KickoffData(BaseModel):
    # round-trip unknown keys untouched (forward-compat + draft-partial safety)
    model_config = ConfigDict(extra="allow")

    # ── AE register form (RegisterPayload, dumped mode="json") ──
    company_name: Optional[str] = None
    time_zone: Optional[str] = None
    order_form_asset_id: Optional[str] = None  # legacy/alt order-form ref (assets live in asset_repo)
    onboarding_contact_name: Optional[str] = None
    onboarding_contact_email: Optional[str] = None
    company_website_url: Optional[str] = None
    poc_tenant_id: Optional[str] = None
    connectors: List[Dict[str, Any]] = Field(default_factory=list)  # [{connector_name, context?}]
    hard_commitments: List[str] = Field(default_factory=list)  # deal-level commitments (moved from handoff)
    channel_of_communication: Optional[str] = None  # stored as the enum's JSON value (a str)
    external_channel_created: Optional[str] = None   # external customer channel already exists? (Yes/No)
    external_channel_name: Optional[str] = None      # its name (when external_channel_created)
    requires_eu_hosting: Optional[str] = None        # does the customer require EU hosting? (Yes/No)
    hubspot_deal_id: Optional[int] = None           # the HubSpot deal this kickoff maps to
    kickoff_date: Optional[str] = None              # ISO date str (model_dump mode="json")
    contract_start_date: Optional[str] = None       # ISO date str
    handoff_checklist: Optional[Dict[str, Any]] = None

    # ── Customer intake (IntakeSubmission, dumped mode="json") ──
    domain_name: Optional[str] = None
    initial_user_email: Optional[str] = None
    initial_user_first_name: Optional[str] = None
    initial_user_last_name: Optional[str] = None
    fiscal_year: Optional[str] = None
    week_start: Optional[str] = None
    currency: Optional[str] = None
    date_format: Optional[str] = None
    academy_users: List[Dict[str, Any]] = Field(default_factory=list)  # [{first_name, last_name?, email}]

    # ── Provisioning / workflow-state keys (written by services + kickoff_repo helpers) ──
    registered_by: Optional[str] = None
    tenant_id: Optional[int] = None
    references: Dict[str, Any] = Field(default_factory=dict)        # produced links/ids (add_reference/merge_references)
    completed_steps: Dict[str, Any] = Field(default_factory=dict)   # effectively-once step flags (mark_step_done)
    task_errors: Dict[str, Any] = Field(default_factory=dict)       # "<stage>:<key>" → message (record_task_error)
    last_error: Optional[Any] = None                                # last async-path exception diagnostic
    # legacy top-level link keys kept for back-compat alongside references{}
    intake_link: Optional[str] = None
    linear_url: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_blob(cls, data):
        """Flatten the grouped register sections (company_details / deal_details / sales_handoff) up to
        top-level so the flat typed reads above work, then drop nulls for the typed collection fields
        (a stored `references: null` must read as the empty default, not blow up validation — mirrors
        the `data.get("references") or {}` idiom the read sites used). Extras pass through untouched."""
        if isinstance(data, dict):
            data = flatten_groups(data)
            data = {k: v for k, v in data.items()
                    if not (v is None and k in ("connectors", "academy_users",
                                                 "references", "completed_steps", "task_errors"))}
        return data

    @classmethod
    def from_blob(cls, d: Optional[dict]) -> "KickoffData":
        """Typed view of a raw kickoff.data dict (None → empty). Tolerant: unknown keys round-trip."""
        return cls.model_validate(d or {})

    def to_blob(self) -> dict:
        """Back to a plain dict for persistence — preserves all keys (incl. extras + the workflow-state
        keys) and keeps explicit Nones so the JSONB shape stays byte-compatible with the raw blob."""
        return self.model_dump(exclude_none=False)


def order_form_filename(company_name: Optional[str], original: Optional[str]) -> str:
    """Canonical order-form name (over the AE's arbitrary upload name); keeps the original extension."""
    company = (company_name or "").strip()
    base = f"{company} - Order Form" if company else "Order Form"
    return f"{base}{Path((original or '').strip()).suffix.lower()}"
