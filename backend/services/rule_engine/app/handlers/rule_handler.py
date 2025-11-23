"""
rule_engine/app/handlers/rule_handler.py

gRPC handlers for Rule Engine service

Author: MilkyHoop Team
Version: 1.0.0
"""

import json
import logging
from typing import List, Dict, Any

import rule_engine_pb2
from core.rule_evaluator import RuleEvaluator
from core.yaml_parser import YAMLParser
from core.rule_cache import RuleCache
from storage.rule_repository import RuleRepository
from storage.prisma_client import get_prisma

logger = logging.getLogger(__name__)


class RuleHandler:
    """Handler for rule engine operations"""

    def __init__(self, cache: RuleCache):
        """
        Initialize handler

        Args:
            cache: Rule cache instance
        """
        self.cache = cache
        self.evaluator = RuleEvaluator()

    async def evaluate_rule(
        self,
        tenant_id: str,
        rule_context: str,
        rule_type: str,
        trace_id: str = ""
    ) -> rule_engine_pb2.RuleResponse:
        """
        Evaluate rules against context

        Args:
            tenant_id: Tenant identifier
            rule_context: JSON string with context data
            rule_type: Rule type to filter
            trace_id: Distributed tracing ID

        Returns:
            RuleResponse proto message
        """
        log_prefix = f"[{trace_id}]" if trace_id else ""
        logger.info(f"{log_prefix} EvaluateRule | tenant={tenant_id}, type={rule_type}")

        try:
            # Parse context
            try:
                context = json.loads(rule_context)
            except json.JSONDecodeError as e:
                logger.error(f"{log_prefix} Invalid JSON context: {e}")
                return rule_engine_pb2.RuleResponse(
                    rule_matched=False,
                    confidence=0.0,
                    fallback_reason="Invalid JSON context"
                )

            # Get rules (from cache or database)
            rules = await self._get_rules(tenant_id, rule_type, log_prefix)

            if not rules:
                logger.info(f"{log_prefix} No rules found for type={rule_type}")
                return rule_engine_pb2.RuleResponse(
                    rule_matched=False,
                    confidence=0.0,
                    fallback_reason=f"No rules configured for type '{rule_type}'"
                )

            # DEBUG: Log context and rules for debugging
            logger.info(f"{log_prefix} Context: {json.dumps(context)}")
            logger.info(f"{log_prefix} Found {len(rules)} rules to evaluate")
            for rule in rules[:3]:
                logger.info(f"{log_prefix}   - Rule: {rule.get('rule_id')} | condition: {rule.get('condition')}")

            # Evaluate rules
            matched, rule_data = self.evaluator.evaluate(rules, context)

            if matched:
                logger.info(f"{log_prefix} Rule matched: {rule_data['rule_id']}")
                return rule_engine_pb2.RuleResponse(
                    rule_matched=True,
                    rule_id=rule_data['rule_id'],
                    action_json=json.dumps(rule_data['action']),
                    confidence=1.0,
                    fallback_reason=""
                )
            else:
                logger.info(f"{log_prefix} No matching rule")
                return rule_engine_pb2.RuleResponse(
                    rule_matched=False,
                    confidence=0.0,
                    fallback_reason="No rules matched the context"
                )

        except Exception as e:
            logger.error(f"{log_prefix} Error evaluating rules: {e}", exc_info=True)
            return rule_engine_pb2.RuleResponse(
                rule_matched=False,
                confidence=0.0,
                fallback_reason=f"Error: {str(e)}"
            )

    async def get_tenant_rules(
        self,
        tenant_id: str,
        rule_type: str = ""
    ) -> rule_engine_pb2.TenantRulesResponse:
        """
        Get all rules for a tenant

        Args:
            tenant_id: Tenant identifier
            rule_type: Optional filter by rule type

        Returns:
            TenantRulesResponse proto message
        """
        logger.info(f"GetTenantRules | tenant={tenant_id}, type={rule_type}")

        try:
            prisma = await get_prisma()
            repo = RuleRepository(prisma)

            # Get rules from database
            db_rules = await repo.get_tenant_rules(
                tenant_id=tenant_id,
                rule_type=rule_type if rule_type else None
            )

            # Convert to proto messages
            rule_defs = []
            for rule in db_rules:
                rule_defs.append(rule_engine_pb2.RuleDefinition(
                    rule_id=rule.ruleId,
                    rule_yaml=rule.ruleYaml,
                    rule_type=rule.ruleType,
                    priority=rule.priority,
                    is_active=rule.isActive,
                    created_at=int(rule.createdAt.timestamp()),
                    updated_at=int(rule.updatedAt.timestamp())
                ))

            logger.info(f"Retrieved {len(rule_defs)} rules")
            return rule_engine_pb2.TenantRulesResponse(rules=rule_defs)

        except Exception as e:
            logger.error(f"Error getting tenant rules: {e}", exc_info=True)
            return rule_engine_pb2.TenantRulesResponse(rules=[])

    async def update_tenant_rules(
        self,
        tenant_id: str,
        rule_yaml: str,
        rule_type: str,
        priority: int = 0
    ) -> rule_engine_pb2.UpdateRulesResponse:
        """
        Update/create a tenant rule

        Args:
            tenant_id: Tenant identifier
            rule_yaml: YAML rule definition
            rule_type: Rule type
            priority: Rule priority

        Returns:
            UpdateRulesResponse proto message
        """
        logger.info(f"UpdateTenantRules | tenant={tenant_id}, type={rule_type}")

        try:
            # Parse and validate YAML
            try:
                rule = YAMLParser.parse(rule_yaml)
            except ValueError as e:
                logger.error(f"Invalid YAML: {e}")
                return rule_engine_pb2.UpdateRulesResponse(
                    success=False,
                    message=f"Invalid YAML: {str(e)}"
                )

            # Validate rule
            if not YAMLParser.validate_rule(rule):
                return rule_engine_pb2.UpdateRulesResponse(
                    success=False,
                    message="Rule validation failed"
                )

            # Upsert to database
            prisma = await get_prisma()
            repo = RuleRepository(prisma)

            db_rule = await repo.upsert_rule(
                tenant_id=tenant_id,
                rule_id=rule['rule_id'],
                rule_type=rule_type,
                rule_yaml=rule_yaml,
                priority=priority
            )

            # Invalidate cache
            self.cache.invalidate(tenant_id)

            logger.info(f"Rule updated: {db_rule.ruleId}")
            return rule_engine_pb2.UpdateRulesResponse(
                success=True,
                message="Rule updated successfully",
                rule_id=db_rule.ruleId
            )

        except Exception as e:
            logger.error(f"Error updating rule: {e}", exc_info=True)
            return rule_engine_pb2.UpdateRulesResponse(
                success=False,
                message=f"Error: {str(e)}"
            )

    async def _get_rules(
        self,
        tenant_id: str,
        rule_type: str,
        log_prefix: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Get rules from cache or database

        Args:
            tenant_id: Tenant identifier
            rule_type: Rule type to filter
            log_prefix: Log prefix for tracing

        Returns:
            List of parsed rule dictionaries
        """
        # Try cache first
        cache_key = f"{tenant_id}:{rule_type}"
        cached_rules = self.cache.get(cache_key)

        if cached_rules is not None:
            logger.debug(f"{log_prefix} Using cached rules: {len(cached_rules)}")
            return cached_rules

        # Load from database
        logger.debug(f"{log_prefix} Loading rules from database")
        prisma = await get_prisma()
        repo = RuleRepository(prisma)

        db_rules = await repo.get_tenant_rules(
            tenant_id=tenant_id,
            rule_type=rule_type
        )

        # Parse YAML rules
        parsed_rules = []
        for db_rule in db_rules:
            try:
                rule = YAMLParser.parse(db_rule.ruleYaml)
                rule['priority'] = db_rule.priority  # Ensure priority from DB
                parsed_rules.append(rule)
            except ValueError as e:
                logger.warning(f"{log_prefix} Skipping invalid rule {db_rule.ruleId}: {e}")
                continue

        # Cache parsed rules
        self.cache.set(cache_key, parsed_rules)
        logger.debug(f"{log_prefix} Cached {len(parsed_rules)} parsed rules")

        return parsed_rules
