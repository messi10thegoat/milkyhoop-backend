# Ticket: accounting_settings.py uses asyncpg.connect() + inline Pydantic (pre-existing Law 32)

- Filed: 2026-06-16
- Severity: backlog (pre-existing tech debt)
- Origin: Law 32 audit during P2 Quote→DP (FIX_P2_QUOTEDP) settings exposure
- File: backend/api_gateway/app/routers/accounting_settings.py

## Finding
The three settings endpoints — `GET /settings/accounting`,
`POST /settings/accounting`, `PATCH /settings/accounting` — each open a raw,
un-pooled connection via:

    async def get_db_connection():
        db_config = settings.get_db_config()
        return await asyncpg.connect(**db_config)   # <-- Law 32 violation

i.e. `conn = await get_db_connection()` + `try/finally: await conn.close()`,
NOT the pooled `async with pool.acquire() as conn:` pattern. They also declare
their Pydantic models inline in the router module rather than importing from
`schemas/`. Both are Law 32 anti-patterns.

This is PRE-EXISTING. The same module already has a correct `get_pool()` helper
(documented "Law 32") used by `create_aging_snapshot`, which proves the violation
is legacy, not introduced by P2.

## What P2 did (and did NOT do)
P2 (FIX_P2_QUOTEDP) added two settings fields
(`default_dp_percent`, `default_uang_muka_account_id`) for the quote DP default.
The new lines were added INSIDE the existing endpoints, reusing the existing
`conn`. P2 did NOT introduce any NEW `asyncpg.connect()` call and did NOT add a
new endpoint. Per owner decision (SP2 P2 #4), because only PRE-EXISTING code
violates Law 32, P2 did NOT refactor the pre-existing pattern — this backlog
ticket was filed instead. (The new DP lines themselves are Law-32-neutral:
they bind `Decimal(str(...))` for the NUMERIC column and `uuid.UUID(...)` for the
FK on the existing connection.)

## Scope of fix (when picked up)
1. Replace `get_db_connection()` usage in all three `/accounting` endpoints with
   the pooled `async with pool.acquire() as conn:` pattern (use existing
   `get_pool()`), setting `app.tenant_id` config like `create_aging_snapshot` does.
2. Move the inline `AccountingSettingsResponse` / `UpdateAccountingSettingsRequest`
   / `CreateAccountingSettingsRequest` models to `schemas/accounting_settings.py`
   (a partial schemas file already exists there).
3. Remove the `get_db_connection()` helper once unused.
4. Re-verify settings GET/POST/PATCH round-trip (incl. the DP default fields).
