"""
Fix grpc_server.py to use correct prompt method names
"""

def fix_grpc_integration():
    with open('/app/backend/services/tenant_parser/app/grpc_server.py', 'r') as f:
        content = f.read()
    
    # Replace incorrect method calls with correct ones
    fixes = [
        ('self.confidence_engine.build_anti_hallucination_prompt', 'self.confidence_engine.build_medium_prompt'),
        ('self.build_synthesis_prompt', 'self.confidence_engine.build_deep_prompt')
    ]
    
    for old, new in fixes:
        if old in content:
            content = content.replace(old, new)
            print(f"✅ Fixed: {old} → {new}")
    
    # Write fixed content
    with open('/app/backend/services/tenant_parser/app/grpc_server.py', 'w') as f:
        f.write(content)
    
    print("✅ grpc_server.py integration fixed")

if __name__ == "__main__":
    fix_grpc_integration()
