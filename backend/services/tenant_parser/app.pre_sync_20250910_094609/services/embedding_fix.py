import json
import logging

logger = logging.getLogger(__name__)

def safe_parse_embedding(embedding_data):
    """Bulletproof embedding parser - handles any format"""
    
    # Already a list - return as is
    if isinstance(embedding_data, list):
        logger.info("✅ Embedding already list format")
        return embedding_data
    
    # String format - try to parse
    if isinstance(embedding_data, str):
        try:
            parsed = json.loads(embedding_data)
            logger.info("✅ Embedding parsed from JSON string")
            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON parse failed: {e}")
            return []
    
    # Other format - fallback
    logger.warning(f"⚠️ Unknown embedding format: {type(embedding_data)}")
    return []

def safe_encode_embedding(embedding_list):
    """Bulletproof embedding encoder"""
    if isinstance(embedding_list, list):
        return json.dumps(embedding_list)
    return embedding_list
