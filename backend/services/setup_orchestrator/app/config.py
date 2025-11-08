from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Service metadata
    service_version: str = "1.0.0"
    grpc_port: int = 5014
    
    # gRPC client addresses - will auto-read from env vars
    # INTENT_PARSER_GRPC_HOST and INTENT_PARSER_GRPC_PORT from docker-compose
    intent_parser_grpc_host: str = "intent_parser"
    intent_parser_grpc_port: int = 5009
    
    business_extractor_grpc_host: str = "business_extractor"
    business_extractor_grpc_port: int = 5015
    
    conversation_manager_grpc_host: str = "conversation_manager"
    conversation_manager_grpc_port: int = 5016
    
    ragcrud_grpc_host: str = "ragcrud_service"
    ragcrud_grpc_port: int = 5001
    
    ragllm_grpc_host: str = "ragllm_service"
    ragllm_grpc_port: int = 5011

    transaction_grpc_host: str = "transaction_service"
    transaction_grpc_port: int = 7020

    reporting_grpc_host: str = "reporting_service"
    reporting_grpc_port: int = 7030

    inventory_grpc_host: str = "inventory_service"
    inventory_grpc_port: int = 7040
    
    accounting_grpc_host: str = "accounting_service"
    accounting_grpc_port: int = 7050
    
    # Computed properties for full addresses
    @property
    def intent_parser_address(self) -> str:
        return f"{self.intent_parser_grpc_host}:{self.intent_parser_grpc_port}"
    
    @property
    def business_extractor_address(self) -> str:
        return f"{self.business_extractor_grpc_host}:{self.business_extractor_grpc_port}"
    
    @property
    def conversation_manager_address(self) -> str:
        return f"{self.conversation_manager_grpc_host}:{self.conversation_manager_grpc_port}"
    
    @property
    def ragcrud_address(self) -> str:
        return f"{self.ragcrud_grpc_host}:{self.ragcrud_grpc_port}"
    
    @property
    def ragllm_address(self) -> str:
        return f"{self.ragllm_grpc_host}:{self.ragllm_grpc_port}"

    @property
    def transaction_address(self) -> str:
        return f"{self.transaction_grpc_host}:{self.transaction_grpc_port}"

    @property
    def reporting_address(self) -> str:
        return f"{self.reporting_grpc_host}:{self.reporting_grpc_port}"

    @property
    def inventory_address(self) -> str:
        return f"{self.inventory_grpc_host}:{self.inventory_grpc_port}"

    @property
    def accounting_address(self) -> str:
        return f"{self.accounting_grpc_host}:{self.accounting_grpc_port}"

    class Config:
        env_file = ".env"
        case_sensitive = False  # Allow case-insensitive env var matching

settings = Settings()
