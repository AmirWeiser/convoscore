"""One-off documented cleanup for two specific rows that predate the Phase 7
worker fixes (b7415593, 2fafd28a) - orphaned at 'pending' by a worker whose
connection pool never recovered from a transient DNS blip during earlier
debugging (see DECISIONS.md). Marked 'failed' with an explicit category
rather than deleted, so they read as documented historical artifacts, not
current failures, and not silently erased either.

Run once via: kubectl exec deployment/convoscore-api -- sh -c
  "PYTHONPATH=/app python /tmp/reset_orphaned_pending.py"
(after `kubectl cp` this file into the pod's /tmp).

This is not needed going forward - the Phase 7/8 fixes (pool.open(wait=True),
bounded timeouts, the outer exception handler) prevent this class of
orphaned row from recurring.
"""

import uuid

from app.db import repository
from app.db.pool import init_schema

ORPHANED_IDS = [
    "b7415593-fb0d-45bc-99a8-9398f0d4a63a",
    "2fafd28a-aaf1-4630-8ca9-2f01ffd691f5",
]

init_schema()
for id_str in ORPHANED_IDS:
    repository.mark_failed(
        uuid.UUID(id_str),
        "transient_exhausted",
        "Orphaned pre-Phase-7-fix debug artifact, manually reset - "
        "see scripts/reset_orphaned_pending.py and DECISIONS.md.",
    )
    print(f"marked {id_str} as failed (documented historical artifact)")
