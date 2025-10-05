from typing import Dict, Optional, List
import json
import time
from datetime import datetime, timedelta

class MemoryModel:
    """Memory model for storing conversation context"""
    
    def __init__(self, user_id: str, tenant_id: str, key: str, value: dict, ttl: int = 3600):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.key = key
        self.value = value
        self.ttl = ttl
        self.created_at = datetime.now()
        self.expires_at = self.created_at + timedelta(seconds=ttl)
    
    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "key": self.key,
            "value": self.value,
            "ttl": self.ttl,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat()
        }
    
    def is_expired(self) -> bool:
        return datetime.now() > self.expires_at
    
    @classmethod
    def from_dict(cls, data: Dict):
        obj = cls(
            user_id=data["user_id"],
            tenant_id=data["tenant_id"],
            key=data["key"],
            value=data["value"],
            ttl=data["ttl"]
        )
        obj.created_at = datetime.fromisoformat(data["created_at"])
        obj.expires_at = datetime.fromisoformat(data["expires_at"])
        return obj
