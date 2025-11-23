#!/usr/bin/env python3
"""Add product_kopi rule for testing"""
import asyncio
import sys
import os

# Add paths
sys.path.insert(0, "/app/backend/api_gateway/libs")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend', 'services', 'rule_engine', 'app'))

from milkyhoop_prisma import Prisma

async def add_kopi_rule():
    prisma = Prisma()
    await prisma.connect()

    tenant_id = "evlogia"

    # Check if rule exists
    existing = await prisma.tenantrule.find_first(
        where={"tenantId": tenant_id, "ruleId": "product_kopi"}
    )

    if existing:
        print("⚠️  Rule product_kopi already exists, updating...")
        await prisma.tenantrule.update(
            where={"id": existing.id},
            data={
                "ruleYaml": """rule_id: product_kopi
priority: 10
condition:
  product_name: "kopi"
action:
  akun_pendapatan: "4-1200"
  akun_hpp: "5-1200"
  description: "Pendapatan & HPP Kopi (Minuman)"
""",
                "priority": 10,
                "isActive": True
            }
        )
        print("✅ Updated rule: product_kopi")
    else:
        created = await prisma.tenantrule.create(
            data={
                "tenantId": tenant_id,
                "ruleId": "product_kopi",
                "ruleType": "product_mapping",
                "ruleYaml": """rule_id: product_kopi
priority: 10
condition:
  product_name: "kopi"
action:
  akun_pendapatan: "4-1200"
  akun_hpp: "5-1200"
  description: "Pendapatan & HPP Kopi (Minuman)"
""",
                "priority": 10,
                "isActive": True
            }
        )
        print(f"✅ Created rule: product_kopi (id={created.id})")

    await prisma.disconnect()

if __name__ == "__main__":
    asyncio.run(add_kopi_rule())
