"""
rule_engine/app/core/rule_evaluator.py

Core rule evaluation engine with AND/OR condition support

Author: MilkyHoop Team
Version: 1.0.0
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RuleEvaluator:
    """Evaluates rules against context data"""

    # Supported operators
    OPERATORS = {
        '==': lambda a, b: a == b,
        '!=': lambda a, b: a != b,
        '>': lambda a, b: float(a) > float(b),
        '<': lambda a, b: float(a) < float(b),
        '>=': lambda a, b: float(a) >= float(b),
        '<=': lambda a, b: float(a) <= float(b),
        'contains': lambda a, b: str(b).lower() in str(a).lower(),
        'in': lambda a, b: str(a).lower() in [str(x).lower() for x in (b if isinstance(b, list) else [b])],
    }

    @staticmethod
    def evaluate(rules: List[Dict[str, Any]], context: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Evaluate rules against context, return first match

        Args:
            rules: List of rule dictionaries (sorted by priority)
            context: Context data to evaluate against

        Returns:
            Tuple of (matched, rule_data) where rule_data contains rule_id and action
        """
        logger.debug(f"Evaluating {len(rules)} rules against context: {list(context.keys())}")

        # Sort by priority (highest first)
        sorted_rules = sorted(rules, key=lambda r: r.get('priority', 0), reverse=True)

        for rule in sorted_rules:
            try:
                if RuleEvaluator._evaluate_rule(rule, context):
                    logger.info(f"Rule MATCHED: {rule['rule_id']}, priority={rule.get('priority', 0)}")
                    return True, {
                        'rule_id': rule['rule_id'],
                        'action': rule['action'],
                        'priority': rule.get('priority', 0)
                    }
            except Exception as e:
                logger.warning(f"Error evaluating rule {rule.get('rule_id', 'unknown')}: {e}")
                continue

        logger.debug("No rules matched")
        return False, None

    @staticmethod
    def _evaluate_rule(rule: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """
        Evaluate a single rule

        Args:
            rule: Rule dictionary
            context: Context data

        Returns:
            True if rule matches, False otherwise
        """
        condition_type = rule.get('condition_type', 'AND')

        # Handle compound conditions (OR logic)
        if 'conditions' in rule:
            results = [
                RuleEvaluator._evaluate_conditions(cond, context, 'AND')
                for cond in rule['conditions']
            ]
            if condition_type == 'OR':
                return any(results)
            else:
                return all(results)

        # Handle simple conditions
        return RuleEvaluator._evaluate_conditions(rule['condition'], context, condition_type)

    @staticmethod
    def _evaluate_conditions(conditions: Dict[str, Any], context: Dict[str, Any], logic: str = 'AND') -> bool:
        """
        Evaluate a set of conditions with AND/OR logic

        Args:
            conditions: Dictionary of field: value pairs
            context: Context data
            logic: 'AND' or 'OR'

        Returns:
            True if conditions match, False otherwise
        """
        results = []

        for field, expected_value in conditions.items():
            match = RuleEvaluator._evaluate_condition(field, expected_value, context)
            results.append(match)

            # Short-circuit for AND logic
            if logic == 'AND' and not match:
                return False
            # Short-circuit for OR logic
            if logic == 'OR' and match:
                return True

        # Final evaluation
        if logic == 'OR':
            return any(results)
        else:
            return all(results)

    @staticmethod
    def _evaluate_condition(field: str, expected_value: Any, context: Dict[str, Any]) -> bool:
        """
        Evaluate a single condition

        Args:
            field: Field name to check
            expected_value: Expected value (can be operator string like ">= 5000")
            context: Context data

        Returns:
            True if condition matches, False otherwise
        """
        # Get actual value from context (support nested fields with dot notation)
        actual_value = RuleEvaluator._get_nested_value(context, field)

        if actual_value is None:
            logger.debug(f"Field '{field}' not found in context")
            return False

        # Check if expected_value contains an operator
        if isinstance(expected_value, str):
            operator, operand = RuleEvaluator._parse_operator(expected_value)

            if operator:
                try:
                    op_func = RuleEvaluator.OPERATORS[operator]
                    result = op_func(actual_value, operand)
                    logger.debug(f"Condition: {field} {operator} {operand} => {actual_value} {operator} {operand} = {result}")
                    return result
                except (ValueError, TypeError) as e:
                    logger.warning(f"Operator evaluation error: {e}")
                    return False

        # Direct equality check
        result = RuleEvaluator._compare_values(actual_value, expected_value)
        logger.debug(f"Condition: {field} == {expected_value} => {actual_value} == {expected_value} = {result}")
        return result

    @staticmethod
    def _parse_operator(value_str: str) -> Tuple[Optional[str], Any]:
        """
        Parse operator from value string

        Args:
            value_str: String like ">= 5000" or "contains kopi"

        Returns:
            Tuple of (operator, operand)
        """
        value_str = str(value_str).strip()

        # Check for operators (longest first to match >= before >)
        operators = ['>=', '<=', '!=', '==', '>', '<', 'contains', 'in']

        for op in operators:
            if value_str.startswith(op):
                operand = value_str[len(op):].strip()

                # Try to convert to number
                try:
                    operand = float(operand) if '.' in operand else int(operand)
                except (ValueError, AttributeError):
                    pass

                return op, operand

        return None, value_str

    @staticmethod
    def _get_nested_value(data: Dict[str, Any], key: str) -> Any:
        """
        Get value from nested dictionary using dot notation

        Args:
            data: Dictionary to search
            key: Key (can be nested like "items.0.name")

        Returns:
            Value if found, None otherwise
        """
        keys = key.split('.')
        value = data

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            elif isinstance(value, list):
                try:
                    idx = int(k)
                    value = value[idx]
                except (ValueError, IndexError, TypeError):
                    return None
            else:
                return None

            if value is None:
                return None

        return value

    @staticmethod
    def _compare_values(actual: Any, expected: Any) -> bool:
        """
        Compare two values with type coercion

        Args:
            actual: Actual value from context
            expected: Expected value from rule

        Returns:
            True if values match, False otherwise
        """
        # Direct equality
        if actual == expected:
            return True

        # String comparison (case-insensitive)
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.lower() == expected.lower()

        # Number comparison
        try:
            return float(actual) == float(expected)
        except (ValueError, TypeError):
            pass

        # List membership
        if isinstance(expected, list):
            return actual in expected

        return False
