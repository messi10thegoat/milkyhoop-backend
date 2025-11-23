"""
rule_engine/app/storage/rule_repository.py

Database repository for tenant rules

Author: MilkyHoop Team
Version: 1.0.0
"""

import logging
from typing import List, Optional
from milkyhoop_prisma import Prisma

logger = logging.getLogger(__name__)


class RuleRepository:
    """Repository for accessing tenant rules from database"""

    def __init__(self, prisma: Prisma):
        """
        Initialize repository

        Args:
            prisma: Prisma client instance
        """
        self.prisma = prisma

    async def get_tenant_rules(
        self,
        tenant_id: str,
        rule_type: Optional[str] = None,
        is_active: bool = True
    ):
        """
        Get rules for a tenant

        Args:
            tenant_id: Tenant identifier
            rule_type: Optional filter by rule type
            is_active: Only return active rules (default True)

        Returns:
            List of TenantRule models
        """
        where_clause = {
            "tenantId": tenant_id,
            "isActive": is_active
        }

        if rule_type:
            where_clause["ruleType"] = rule_type

        rules = await self.prisma.tenantrule.find_many(
            where=where_clause,
            order={"priority": "desc"}
        )

        logger.debug(f"Retrieved {len(rules)} rules for tenant={tenant_id}, type={rule_type}")
        return rules

    async def get_rule_by_id(self, tenant_id: str, rule_id: str):
        """
        Get a specific rule by ID

        Args:
            tenant_id: Tenant identifier
            rule_id: Rule identifier

        Returns:
            TenantRule model or None
        """
        rule = await self.prisma.tenantrule.find_first(
            where={
                "tenantId": tenant_id,
                "ruleId": rule_id
            }
        )

        if rule:
            logger.debug(f"Found rule: {rule_id} for tenant={tenant_id}")
        else:
            logger.debug(f"Rule not found: {rule_id} for tenant={tenant_id}")

        return rule

    async def create_rule(
        self,
        tenant_id: str,
        rule_id: str,
        rule_type: str,
        rule_yaml: str,
        priority: int = 0,
        is_active: bool = True
    ):
        """
        Create a new rule

        Args:
            tenant_id: Tenant identifier
            rule_id: Rule identifier
            rule_type: Rule type
            rule_yaml: YAML rule definition
            priority: Rule priority
            is_active: Whether rule is active

        Returns:
            Created TenantRule model
        """
        rule = await self.prisma.tenantrule.create(
            data={
                "tenantId": tenant_id,
                "ruleId": rule_id,
                "ruleType": rule_type,
                "ruleYaml": rule_yaml,
                "priority": priority,
                "isActive": is_active
            }
        )

        logger.info(f"Created rule: {rule_id} for tenant={tenant_id}, type={rule_type}")
        return rule

    async def upsert_rule(
        self,
        tenant_id: str,
        rule_id: str,
        rule_type: str,
        rule_yaml: str,
        priority: int = 0,
        is_active: bool = True
    ):
        """
        Create or update a rule (upsert)

        Args:
            tenant_id: Tenant identifier
            rule_id: Rule identifier
            rule_type: Rule type
            rule_yaml: YAML rule definition
            priority: Rule priority
            is_active: Whether rule is active

        Returns:
            Created or updated TenantRule model
        """
        rule = await self.prisma.tenantrule.upsert(
            where={
                "tenantId_ruleId": {
                    "tenantId": tenant_id,
                    "ruleId": rule_id
                }
            },
            data={
                "create": {
                    "tenantId": tenant_id,
                    "ruleId": rule_id,
                    "ruleType": rule_type,
                    "ruleYaml": rule_yaml,
                    "priority": priority,
                    "isActive": is_active
                },
                "update": {
                    "ruleYaml": rule_yaml,
                    "priority": priority,
                    "isActive": is_active
                }
            }
        )

        logger.info(f"Upserted rule: {rule_id} for tenant={tenant_id}")
        return rule
