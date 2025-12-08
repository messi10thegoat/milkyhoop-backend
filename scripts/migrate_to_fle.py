#!/usr/bin/env python3
"""
FLE Migration Script
====================
Migrates existing plaintext data to encrypted format.

Usage:
    python migrate_to_fle.py --dry-run  # Preview changes
    python migrate_to_fle.py            # Execute migration

Requirements:
    - FLE_PRIMARY_KEK environment variable must be set
    - Database connection via DATABASE_URL
"""
import os
import sys
import asyncio
import argparse
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import asyncpg

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend', 'api_gateway', 'app'))

from services.crypto.fle_service import get_fle, encrypt_field
from services.crypto.encrypted_fields import get_blind_index

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Migration configuration
MIGRATIONS = [
    {
        "table": "User",
        "id_column": "id",
        "fields": [
            {"source": "email", "target": "email_encrypted", "blind_index": "email_blind_index"},
            {"source": "name", "target": "name_encrypted"},
            {"source": "fullname", "target": "fullname_encrypted"},
            {"source": "nickname", "target": "nickname_encrypted"},
        ],
        "batch_size": 100
    },
    {
        "table": "UserProfile",
        "id_column": "id",
        "fields": [
            {"source": "phoneNumber", "target": "phone_number_encrypted", "blind_index": "phone_blind_index"},
            {"source": "digitalSignature", "target": "digital_signature_encrypted"},
        ],
        "batch_size": 100
    },
    {
        "table": "UserBusiness",
        "id_column": "businessId",
        "fields": [
            {"source": "taxId", "target": "tax_id_encrypted"},
            {"source": "businessLicense", "target": "business_license_encrypted"},
        ],
        "batch_size": 100
    },
    {
        "table": "transaksi_harian",
        "id_column": "id",
        "fields": [
            {"source": "nama_pihak", "target": "nama_pihak_encrypted", "blind_index": "nama_pihak_blind_index"},
            {"source": "kontak_pihak", "target": "kontak_pihak_encrypted"},
        ],
        "batch_size": 500
    },
    {
        "table": "suppliers",
        "id_column": "id",
        "fields": [
            {"source": "kontak", "target": "kontak_encrypted"},
        ],
        "batch_size": 100
    },
    {
        "table": "Order",
        "id_column": "id",
        "fields": [
            {"source": "customer_name", "target": "customer_name_encrypted"},
        ],
        "batch_size": 100
    },
]


class FLEMigrator:
    """Handles batch encryption of existing data"""

    def __init__(self, pool: asyncpg.Pool, dry_run: bool = False):
        self.pool = pool
        self.dry_run = dry_run
        self.fle = get_fle()
        self.stats = {
            "total": 0,
            "migrated": 0,
            "skipped": 0,
            "failed": 0
        }

    async def migrate_all(self):
        """Run all migrations"""
        logger.info("=" * 60)
        logger.info("FLE Migration Started")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info("=" * 60)

        for config in MIGRATIONS:
            await self.migrate_table(config)

        self._print_summary()

    async def migrate_table(self, config: Dict[str, Any]):
        """Migrate a single table"""
        table = config["table"]
        id_column = config["id_column"]
        fields = config["fields"]
        batch_size = config.get("batch_size", 100)

        logger.info(f"\n--- Migrating {table} ---")

        # Update migration status
        await self._update_status(table, fields, "in_progress")

        # Build SELECT columns
        source_cols = [f'"{f["source"]}"' for f in fields]
        select_cols = f'"{id_column}", ' + ", ".join(source_cols)

        # Count total records
        async with self.pool.acquire() as conn:
            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"')
            logger.info(f"Total records: {count}")

        offset = 0
        table_migrated = 0
        table_failed = 0

        while True:
            async with self.pool.acquire() as conn:
                # Fetch batch
                query = f"""
                    SELECT {select_cols}
                    FROM "{table}"
                    WHERE "encryption_version" = 0 OR "encryption_version" IS NULL
                    ORDER BY "{id_column}"
                    LIMIT {batch_size} OFFSET {offset}
                """

                try:
                    rows = await conn.fetch(query)
                except Exception as e:
                    logger.error(f"Query failed: {e}")
                    break

                if not rows:
                    break

                # Process batch
                for row in rows:
                    record_id = row[id_column]
                    success = await self._migrate_record(conn, table, id_column, record_id, fields, row)

                    if success:
                        table_migrated += 1
                        self.stats["migrated"] += 1
                    else:
                        table_failed += 1
                        self.stats["failed"] += 1

                    self.stats["total"] += 1

                offset += batch_size

                # Progress
                if table_migrated % 500 == 0:
                    logger.info(f"Progress: {table_migrated}/{count}")

        logger.info(f"Completed {table}: {table_migrated} migrated, {table_failed} failed")

        # Update status
        status = "completed" if table_failed == 0 else "partial"
        await self._update_status(table, fields, status, table_migrated, table_failed)

    async def _migrate_record(
        self,
        conn: asyncpg.Connection,
        table: str,
        id_column: str,
        record_id: str,
        fields: List[Dict],
        row: asyncpg.Record
    ) -> bool:
        """Migrate a single record"""

        updates = []
        values = [record_id]
        param_idx = 2

        for field_config in fields:
            source = field_config["source"]
            target = field_config["target"]
            blind_index_col = field_config.get("blind_index")

            value = row[source]

            if not value:
                continue

            try:
                # Encrypt the value
                aad = f"{table}.{source}"
                encrypted = encrypt_field(str(value), aad)

                updates.append(f'"{target}" = ${param_idx}')
                values.append(encrypted)
                param_idx += 1

                # Generate blind index if needed
                if blind_index_col:
                    blind_idx = get_blind_index(str(value))
                    updates.append(f'"{blind_index_col}" = ${param_idx}')
                    values.append(blind_idx)
                    param_idx += 1

            except Exception as e:
                logger.error(f"Failed to encrypt {table}.{source} for {record_id}: {e}")
                return False

        if not updates:
            self.stats["skipped"] += 1
            return True

        # Add encryption version
        updates.append(f'"encryption_version" = 1')

        # Execute update
        if not self.dry_run:
            update_query = f"""
                UPDATE "{table}"
                SET {", ".join(updates)}
                WHERE "{id_column}" = $1
            """

            try:
                await conn.execute(update_query, *values)
            except Exception as e:
                logger.error(f"Failed to update {table}.{record_id}: {e}")
                return False

        return True

    async def _update_status(
        self,
        table: str,
        fields: List[Dict],
        status: str,
        migrated: int = 0,
        failed: int = 0
    ):
        """Update migration status table"""
        if self.dry_run:
            return

        async with self.pool.acquire() as conn:
            for field_config in fields:
                await conn.execute("""
                    UPDATE fle_migration_status
                    SET status = $1,
                        migrated_records = $2,
                        completed_at = CASE WHEN $1 = 'completed' THEN NOW() ELSE completed_at END
                    WHERE table_name = $3 AND field_name = $4
                """, status, migrated, table, field_config["source"])

    def _print_summary(self):
        """Print migration summary"""
        logger.info("\n" + "=" * 60)
        logger.info("MIGRATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total records processed: {self.stats['total']}")
        logger.info(f"Successfully migrated:   {self.stats['migrated']}")
        logger.info(f"Skipped (empty):         {self.stats['skipped']}")
        logger.info(f"Failed:                  {self.stats['failed']}")

        if self.dry_run:
            logger.info("\n*** DRY RUN - No changes were made ***")

        logger.info("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Migrate data to FLE")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--table", type=str, help="Migrate specific table only")
    args = parser.parse_args()

    # Validate environment
    if not os.getenv("FLE_PRIMARY_KEK"):
        logger.error("FLE_PRIMARY_KEK environment variable not set!")
        logger.error("Generate with: openssl rand -base64 32")
        sys.exit(1)

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable not set!")
        sys.exit(1)

    # Connect to database
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)

    try:
        migrator = FLEMigrator(pool, dry_run=args.dry_run)

        if args.table:
            # Migrate specific table
            config = next((m for m in MIGRATIONS if m["table"] == args.table), None)
            if config:
                await migrator.migrate_table(config)
            else:
                logger.error(f"Unknown table: {args.table}")
        else:
            # Migrate all
            await migrator.migrate_all()

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
