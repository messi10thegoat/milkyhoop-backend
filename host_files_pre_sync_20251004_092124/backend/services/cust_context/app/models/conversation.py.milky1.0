from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import json
from dataclasses import dataclass, asdict

@dataclass
class ConversationEntity:
    """Individual entity in conversation (product, service, etc.)"""
    entity_type: str  # "product", "service", "document"
    entity_name: str  # "Tahapan Xpresi", "TabunganKu"
    entity_details: Dict[str, Any]  # {"price": "50000", "admin": "10000"}
    mentioned_turn: int  # Which conversation turn it was mentioned
    focus_score: float = 0.0  # How much focus this entity has

@dataclass
class ConversationContext:
    """Complete conversation context for a customer session"""
    session_id: str
    tenant_id: str
    entities: List[ConversationEntity]
    current_focus: Optional[str] = None  # Currently discussed entity
    last_query: str = ""
    turn_count: int = 0
    created_at: datetime = None
    updated_at: datetime = None
    ttl_seconds: int = 3600  # 1 hour default
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def add_entity(self, entity_type: str, entity_name: str, details: Dict[str, Any]):
        """Add new entity to conversation context"""
        entity = ConversationEntity(
            entity_type=entity_type,
            entity_name=entity_name,
            entity_details=details,
            mentioned_turn=self.turn_count
        )
        
        # Remove if already exists (update case)
        self.entities = [e for e in self.entities if e.entity_name != entity_name]
        self.entities.append(entity)
        
        # Set as current focus
        self.current_focus = entity_name
        self.update_focus_scores()
    
    def update_focus_scores(self):
        """Update focus scores based on recency and mentions"""
        for entity in self.entities:
            # Recent mentions get higher scores
            turns_ago = self.turn_count - entity.mentioned_turn
            entity.focus_score = max(0.1, 1.0 - (turns_ago * 0.2))
            
            # Current focus gets bonus
            if entity.entity_name == self.current_focus:
                entity.focus_score += 0.5
    
    def get_entities_by_type(self, entity_type: str) -> List[ConversationEntity]:
        """Get all entities of specific type"""
        return [e for e in self.entities if e.entity_type == entity_type]
    
    def get_focused_entity(self) -> Optional[ConversationEntity]:
        """Get currently focused entity"""
        if self.current_focus:
            for entity in self.entities:
                if entity.entity_name == self.current_focus:
                    return entity
        return None
    
    def increment_turn(self, query: str):
        """Increment conversation turn and update context"""
        self.turn_count += 1
        self.last_query = query
        self.updated_at = datetime.now()
        self.update_focus_scores()
    
    def is_expired(self) -> bool:
        """Check if conversation context has expired"""
        if self.updated_at is None:
            return True
        return datetime.now() > (self.updated_at + timedelta(seconds=self.ttl_seconds))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Redis storage"""
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "entities": [asdict(e) for e in self.entities],
            "current_focus": self.current_focus,
            "last_query": self.last_query,
            "turn_count": self.turn_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "ttl_seconds": self.ttl_seconds
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationContext':
        """Create from dictionary (Redis loading)"""
        entities = []
        for e_data in data.get("entities", []):
            entities.append(ConversationEntity(**e_data))
        
        context = cls(
            session_id=data["session_id"],
            tenant_id=data["tenant_id"],
            entities=entities,
            current_focus=data.get("current_focus"),
            last_query=data.get("last_query", ""),
            turn_count=data.get("turn_count", 0),
            ttl_seconds=data.get("ttl_seconds", 3600)
        )
        
        if data.get("created_at"):
            context.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("updated_at"):
            context.updated_at = datetime.fromisoformat(data["updated_at"])
            
        return context
