"""
Unit tests for RuleEvaluator

Tests rule evaluation logic with AND/OR conditions, operators, and priority
"""

import pytest
import sys
import os

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from core.rule_evaluator import RuleEvaluator


class TestRuleEvaluator:
    """Test suite for RuleEvaluator"""

    def setup_method(self):
        """Setup for each test"""
        self.evaluator = RuleEvaluator()

    def test_simple_string_match(self):
        """Test simple string equality match"""
        rules = [{
            "rule_id": "test_kopi",
            "priority": 5,
            "condition": {"product_name": "kopi"},
            "action": {"akun_pendapatan": "4-1100"}
        }]

        context = {"product_name": "kopi"}
        matched, data = self.evaluator.evaluate(rules, context)

        assert matched is True
        assert data["rule_id"] == "test_kopi"
        assert data["action"]["akun_pendapatan"] == "4-1100"

    def test_numeric_greater_than(self):
        """Test numeric greater than operator"""
        rules = [{
            "rule_id": "ppn_threshold",
            "priority": 10,
            "condition": {"total_nominal": ">= 5000000"},
            "action": {"apply_ppn": True, "ppn_rate": 0.11}
        }]

        context = {"total_nominal": 6000000}
        matched, data = self.evaluator.evaluate(rules, context)

        assert matched is True
        assert data["rule_id"] == "ppn_threshold"
        assert data["action"]["apply_ppn"] is True

    def test_numeric_less_than(self):
        """Test numeric less than operator"""
        rules = [{
            "rule_id": "low_stock",
            "priority": 10,
            "condition": {"stock_level": "<= 10"},
            "action": {"trigger_alert": True}
        }]

        context = {"stock_level": 5}
        matched, data = self.evaluator.evaluate(rules, context)

        assert matched is True
        assert data["rule_id"] == "low_stock"

    def test_and_condition(self):
        """Test AND condition (default)"""
        rules = [{
            "rule_id": "bulk_kopi",
            "priority": 8,
            "condition": {
                "product_name": "kopi",
                "quantity": ">= 10"
            },
            "action": {"apply_discount": True}
        }]

        # Should match (both conditions true)
        context = {"product_name": "kopi", "quantity": 15}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True

        # Should not match (quantity too low)
        context = {"product_name": "kopi", "quantity": 5}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is False

    def test_or_condition(self):
        """Test OR condition"""
        rules = [{
            "rule_id": "beverage_tax",
            "priority": 7,
            "condition_type": "OR",
            "conditions": [
                {"product_category": "minuman"},
                {"product_category": "kopi"}
            ],
            "action": {"apply_tax": True}
        }]

        # Should match (first condition)
        context = {"product_category": "minuman"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True

        # Should match (second condition)
        context = {"product_category": "kopi"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True

        # Should not match (neither condition)
        context = {"product_category": "makanan"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is False

    def test_priority_ordering(self):
        """Test that higher priority rules are evaluated first"""
        rules = [
            {
                "rule_id": "kopi_regular",
                "priority": 5,
                "condition": {"product_name": "kopi"},
                "action": {"akun_pendapatan": "4-1100"}
            },
            {
                "rule_id": "kopi_premium",
                "priority": 10,
                "condition": {
                    "product_name": "kopi",
                    "total_nominal": ">= 100000"
                },
                "action": {"akun_pendapatan": "4-1150"}
            }
        ]

        # High value should match premium (priority 10)
        context = {"product_name": "kopi", "total_nominal": 150000}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True
        assert data["rule_id"] == "kopi_premium"
        assert data["action"]["akun_pendapatan"] == "4-1150"

        # Low value should match regular (priority 5)
        context = {"product_name": "kopi", "total_nominal": 50000}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True
        assert data["rule_id"] == "kopi_regular"
        assert data["action"]["akun_pendapatan"] == "4-1100"

    def test_case_insensitive_string_match(self):
        """Test case-insensitive string matching"""
        rules = [{
            "rule_id": "test_kopi",
            "priority": 5,
            "condition": {"product_name": "kopi"},
            "action": {"akun_pendapatan": "4-1100"}
        }]

        # Should match despite different case
        context = {"product_name": "KOPI"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True

        context = {"product_name": "Kopi"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True

    def test_contains_operator(self):
        """Test contains operator"""
        rules = [{
            "rule_id": "es_teh",
            "priority": 5,
            "condition": {"product_name": "contains teh"},
            "action": {"akun_pendapatan": "4-1200"}
        }]

        context = {"product_name": "es teh manis"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is True

    def test_no_match(self):
        """Test when no rules match"""
        rules = [{
            "rule_id": "test_kopi",
            "priority": 5,
            "condition": {"product_name": "kopi"},
            "action": {"akun_pendapatan": "4-1100"}
        }]

        context = {"product_name": "teh"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is False
        assert data is None

    def test_empty_rules(self):
        """Test with empty rules list"""
        rules = []
        context = {"product_name": "kopi"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is False
        assert data is None

    def test_missing_field_in_context(self):
        """Test when required field is missing in context"""
        rules = [{
            "rule_id": "test_kopi",
            "priority": 5,
            "condition": {"product_name": "kopi"},
            "action": {"akun_pendapatan": "4-1100"}
        }]

        context = {"other_field": "value"}
        matched, data = self.evaluator.evaluate(rules, context)
        assert matched is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
