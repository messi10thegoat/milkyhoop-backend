"""
Tool definitions for LLM function calling in action_planner.
Pattern identical to ragllm_service - proven with 31 tools.
"""

ENRICHMENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_customers",
            "description": "Search for customers by name or email. Returns customer details including ID, name, email, AR balance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Customer name or email to search for"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default 10)",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_items",
            "description": "Fuzzy search for items/products by name or SKU. Returns item ID, name, sell price, unit, stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Item name or SKU to search for"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default 10)",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_item_details",
            "description": "Get complete item details by ID including price, unit, stock, category, tax settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item UUID"
                    }
                },
                "required": ["item_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_accounts",
            "description": "Search chart of accounts by name or code. Used to resolve GL account references.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Account name or code"
                    },
                    "account_type": {
                        "type": "string",
                        "description": "Filter by account type",
                        "enum": ["ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"]
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        }
    }
]
