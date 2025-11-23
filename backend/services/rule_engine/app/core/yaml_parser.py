"""
rule_engine/app/core/yaml_parser.py

YAML rule parser with validation

Author: MilkyHoop Team
Version: 1.0.0
"""

import yaml
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class YAMLParser:
    """Parser for YAML rule definitions"""

    @staticmethod
    def parse(rule_yaml: str) -> Dict[str, Any]:
        """
        Parse a single YAML rule string

        Args:
            rule_yaml: YAML string containing rule definition

        Returns:
            Parsed rule dictionary

        Raises:
            ValueError: If YAML is invalid or missing required fields
        """
        try:
            rule = yaml.safe_load(rule_yaml)

            if not isinstance(rule, dict):
                raise ValueError("Rule must be a dictionary")

            # Validate required fields
            required_fields = ['rule_id', 'condition', 'action']
            for field in required_fields:
                if field not in rule:
                    raise ValueError(f"Missing required field: {field}")

            # Set defaults
            if 'priority' not in rule:
                rule['priority'] = 0
            if 'condition_type' not in rule:
                rule['condition_type'] = 'AND'

            logger.debug(f"Parsed rule: {rule['rule_id']}")
            return rule

        except yaml.YAMLError as e:
            logger.error(f"YAML parse error: {e}")
            raise ValueError(f"Invalid YAML: {e}")

    @staticmethod
    def parse_multiple(rules_yaml: str) -> List[Dict[str, Any]]:
        """
        Parse multiple YAML rules from a string

        Args:
            rules_yaml: YAML string containing list of rules

        Returns:
            List of parsed rule dictionaries

        Raises:
            ValueError: If YAML is invalid
        """
        try:
            rules = yaml.safe_load(rules_yaml)

            if not isinstance(rules, list):
                # If single rule, wrap in list
                if isinstance(rules, dict):
                    rules = [rules]
                else:
                    raise ValueError("Rules must be a list or dictionary")

            parsed_rules = []
            for idx, rule in enumerate(rules):
                try:
                    parsed_rule = YAMLParser.parse(yaml.dump(rule))
                    parsed_rules.append(parsed_rule)
                except ValueError as e:
                    logger.warning(f"Skipping invalid rule at index {idx}: {e}")
                    continue

            logger.info(f"Parsed {len(parsed_rules)} valid rules from YAML")
            return parsed_rules

        except yaml.YAMLError as e:
            logger.error(f"YAML parse error: {e}")
            raise ValueError(f"Invalid YAML: {e}")

    @staticmethod
    def validate_rule(rule: Dict[str, Any]) -> bool:
        """
        Validate a parsed rule structure

        Args:
            rule: Parsed rule dictionary

        Returns:
            True if valid, False otherwise
        """
        try:
            # Check required fields
            required = ['rule_id', 'condition', 'action']
            for field in required:
                if field not in rule:
                    logger.warning(f"Rule missing field: {field}")
                    return False

            # Validate condition
            if not isinstance(rule['condition'], dict):
                logger.warning(f"Invalid condition type: {type(rule['condition'])}")
                return False

            # Validate action
            if not isinstance(rule['action'], dict):
                logger.warning(f"Invalid action type: {type(rule['action'])}")
                return False

            # Validate condition_type if present
            if 'condition_type' in rule:
                valid_types = ['AND', 'OR']
                if rule['condition_type'] not in valid_types:
                    logger.warning(f"Invalid condition_type: {rule['condition_type']}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Rule validation error: {e}")
            return False
