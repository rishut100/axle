# Stable, non-secret Access-automation values — code constants, not env (mirrors kickstart's
# core/constants.py). Env-resolved "who/where" routing lives in recipients.py, not here.

# Axle's own access-automation SQS queue — separate from kickstart's "kickstart-provisioning" queue so
# a bulk offboarding day (many grant/revoke messages) can never back up behind kickstart's 300s
# VisibilityTimeout and starve real customer kickoffs.
ACCESS_QUEUE_NAME = "garage-access"

# Offboarding drift-verification: how long after a revoke call to re-check the tool still agrees the
# user is deactivated. Long enough for eventually-consistent SCIM backends to settle; short enough that
# a real miss is caught same-day.
REVOKE_VERIFY_DELAY_HOURS = 24

# Cron scan cadence for the verify job (registered on the shared CronScheduler in main.py). This is a
# DB-scan cron, NOT a queued SQS message type — see queue.py's module note for why.
REVOKE_VERIFY_CRON = "0 * * * *"  # hourly
REVOKE_VERIFY_JOB_ID = "access_revoke_verify"

# Onboarding-side mirror, added after real-world testing surfaced the missing grant-side check.
# Shorter delay than revoke's — most grant APIs are read-your-writes consistent within minutes, and an
# unverified "you have access" is a worse failure mode to leave open long than an unverified revoke.
GRANT_VERIFY_DELAY_HOURS = 1
GRANT_VERIFY_CRON = "30 * * * *"  # hourly, offset from the revoke scan so they don't both fire at :00
GRANT_VERIFY_JOB_ID = "access_grant_verify"

# Bounded redelivery before a grant/revoke message is dead-lettered (mirrors kickstart's
# _MAX_RECEIVE_COUNT) — a permanently-failing message must not retry forever.
MAX_RECEIVE_COUNT = 3

# The only Outbox.type values this module ever writes/relays — used to scope this module's relay sweep
# (access/sqs.py) and to tell the shared outbox table's other consumer (kickstart) which rows are ours.
OUTBOX_TYPES = ["grant", "revoke"]
