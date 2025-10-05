"""
Generic Indonesian Reference Resolution Service
Works for ANY tenant: BCA, clothing stores, restaurants, consultants, etc.
"""
import re
import asyncio
from typing import List, Dict, Optional, Tuple
import grpc
from datetime import datetime

# Import context service client  
import sys
sys.path.append('/app/backend/api_gateway/libs')
from milkyhoop_protos import cust_context_pb2, cust_context_pb2_grpc

class IndonesianReferenceResolver:
    """Generic Indonesian pronoun reference resolver for any business type"""
    
    def __init__(self):
        self.context_client = None
        
    async def get_context_client(self):
        """Get gRPC client for cust_context service"""
        if not self.context_client:
            channel = grpc.aio.insecure_channel('cust_context:5008')
            # CORRECT: Use CustContextServiceStub (as found in generated file)
            self.context_client = cust_context_pb2_grpc.CustContextServiceStub(channel)
        return self.context_client
    
    async def resolve_reference(self, session_id: str, tenant_id: str, 
                              reference_text: str, context_query: str) -> Dict:
        """
        Generic reference resolution for any tenant/business type
        """
        try:
            # Get conversation context from ANY tenant
            context_client = await self.get_context_client()
            context_request = cust_context_pb2.GetContextRequest(
                session_id=session_id,
                tenant_id=tenant_id  # Works for ANY tenant
            )
            context_response = await context_client.GetContext(context_request)
            
            if not context_response.success:
                return {
                    'success': False,
                    'error_message': f'No conversation context found for tenant {tenant_id}',
                    'resolution_method': 'no_context'
                }
            
            # Parse entities from context (generic for any business)
            entities = self._parse_entities(context_response.entities)
            current_focus = context_response.current_focus
            
            # Resolve reference using generic Indonesian patterns
            resolution = await self._resolve_pattern(
                reference_text, entities, current_focus, context_query, tenant_id
            )
            
            return resolution
            
        except Exception as e:
            return {
                'success': False,
                'error_message': f'Reference resolution error for {tenant_id}: {str(e)}',
                'resolution_method': 'error'
            }
    
    def _parse_entities(self, entities_json: str) -> List[Dict]:
        """Parse entities from JSON string - works for any business type"""
        import json
        try:
            return json.loads(entities_json) if entities_json else []
        except:
            return []
    
    async def _resolve_pattern(self, reference_text: str, entities: List[Dict], 
                             current_focus: str, context_query: str, tenant_id: str) -> Dict:
        """Resolve reference using generic Indonesian patterns"""
        
        ref_lower = reference_text.lower().strip()
        
        # Pattern 1: "yang tadi" - Last mentioned entity (ANY business)
        if any(pattern in ref_lower for pattern in ['yang tadi', 'tadi', 'yang barusan']):
            return self._resolve_last_mentioned(entities, tenant_id)
        
        # Pattern 2: "yang itu" - Current focus entity (ANY business)
        elif any(pattern in ref_lower for pattern in ['yang itu', 'itu', 'yang dimaksud']):
            return self._resolve_current_focus(entities, current_focus, tenant_id)
        
        # Pattern 3: "yang paling [criteria]" - Generic criteria filtering
        elif 'yang paling' in ref_lower:
            criteria = self._extract_criteria(ref_lower)
            return self._resolve_by_criteria(entities, criteria, tenant_id)
        
        # Pattern 4: "semuanya" - All entities (ANY business)
        elif any(pattern in ref_lower for pattern in ['semuanya', 'semua', 'yang ada']):
            return self._resolve_all_entities(entities, tenant_id)
        
        # Pattern 5: Position-based "yang pertama/kedua" (ANY business)
        elif any(pattern in ref_lower for pattern in ['yang pertama', 'yang kedua', 'yang ketiga']):
            position = self._extract_position(ref_lower)
            return self._resolve_by_position(entities, position, tenant_id)
        
        # Pattern 6: Direct entity mention (ANY business)
        else:
            return self._resolve_direct_mention(entities, ref_lower, tenant_id)
    
    def _resolve_last_mentioned(self, entities: List[Dict], tenant_id: str) -> Dict:
        """Resolve 'yang tadi' to last mentioned entity - works for any business"""
        if not entities:
            return {'success': False, 'error_message': f'No entities in context for {tenant_id}'}
        
        last_entity = entities[-1]
        return {
            'success': True,
            'resolved_entity': last_entity.get('name', ''),
            'entity_type': last_entity.get('type', ''),
            'resolution_method': 'last_mentioned',
            'candidates': [e.get('name', '') for e in entities[-3:]]
        }
    
    def _resolve_current_focus(self, entities: List[Dict], current_focus: str, tenant_id: str) -> Dict:
        """Resolve 'yang itu' to current focus - works for any business"""
        if not current_focus:
            return self._resolve_last_mentioned(entities, tenant_id)  # Fallback
        
        # Find entity matching current focus
        for entity in entities:
            if entity.get('name', '').lower() == current_focus.lower():
                return {
                    'success': True,
                    'resolved_entity': entity.get('name', ''),
                    'entity_type': entity.get('type', ''),
                    'resolution_method': 'current_focus',
                    'candidates': [current_focus]
                }
        
        return {'success': False, 'error_message': f'Focus entity "{current_focus}" not found for {tenant_id}'}
    
    def _resolve_by_criteria(self, entities: List[Dict], criteria: str, tenant_id: str) -> Dict:
        """Generic criteria filtering - works for ANY business type"""
        if not entities:
            return {'success': False, 'error_message': f'No entities to filter for {tenant_id}'}
        
        # Generic criteria handling for ANY business
        if criteria in ['murah', 'terjangkau', 'hemat']:
            return self._find_by_price(entities, 'lowest', criteria, tenant_id)
        elif criteria in ['mahal', 'premium', 'bagus']:
            return self._find_by_price(entities, 'highest', criteria, tenant_id)
        elif criteria in ['populer', 'favorit', 'recommended']:
            return self._find_by_popularity(entities, criteria, tenant_id)
        elif criteria in ['baru', 'terbaru', 'latest']:
            return self._find_by_recency(entities, criteria, tenant_id)
        
        # Default: return first entity
        return {
            'success': True,
            'resolved_entity': entities[0].get('name', ''),
            'entity_type': entities[0].get('type', ''),
            'resolution_method': f'filter_by_{criteria}_default',
            'candidates': [e.get('name', '') for e in entities]
        }
    
    def _find_by_price(self, entities: List[Dict], direction: str, criteria: str, tenant_id: str) -> Dict:
        """Find entity by price - generic for any business"""
        priced_entities = [e for e in entities if self._has_price_info(e)]
        
        if not priced_entities:
            return {
                'success': True,
                'resolved_entity': entities[0].get('name', ''),
                'entity_type': entities[0].get('type', ''),
                'resolution_method': f'filter_by_{criteria}_no_price',
                'candidates': [e.get('name', '') for e in entities]
            }
        
        if direction == 'lowest':
            target = min(priced_entities, key=lambda x: self._extract_price(x.get('details', {})))
        else:
            target = max(priced_entities, key=lambda x: self._extract_price(x.get('details', {})))
        
        return {
            'success': True,
            'resolved_entity': target.get('name', ''),
            'entity_type': target.get('type', ''),
            'resolution_method': f'filter_by_{criteria}',
            'candidates': [e.get('name', '') for e in priced_entities]
        }
    
    def _find_by_popularity(self, entities: List[Dict], criteria: str, tenant_id: str) -> Dict:
        """Find by popularity - generic approach"""
        return {
            'success': True,
            'resolved_entity': entities[-1].get('name', ''),
            'entity_type': entities[-1].get('type', ''),
            'resolution_method': f'filter_by_{criteria}',
            'candidates': [e.get('name', '') for e in entities]
        }
    
    def _find_by_recency(self, entities: List[Dict], criteria: str, tenant_id: str) -> Dict:
        """Find newest/latest option"""
        return {
            'success': True,
            'resolved_entity': entities[-1].get('name', ''),
            'entity_type': entities[-1].get('type', ''),
            'resolution_method': f'filter_by_{criteria}',
            'candidates': [e.get('name', '') for e in entities]
        }
    
    def _resolve_all_entities(self, entities: List[Dict], tenant_id: str) -> Dict:
        """Resolve 'semuanya' - generic for any business"""
        if not entities:
            return {'success': False, 'error_message': f'No entities in context for {tenant_id}'}
        
        entity_names = [e.get('name', '') for e in entities]
        return {
            'success': True,
            'resolved_entity': ', '.join(entity_names),
            'entity_type': 'multiple',
            'resolution_method': 'all_entities',
            'candidates': entity_names
        }
    
    def _resolve_by_position(self, entities: List[Dict], position: int, tenant_id: str) -> Dict:
        """Resolve positional reference - generic for any business"""
        if position <= 0 or position > len(entities):
            return {'success': False, 'error_message': f'Position {position} out of range for {tenant_id}'}
        
        target_entity = entities[position - 1]
        return {
            'success': True,
            'resolved_entity': target_entity.get('name', ''),
            'entity_type': target_entity.get('type', ''),
            'resolution_method': f'position_{position}',
            'candidates': [e.get('name', '') for e in entities]
        }
    
    def _resolve_direct_mention(self, entities: List[Dict], reference_text: str, tenant_id: str) -> Dict:
        """Try to resolve direct entity mention - generic"""
        for entity in entities:
            entity_name = entity.get('name', '').lower()
            if entity_name in reference_text or reference_text in entity_name:
                return {
                    'success': True,
                    'resolved_entity': entity.get('name', ''),
                    'entity_type': entity.get('type', ''),
                    'resolution_method': 'direct_mention',
                    'candidates': [entity.get('name', '')]
                }
        
        return {'success': False, 'error_message': f'No matching entity found for {tenant_id}'}
    
    def _extract_criteria(self, text: str) -> str:
        """Extract criteria from 'yang paling [criteria]' - generic patterns"""
        patterns = {
            'murah': ['murah', 'terjangkau', 'hemat', 'ekonomis', 'cheap'],
            'mahal': ['mahal', 'premium', 'eksklusif', 'expensive'],
            'bagus': ['bagus', 'terbaik', 'recommended', 'populer', 'best', 'good'],
            'baru': ['baru', 'terbaru', 'latest', 'new'],
            'populer': ['populer', 'favorit', 'favorite', 'popular', 'hits']
        }
        
        for criteria, keywords in patterns.items():
            if any(keyword in text for keyword in keywords):
                return criteria
        
        return 'default'
    
    def _extract_position(self, text: str) -> int:
        """Extract position number - generic"""
        position_map = {
            'pertama': 1, 'kedua': 2, 'ketiga': 3, 'keempat': 4, 'kelima': 5,
            'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5
        }
        
        for word, position in position_map.items():
            if word in text:
                return position
        
        return 1
    
    def _has_price_info(self, entity: Dict) -> bool:
        """Check if entity has price information - generic"""
        details = entity.get('details', {})
        return any(key in details for key in ['price', 'harga', 'cost', 'biaya', 'tarif'])
    
    def _extract_price(self, details: Dict) -> float:
        """Extract numeric price - generic for any currency/format"""
        try:
            for price_field in ['price', 'harga', 'cost', 'biaya', 'tarif']:
                if price_field in details:
                    price_str = str(details[price_field])
                    price_clean = re.sub(r'[^\d.]', '', price_str)
                    if price_clean:
                        return float(price_clean)
            return 0.0
        except:
            return 0.0
