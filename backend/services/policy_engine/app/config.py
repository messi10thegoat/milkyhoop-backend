"""
Policy Engine Configuration

IRON LAW Compliance:
- Law 0: Configuration isolated from business logic
- Law 12: Audit configuration for immutable logging
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DatabaseConfig:
    """Database connection configuration"""
    url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:Proyek771977@postgres:5432/milkydb"
    ))
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 1800


@dataclass
class CacheConfig:
    """Cache configuration for permission caching"""
    redis_url: str = field(default_factory=lambda: os.getenv(
        "REDIS_URL", "redis://redis:6379/0"
    ))
    permission_cache_ttl: int = 300  # 5 minutes
    visibility_cache_ttl: int = 300  # 5 minutes
    role_cache_ttl: int = 600  # 10 minutes


@dataclass
class AuditConfig:
    """Audit logging configuration
    
    IRON LAW 12: Audit Immutability
    - All permission checks are logged
    - Logs are append-only
    - No edits or deletes allowed
    """
    enabled: bool = True
    log_all_checks: bool = True
    log_denied_only: bool = False
    kafka_topic: str = "policy.audit"
    retention_days: int = 365  # 1 year retention


@dataclass
class PolicyConfig:
    """Policy engine specific configuration"""
    
    # Default actions available
    ACTIONS: Dict[str, str] = field(default_factory=lambda: {
        "C": "Create",
        "R": "Read",
        "U": "Update",
        "D": "Delete",
        "V": "Void",
        "A": "Approve",
        "P": "Post",
        "E": "Export",
    })
    
    # Confidentiality levels (FCL - Financial Confidentiality Level)
    CONFIDENTIALITY_LEVELS: List[str] = field(default_factory=lambda: [
        "L1",  # Public - semua user
        "L2",  # Internal - staff level
        "L3",  # Confidential - manager level
        "L4",  # Restricted - director level
        "L5",  # Top Secret - owner only
    ])
    
    # Default role hierarchy
    ROLE_HIERARCHY: Dict[str, int] = field(default_factory=lambda: {
        "OWNER": 100,
        "ADMIN": 90,
        "MANAGER": 70,
        "SUPERVISOR": 50,
        "STAFF": 30,
        "VIEWER": 10,
    })
    
    # Module list
    MODULES: List[str] = field(default_factory=lambda: [
        "sales",
        "purchase",
        "inventory",
        "accounting",
        "report",
        "settings",
        "user_management",
        "approval",
    ])
    
    # Super admin bypass (untuk emergency access)
    super_admin_bypass: bool = field(default_factory=lambda: 
        os.getenv("POLICY_SUPER_ADMIN_BYPASS", "false").lower() == "true"
    )
    
    # AI Safety Boundary (Law 10)
    ai_write_enabled: bool = field(default_factory=lambda:
        os.getenv("POLICY_AI_WRITE_ENABLED", "false").lower() == "true"
    )
    ai_max_amount_threshold: float = field(default_factory=lambda:
        float(os.getenv("POLICY_AI_MAX_AMOUNT", "1000000"))  # 1 juta default
    )


@dataclass
class KafkaConfig:
    """Kafka configuration for event publishing"""
    bootstrap_servers: str = field(default_factory=lambda: os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"
    ))
    consumer_group: str = "policy_engine"
    
    # Topics
    topic_permission_check: str = "policy.permission.check"
    topic_permission_denied: str = "policy.permission.denied"
    topic_approval_required: str = "policy.approval.required"
    topic_audit_log: str = "policy.audit.log"


@dataclass
class Settings:
    """Main settings container"""
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    kafka: KafkaConfig = field(default_factory=KafkaConfig)
    
    # Environment
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    service_name: str = "policy_engine"
    service_port: int = field(default_factory=lambda: int(os.getenv("SERVICE_PORT", "7070")))


# Global settings instance
settings = Settings()
