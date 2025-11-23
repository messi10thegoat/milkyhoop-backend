"""
Customer Reference Resolution Service Configuration
"""
import os
from typing import Dict

class Config:
    """Service configuration"""
    
    # gRPC Server
    GRPC_PORT = int(os.getenv('GRPC_PORT', 5013))
    
    # Context Service Integration
    CONTEXT_SERVICE_HOST = os.getenv('CONTEXT_SERVICE_HOST', 'cust_context')
    CONTEXT_SERVICE_PORT = int(os.getenv('CONTEXT_SERVICE_PORT', 5008))
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    
    # Service Info
    SERVICE_NAME = 'cust_reference'
    SERVICE_VERSION = '1.0.0'
    
    @classmethod
    def get_context_address(cls) -> str:
        """Get context service gRPC address"""
        return f"{cls.CONTEXT_SERVICE_HOST}:{cls.CONTEXT_SERVICE_PORT}"
    
    @classmethod
    def to_dict(cls) -> Dict:
        """Convert config to dictionary"""
        return {
            'grpc_port': cls.GRPC_PORT,
            'context_service': cls.get_context_address(),
            'log_level': cls.LOG_LEVEL,
            'service_name': cls.SERVICE_NAME,
            'service_version': cls.SERVICE_VERSION
        }

# Global config instance
config = Config()
