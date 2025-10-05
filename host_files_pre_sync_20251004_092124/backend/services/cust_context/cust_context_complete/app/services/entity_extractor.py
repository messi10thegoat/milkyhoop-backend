import re
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class CustomerEntityExtractor:
    """Extracts entities from customer queries (zero API calls)"""
    
    def __init__(self):
        # Product patterns (Indonesian banking terms)
        self.product_patterns = {
            "tahapan xpresi": {"type": "product", "category": "tabungan"},
            "tahapan bca": {"type": "product", "category": "tabungan"},
            "tabunganku": {"type": "product", "category": "tabungan"},
            "tapres": {"type": "product", "category": "tabungan"},
            "simpel": {"type": "product", "category": "tabungan"},
            "kredit": {"type": "product", "category": "kredit"},
            "deposito": {"type": "product", "category": "investasi"}
        }
        
        # Intent patterns
        self.intent_patterns = {
            "pricing": ["harga", "biaya", "tarif", "setoran", "admin", "berapa"],
            "requirements": ["syarat", "dokumen", "persyaratan", "butuh", "perlu"],
            "process": ["cara", "gimana", "bagaimana", "proses", "langkah"],
            "comparison": ["paling", "terbaik", "murah", "mahal", "bandingkan"],
            "features": ["fitur", "keuntungan", "benefit", "kelebihan"]
        }
        
        # Reference patterns  
        self.reference_patterns = {
            "yang_tadi": ["yang tadi", "sebelumnya", "yang barusan"],
            "yang_itu": ["yang itu", "itu", "tersebut"],
            "semuanya": ["semuanya", "semua", "seluruhnya"],
            "yang_mana": ["yang mana", "mana", "pilih yang mana"]
        }
    
    def extract_products(self, query: str) -> List[Dict[str, Any]]:
        """Extract product mentions from query"""
        products = []
        query_lower = query.lower()
        
        for product_name, product_info in self.product_patterns.items():
            if product_name in query_lower:
                products.append({
                    "type": "product",
                    "name": product_name.title(),
                    "details": product_info
                })
        
        return products
    
    def extract_intent(self, query: str) -> Optional[str]:
        """Extract primary intent from query"""
        query_lower = query.lower()
        
        for intent, keywords in self.intent_patterns.items():
            for keyword in keywords:
                if keyword in query_lower:
                    return intent
        
        return "general_inquiry"
    
    def extract_references(self, query: str) -> List[str]:
        """Extract reference patterns from query"""
        references = []
        query_lower = query.lower()
        
        for ref_type, patterns in self.reference_patterns.items():
            for pattern in patterns:
                if pattern in query_lower:
                    references.append(ref_type)
                    break
        
        return references
    
    def extract_comparison_criteria(self, query: str) -> Optional[str]:
        """Extract comparison criteria (murah, mahal, etc.)"""
        query_lower = query.lower()
        
        criteria_map = {
            "murah": "price_low",
            "mahal": "price_high", 
            "terbaik": "best_overall",
            "bagus": "quality",
            "gampang": "ease_of_use",
            "cepat": "speed",
            "aman": "security"
        }
        
        for keyword, criteria in criteria_map.items():
            if keyword in query_lower:
                return criteria
        
        return None
    
    def extract_all_entities(self, query: str) -> Dict[str, Any]:
        """Extract all entities and metadata from query"""
        return {
            "products": self.extract_products(query),
            "intent": self.extract_intent(query),
            "references": self.extract_references(query),
            "comparison_criteria": self.extract_comparison_criteria(query),
            "original_query": query
        }
    
    def prepare_context_entities(self, extracted: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert extracted entities to context format"""
        entities = []
        
        # Add products
        for product in extracted.get("products", []):
            entities.append({
                "type": "product",
                "name": product["name"],
                "details": {
                    **product["details"],
                    "intent": extracted.get("intent"),
                    "query": extracted.get("original_query")
                }
            })
        
        # Add intent as entity if no products found
        if not entities and extracted.get("intent"):
            entities.append({
                "type": "intent",
                "name": extracted["intent"],
                "details": {
                    "query": extracted.get("original_query"),
                    "references": extracted.get("references", []),
                    "comparison_criteria": extracted.get("comparison_criteria")
                }
            })
        
        return entities
