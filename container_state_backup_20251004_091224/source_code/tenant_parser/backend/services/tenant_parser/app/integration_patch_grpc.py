"""
SURGICAL INTEGRATION PATCH for grpc_server.py
Replace embedded UnifiedConfidenceEngine with EnhancedConfidenceEngine
"""

def apply_integration_patch():
    """Apply surgical integration patch"""
    
    # Read current grpc_server.py
    with open('/app/backend/services/tenant_parser/app/grpc_server.py', 'r') as f:
        content = f.read()
    
    # SURGICAL REPLACEMENT 1: Import enhanced confidence engine
    old_import = "from app.services.llm_parser import parse_intent_entities"
    new_import = """from app.services.llm_parser import parse_intent_entities
from app.services.enhanced_confidence_engine import create_enhanced_confidence_engine"""
    
    if old_import in content:
        content = content.replace(old_import, new_import)
        print("✅ Enhanced confidence engine import added")
    
    # SURGICAL REPLACEMENT 2: Replace UnifiedConfidenceEngine class definition
    # Find start and end of UnifiedConfidenceEngine class
    start_marker = "class UnifiedConfidenceEngine:"
    end_marker = "class TenantParserServicer"
    
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    
    if start_idx != -1 and end_idx != -1:
        # Replace embedded class with factory call
        before_class = content[:start_idx]
        after_class = content[end_idx:]
        
        # Simple replacement - use factory function
        replacement = """# SURGICAL INTEGRATION: Use enhanced confidence engine from services
# UnifiedConfidenceEngine replaced with EnhancedConfidenceEngine

"""
        
        content = before_class + replacement + after_class
        print("✅ Embedded UnifiedConfidenceEngine class removed")
    
    # SURGICAL REPLACEMENT 3: Update confidence engine initialization
    old_init = "self.confidence_engine = UnifiedConfidenceEngine()"
    new_init = "self.confidence_engine = create_enhanced_confidence_engine()"
    
    if old_init in content:
        content = content.replace(old_init, new_init)
        print("✅ Confidence engine initialization updated")
    
    # SURGICAL REPLACEMENT 4: Update method calls for anti-hallucination
    old_build_method = "self.build_synthesis_prompt"
    new_build_method = "self.confidence_engine.build_anti_hallucination_prompt"
    
    content = content.replace(old_build_method, new_build_method)
    print("✅ Anti-hallucination prompt method updated")
    
    # Write patched content
    with open('/app/backend/services/tenant_parser/app/grpc_server_integrated.py', 'w') as f:
        f.write(content)
    
    print("💾 Integrated grpc_server.py created")
    return True

if __name__ == "__main__":
    apply_integration_patch()
