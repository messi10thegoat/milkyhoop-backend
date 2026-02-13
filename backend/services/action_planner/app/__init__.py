"""
action_planner - Sprint 2 Microservice for MilkyHoop Agentic Accounting

Generates ActionPlan from user text via LLM classification and parsing.

IRON LAW 0 & 10: This service ONLY plans. It NEVER writes accounting data.
All data mutations are handled downstream by Kernel services.
"""

__version__ = "2.0.0"
