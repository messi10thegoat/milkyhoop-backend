"""
Fix import to use super intelligent engine
"""

def fix_grpc_import():
    with open('/app/backend/services/tenant_parser/app/grpc_server.py', 'r') as f:
        content = f.read()
    
    # Replace import and factory function calls
    fixes = [
        ('from app.services.enhanced_confidence_engine import create_enhanced_confidence_engine', 
         'from app.services.enhanced_confidence_engine import create_super_intelligent_engine'),
        ('create_enhanced_confidence_engine()', 'create_super_intelligent_engine()'),
        ('self.confidence_engine.calculate_universal_confidence', 'self.confidence_engine.calculate_super_confidence'),
        ('self.confidence_engine.enhanced_decision_engine', 'self.confidence_engine.super_decision_engine')
    ]
    
    for old, new in fixes:
        if old in content:
            content = content.replace(old, new)
            print(f"✅ Updated: {old[:50]}... → {new[:50]}...")
    
    with open('/app/backend/services/tenant_parser/app/grpc_server.py', 'w') as f:
        f.write(content)
    
    print("✅ Super Intelligence import updated")

if __name__ == "__main__":
    fix_grpc_import()
