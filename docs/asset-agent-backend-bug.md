# Asset Agent — Known Backend Bug (ID Counter Stuck)

**Backend repo:** `asset-agent-s4tw.onrender.com` (separate codebase, not this repo)
**Discovered:** 2026-05-06 while seeding portfolio data for VIP Orchestrator integration.

## Symptom

`POST /api/manage/properties`, `POST /api/manage/tenants`, `POST /api/manage/leases`,
and the lease/tenant rows of `POST /api/upload/excel` all fail with HTTP 500:

```
duplicate key value violates unique constraint "tenants_tenant_id_key"
DETAIL:  Key (tenant_id)=(T-060) already exists.
```

The auto-generated id is **stuck at the same value across every retry** — 20+
back-to-back POST calls all generate `T-060`. The counter does not advance even
when the insert fails.

The same pattern shows up for properties (`P-012`) and leases.

## Root cause (likely)

The backend computes the next `tenant_id` via something like:

```python
next_id = f"T-{Tenant.query.filter_by(org_id=org_id).count() + 1:03d}"
```

…rather than a database `SEQUENCE`. When the global unique constraint
`tenants_tenant_id_key` is on the column alone (not `(org_id, tenant_id)`),
old rows from any org occupy `T-060` while this org sees zero tenants — so
the count-based generator perpetually computes a colliding ID.

Note: **properties + units uploaded via `/api/upload/excel` succeeded** (8 each),
so the property + unit code paths use a different id-generator that handles
conflicts. Only the tenant + lease paths are stuck.

## Fix in the asset-agent backend

One of:

1. Replace the count-based ID generator with a Postgres `SEQUENCE`:
   ```sql
   CREATE SEQUENCE tenants_id_seq;
   ALTER TABLE tenants ALTER COLUMN tenant_id SET DEFAULT 'T-' || lpad(nextval('tenants_id_seq')::text, 3, '0');
   ```
2. Or change the unique constraint to `(org_id, tenant_id)` so each org has its
   own ID namespace.
3. Or compute next ID as `MAX(CAST(SUBSTRING(tenant_id, 3) AS INT)) + 1` so it
   skips past existing rows.

Same fix needed for `leases_lease_id_key` / contracts.

## Workaround in VIP Orchestrator (current)

Since the backend bug blocks lease/tenant creation, VIP gets full asset data via
the CSV adapter path:

- `data/uploads/asset/latest.csv` — user's portfolio (8 properties, monthly income, occupancy)
- `UPLOADED_DATA_ENABLED=true` in `.env`
- Adapter routing prefers CSV when the file exists → `CsvAssetAdapter`
- Real backend at asset-agent-s4tw.onrender.com remains the secondary source
  (8 properties + 8 units already seeded via Excel upload — visible in the
  asset agent UI even though leases failed)

When the backend bug is fixed:

1. Run `python scripts/seed_asset_agent.py --reseed` to populate properties +
   tenants + leases properly.
2. Optionally remove `data/uploads/asset/latest.csv` so the adapter falls
   through to `RealAssetAdapter` and pulls live data from the backend.

## Verification path

```bash
# Confirm bug still present
python scripts/seed_asset_agent.py --check

# Try creating one tenant — should fail with T-{nnn} collision
curl -X POST https://asset-agent-s4tw.onrender.com/api/manage/tenants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","credit_grade":"A"}'
```
