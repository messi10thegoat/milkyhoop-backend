#!/usr/bin/env python3
"""Management command: RECLASSIFY_BILL_INVENTORY (S14/S1).

Owner-no-browser path. Does a SERVER-SIDE OWNER-role check (finds the tenant's
OWNER user via user_tenant_roles) and calls the SAME service function
reclassify_bill_inventory() — NOT a raw SQL INSERT. Default = dry-run; pass
--apply to actually post.

Run inside the api_gateway container:
  docker exec milkyhoop-dev-api_gateway python \
    /app/backend/api_gateway/app/scripts/reclass_bill_inventory_cmd.py \
    --tenant grapgrap-manado --ticket TIKET-... [--apply]
"""
import argparse
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from backend.api_gateway.app.services.db_pool import get_db_pool  # noqa: E402
from backend.api_gateway.app.routers.journals import (  # noqa: E402
    reclassify_bill_inventory,
)


async def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--target", default="5-10100")
    ap.add_argument("--reason", default="Reklas otomatis oleh agen atas perintah pemilik/MASTER.")
    ap.add_argument("--ticket", default="")
    ap.add_argument("--apply", action="store_true", help="benar-benar posting (default: dry-run)")
    args = ap.parse_args()

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{args.tenant}'")
        # SERVER-SIDE OWNER CHECK (bukan mint token; bukan SQL INSERT)
        owner = await conn.fetchrow(
            """
            SELECT utr.user_id
            FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
            WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND r.is_active = TRUE
            ORDER BY utr.is_primary DESC NULLS LAST
            LIMIT 1
            """,
            args.tenant,
        )
        if not owner:
            print(json.dumps({"error": "NO_OWNER_ROLE", "tenant": args.tenant}))
            return
        res = await reclassify_bill_inventory(
            conn, args.tenant, owner["user_id"],
            target_account_code=args.target, reason=args.reason, ticket=args.ticket,
            bill_ids=None, dry_run=(not args.apply),
        )
        print(json.dumps(res, default=str))


if __name__ == "__main__":
    asyncio.run(_main())
