"""
Accounting Kernel Configuration
"""
import os
from dataclasses import dataclass, field
from typing import Optional


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
class GrpcConfig:
    """gRPC server configuration"""
    host: str = "0.0.0.0"
    port: int = field(default_factory=lambda: int(os.getenv("GRPC_PORT", "7060")))
    max_workers: int = 10
    max_message_length: int = 50 * 1024 * 1024  # 50MB


@dataclass
class KafkaConfig:
    """Kafka configuration for event publishing"""
    bootstrap_servers: str = field(default_factory=lambda: os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"
    ))
    consumer_group: str = "accounting_kernel"

    # Topics
    topic_invoice_created: str = "invoice.created"
    topic_invoice_voided: str = "invoice.voided"
    topic_bill_created: str = "bill.created"
    topic_bill_voided: str = "bill.voided"
    topic_payment_received: str = "payment.received"
    topic_payment_bill: str = "payment.bill"
    topic_pos_completed: str = "pos.completed"
    topic_journal_posted: str = "accounting.journal.posted"


@dataclass
class AccountingConfig:
    """Accounting-specific configuration"""

    # Default account codes
    CASH_ACCOUNT: str = "1-10100"
    BANK_ACCOUNT: str = "1-10200"
    AR_ACCOUNT: str = "1-10400"
    INVENTORY_ACCOUNT: str = "1-10600"
    VAT_INPUT_ACCOUNT: str = "1-10500"

    AP_ACCOUNT: str = "2-10100"
    VAT_OUTPUT_ACCOUNT: str = "2-10400"

    RETAINED_EARNINGS_ACCOUNT: str = "3-20000"
    CURRENT_YEAR_EARNINGS_ACCOUNT: str = "3-30000"

    SALES_REVENUE_ACCOUNT: str = "4-10100"
    SALES_DISCOUNT_ACCOUNT: str = "4-10200"
    SALES_RETURN_ACCOUNT: str = "4-10300"

    COGS_ACCOUNT: str = "5-10100"
    PURCHASE_DISCOUNT_ACCOUNT: str = "5-10200"
    PURCHASE_RETURN_ACCOUNT: str = "5-10300"

    # Journal prefixes
    JOURNAL_PREFIX_GENERAL: str = "JV"
    JOURNAL_PREFIX_SALES: str = "SJ"
    JOURNAL_PREFIX_PURCHASE: str = "PJ"
    JOURNAL_PREFIX_CASH: str = "CJ"
    JOURNAL_PREFIX_ADJUSTMENT: str = "AJ"

    # Payment method to account mapping
    PAYMENT_ACCOUNT_MAPPING: dict = field(default_factory=lambda: {
        "CASH": "1-10100",
        "TUNAI": "1-10100",
        "TRANSFER": "1-10200",
        "BANK": "1-10200",
        "GIRO": "1-10200",
        "CEK": "1-10200",
        "QRIS": "1-10200",
    })

    # Decimal precision
    DECIMAL_PLACES: int = 6

    # Balance tolerance for validation
    BALANCE_TOLERANCE: float = 0.01


@dataclass
class Settings:
    """Main settings container"""
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    grpc: GrpcConfig = field(default_factory=GrpcConfig)
    kafka: KafkaConfig = field(default_factory=KafkaConfig)
    accounting: AccountingConfig = field(default_factory=AccountingConfig)

    # Environment
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


# Global settings instance
settings = Settings()
