"""
tenant_orchestrator/app/config.py
Configuration for Tenant Orchestrator Service
Manages all downstream service addresses and settings

Author: MilkyHoop Team
Version: 2.0.0
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Configuration settings for Tenant Orchestrator
    All settings can be overridden via environment variables
    """
    
    # ============================================
    # Service Metadata
    # ============================================
    SERVICE_NAME: str = "TenantOrchestrator"
    service_version: str = "2.0.0"
    grpc_port: int = 5017
    
    # ============================================
    # Database Configuration
    # ============================================
    DATABASE_URL: str = ""
    
    # ============================================
    # Downstream Service Addresses
    # ============================================
    
    # Business Parser (intent classification for tenant queries)
    business_parser_grpc_host: str = "business_parser"
    business_parser_grpc_port: int = 5018

    # Rule Engine (deterministic rule evaluation)
    rule_engine_grpc_host: str = "rule_engine"
    rule_engine_grpc_port: int = 5070

    # Conversation Manager (context and history for multi-turn)
    conversation_manager_grpc_host: str = "conversation_manager"
    conversation_manager_grpc_port: int = 5016
    
    # Conversation Service (chat persistence - NEW)
    conversation_grpc_host: str = "conversation_service"
    conversation_grpc_port: int = 5002
    
    # Transaction Service (financial transactions, analytics)
    transaction_grpc_host: str = "transaction_service"
    transaction_grpc_port: int = 7020
    
    # Reporting Service (SAK EMKM reports)
    reporting_grpc_host: str = "reporting_service"
    reporting_grpc_port: int = 7030
    
    # Inventory Service (stock management)
    inventory_grpc_host: str = "inventory_service"
    inventory_grpc_port: int = 7040
    
    # Accounting Service (journal entries, chart of accounts)
    accounting_grpc_host: str = "accounting_service"
    accounting_grpc_port: int = 7050
    
    # ============================================
    # Computed Properties (Full Addresses)
    # ============================================
    
    @property
    def business_parser_address(self) -> str:
        """Business Parser full gRPC address"""
        return f"{self.business_parser_grpc_host}:{self.business_parser_grpc_port}"

    @property
    def rule_engine_address(self) -> str:
        """Rule Engine full gRPC address"""
        return f"{self.rule_engine_grpc_host}:{self.rule_engine_grpc_port}"

    @property
    def conversation_manager_address(self) -> str:
        """Conversation Manager full gRPC address"""
        return f"{self.conversation_manager_grpc_host}:{self.conversation_manager_grpc_port}"
    
    @property
    def conversation_address(self) -> str:
        """Conversation Service full gRPC address (Chat Persistence)"""
        return f"{self.conversation_grpc_host}:{self.conversation_grpc_port}"
    
    @property
    def transaction_address(self) -> str:
        """Transaction Service full gRPC address"""
        return f"{self.transaction_grpc_host}:{self.transaction_grpc_port}"
    
    @property
    def reporting_address(self) -> str:
        """Reporting Service full gRPC address"""
        return f"{self.reporting_grpc_host}:{self.reporting_grpc_port}"
    
    @property
    def inventory_address(self) -> str:
        """Inventory Service full gRPC address"""
        return f"{self.inventory_grpc_host}:{self.inventory_grpc_port}"
    
    @property
    def accounting_address(self) -> str:
        """Accounting Service full gRPC address"""
        return f"{self.accounting_grpc_host}:{self.accounting_grpc_port}"
    
    # ============================================
    # Configuration
    # ============================================
    class Config:
        env_file = ".env"
        case_sensitive = False  # Allow case-insensitive env var matching
        extra = "allow"  # Allow extra fields from environment


# ============================================
# Global Settings Instance
# ============================================
settings = Settings()