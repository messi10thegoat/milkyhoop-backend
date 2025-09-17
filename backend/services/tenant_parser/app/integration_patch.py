"""
SURGICAL INTEGRATION: Patch existing grpc_server.py to use optimized engine
Minimal changes to maintain existing architecture
"""

def apply_surgical_patch():
    """
    Apply surgical patch to replace confidence engine with optimized version
    """
    import sys
    import os
    
    # Add current directory to path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, current_dir)
    
    try:
        from confidence_engine_optimized import OptimizedConfidenceEngine
        
        # Replace the confidence engine class in the existing module
        import grpc_server
        
        # Backup original engine
        grpc_server.OriginalConfidenceEngine = grpc_server.UnifiedConfidenceEngine
        
        # Replace with optimized version
        grpc_server.UnifiedConfidenceEngine = OptimizedConfidenceEngine
        
        print("✅ Surgical patch applied - OptimizedConfidenceEngine active")
        return True
        
    except Exception as e:
        print(f"❌ Surgical patch failed: {e}")
        return False

if __name__ == "__main__":
    apply_surgical_patch()
