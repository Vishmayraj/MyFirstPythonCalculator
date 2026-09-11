"""
shared.db.constants
---------------------
Fixed, well-known IDs that more than one part of the codebase needs
to agree on without a lookup query.

SENTINEL_GRID_SYSTEM_ID: the vms_systems row representing the main
Sentinel camera grid itself (the one model2_analytics/app/ingestion/
catalogue.py polls from https://cctv.corp8.cloud). It exists so the
grid is just another row in vms_systems — on equal footing with the
Police/RTO/Municipal VMS adapters in model3_federation, which
self-register their own vms_systems rows at runtime — instead of
being a NULL/implicit special case. shared/db/seed.sql inserts this
row with this exact id; the poller references the same constant so
it never has to look the row up.
"""

SENTINEL_GRID_SYSTEM_ID = "a1000000-0000-0000-0000-000000000000"
