"""Prompt templates sent to external AI services (kept out of the query-agnostic client)."""

# Sent as the `query` to f_stringer's pre-sales agent; `{company}` = the kickoff company name.
TENANT_CONTEXT_PROMPT = """Fetch pre-sales call details for {company}.

You are preparing a Sales-to-Implementation handoff brief for {company}, who just signed with Drivetrain and is moving from the sales stage into onboarding/implementation. Using ONLY what was actually said across their pre-sales calls (discovery, demos, POC, pricing/negotiation), produce a concise, skimmable brief for the implementation team. Use these exact bold section headers:

**TL;DR** - 2-3 sentences: who they are, the core reason they bought, and the single most important thing implementation must get right.
**Source** - which calls this brief is based on (call types + dates), and explicitly note any expected call types NOT on record (e.g. security review, POC, closing).
**Company snapshot** - what they do, industry, and size/scale if mentioned.
**Why they bought** - core pain points, the trigger to buy, and the outcomes they expect in the first 3-6 months.
**Key use cases** - the specific planning, reporting, or modeling problems they want Drivetrain to solve.
**Stakeholders** - economic buyer, champion, day-to-day users/POCs, and anyone skeptical; note their priorities or persona.
**Current stack & data** - tools/systems they use or are migrating from (ERP, HRIS, CRM, Excel), key data sources, and the integrations/connectors they need.
**Commitments & expectations** - any features, timelines, integrations, or SLAs promised during the sale, plus their go-live expectations.
**Risks & watch-outs** - objections, concerns (support, data readiness, org change, budget), and anything implementation should handle carefully.

Base everything strictly on what was said in their calls. If a section was not discussed, write "Not discussed" rather than guessing. Lead with the most important points and keep each section to about 3-6 tight bullets so it fits a Slack canvas."""
