"""
Seed default rules for rule_engine

Creates common product mapping and tax calculation rules for tenants.

Usage:
    python seed_default_rules.py --tenant-id <tenant_id>
    python seed_default_rules.py --all  # Seed for all tenants
"""

import asyncio
import sys
sys.path.insert(0, "/app/backend/api_gateway/libs")
import argparse
import logging
import sys
import os

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from milkyhoop_prisma import Prisma
from storage.rule_repository import RuleRepository

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Default rules to seed
DEFAULT_RULES = [
    {
        "rule_id": "product_makanan",
        "rule_type": "product_mapping",
        "priority": 5,
        "rule_yaml": """
rule_id: product_makanan
priority: 5
condition:
  product_category: "makanan"
action:
  akun_pendapatan: "4-1100"
  akun_hpp: "5-1100"
  description: "Pendapatan & HPP Makanan"
"""
    },
    {
        "rule_id": "product_minuman",
        "rule_type": "product_mapping",
        "priority": 5,
        "rule_yaml": """
rule_id: product_minuman
priority: 5
condition:
  product_category: "minuman"
action:
  akun_pendapatan: "4-1200"
  akun_hpp: "5-1200"
  description: "Pendapatan & HPP Minuman"
"""
    },
    {
        "rule_id": "product_jasa",
        "rule_type": "product_mapping",
        "priority": 5,
        "rule_yaml": """
rule_id: product_jasa
priority: 5
condition:
  product_category: "jasa"
action:
  akun_pendapatan: "4-1300"
  description: "Pendapatan Jasa"
"""
    },
    {
        "rule_id": "ppn_threshold",
        "rule_type": "tax_calculation",
        "priority": 10,
        "rule_yaml": """
rule_id: ppn_threshold
priority: 10
condition:
  total_nominal: ">= 5000000"
action:
  apply_ppn: true
  ppn_rate: 0.11
  description: "PPN 11% untuk transaksi >= 5 juta"
"""
    },
    {
        "rule_id": "pph_final_threshold",
        "rule_type": "tax_calculation",
        "priority": 10,
        "rule_yaml": """
rule_id: pph_final_threshold
priority: 10
condition:
  omzet_tahun_berjalan: ">= 500000000"
action:
  apply_pph_final: true
  pph_final_rate: 0.005
  description: "PPh Final 0.5% untuk omzet tahun >= 500 juta"
"""
    },
    {
        "rule_id": "bulk_discount_10",
        "rule_type": "discount_calculation",
        "priority": 8,
        "rule_yaml": """
rule_id: bulk_discount_10
priority: 8
condition:
  quantity: ">= 10"
action:
  apply_discount: true
  discount_rate: 0.05
  description: "Diskon 5% untuk pembelian >= 10 unit"
"""
    },
    {
        "rule_id": "bulk_discount_50",
        "rule_type": "discount_calculation",
        "priority": 9,
        "rule_yaml": """
rule_id: bulk_discount_50
priority: 9
condition:
  quantity: ">= 50"
action:
  apply_discount: true
  discount_rate: 0.10
  description: "Diskon 10% untuk pembelian >= 50 unit"
"""
    },
    {
        "rule_id": "low_stock_alert",
        "rule_type": "inventory_alert",
        "priority": 10,
        "rule_yaml": """
rule_id: low_stock_alert
priority: 10
condition:
  stock_level: "<= 10"
action:
  trigger_alert: true
  alert_type: "low_stock"
  description: "Alert saat stok <= 10 unit"
"""
    }
]


async def seed_tenant_rules(tenant_id: str):
    """
    Seed default rules for a tenant

    Args:
        tenant_id: Tenant identifier
    """
    logger.info(f"Seeding default rules for tenant: {tenant_id}")

    prisma = Prisma()
    await prisma.connect()

    try:
        # Check if tenant exists
        tenant = await prisma.tenant.find_unique(where={"id": tenant_id})
        if not tenant:
            logger.error(f"Tenant not found: {tenant_id}")
            return

        logger.info(f"Found tenant: {tenant.alias} ({tenant.display_name})")

        # Create repository
        repo = RuleRepository(prisma)

        # Seed each rule
        for rule_def in DEFAULT_RULES:
            try:
                existing = await repo.get_rule_by_id(tenant_id, rule_def["rule_id"])

                if existing:
                    logger.info(f"  - Skipping existing rule: {rule_def['rule_id']}")
                    continue

                created = await repo.create_rule(
                    tenant_id=tenant_id,
                    rule_id=rule_def["rule_id"],
                    rule_type=rule_def["rule_type"],
                    rule_yaml=rule_def["rule_yaml"],
                    priority=rule_def["priority"],
                    is_active=True
                )

                logger.info(f"  ✅ Created rule: {created.ruleId} (type={created.ruleType}, priority={created.priority})")

            except Exception as e:
                logger.error(f"  ❌ Failed to create rule {rule_def['rule_id']}: {e}")

        logger.info(f"✅ Seeding complete for tenant: {tenant_id}")

    finally:
        await prisma.disconnect()


async def seed_all_tenants():
    """Seed default rules for all active tenants"""
    logger.info("Seeding default rules for all tenants")

    prisma = Prisma()
    await prisma.connect()

    try:
        tenants = await prisma.tenant.find_many(where={"status": "ACTIVE"})
        logger.info(f"Found {len(tenants)} active tenants")

        for tenant in tenants:
            logger.info(f"\n--- Tenant: {tenant.alias} ({tenant.display_name}) ---")
            await prisma.disconnect()
            await seed_tenant_rules(tenant.id)
            await prisma.connect()

        logger.info("\n✅ All tenants seeded successfully")

    finally:
        await prisma.disconnect()


def main():
    parser = argparse.ArgumentParser(description='Seed default rules for rule_engine')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--tenant-id', type=str, help='Seed rules for specific tenant')
    group.add_argument('--all', action='store_true', help='Seed rules for all active tenants')

    args = parser.parse_args()

    if args.tenant_id:
        asyncio.run(seed_tenant_rules(args.tenant_id))
    else:
        asyncio.run(seed_all_tenants())


if __name__ == '__main__':
    main()
