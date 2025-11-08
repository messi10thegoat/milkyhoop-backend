"""
Test Script for Setup Orchestrator Service
Tests the ProcessSetupChat RPC with a simple business setup scenario
"""

import grpc
import sys
import os

# Add the proto stubs directory to path
sys.path.insert(0, '/root/milkyhoop-dev/backend/services/setup_orchestrator/app')

# Import generated proto stubs
import setup_orchestrator_pb2
import setup_orchestrator_pb2_grpc
from google.protobuf import empty_pb2


def test_health_check():
    """Test 1: Health Check"""
    print("=" * 60)
    print("TEST 1: Health Check")
    print("=" * 60)
    
    try:
        channel = grpc.insecure_channel('localhost:7014')
        stub = setup_orchestrator_pb2_grpc.SetupOrchestratorStub(channel)
        
        response = stub.HealthCheck(empty_pb2.Empty())
        
        print("✅ Health Check PASSED")
        print("Service is responding")
        channel.close()
        return True
        
    except Exception as e:
        print(f"❌ Health Check FAILED: {e}")
        return False


def test_process_setup_chat_initial():
    """Test 2: Initial Business Setup Message"""
    print("\n" + "=" * 60)
    print("TEST 2: Process Initial Setup Message")
    print("=" * 60)
    
    try:
        channel = grpc.insecure_channel('localhost:7014')
        stub = setup_orchestrator_pb2_grpc.SetupOrchestratorStub(channel)
        
        # Create request
        request = setup_orchestrator_pb2.ProcessSetupChatRequest(
            user_id="test-user-001",
            tenant_id="test-tenant-001",
            session_id="test-session-001",
            message="Gue jualan kue custom, nama bisnis Rina Cakes, harga mulai dari 250rb"
        )
        
        print("\n📤 Sending request:")
        print(f"  User ID: {request.user_id}")
        print(f"  Tenant ID: {request.tenant_id}")
        print(f"  Session ID: {request.session_id}")
        print(f"  Message: {request.message}")
        
        # Call RPC
        response = stub.ProcessSetupChat(request)
        
        print("\n📥 Response received:")
        print(f"  Status: {response.status}")
        print(f"  Current State: {response.current_state}")
        print(f"  Session ID: {response.session_id}")
        print(f"  Next Action: {response.next_action}")
        print(f"\n  Milky Response:")
        print(f"  {response.milky_response}")
        
        if response.business_data.business_type:
            print(f"\n  📊 Extracted Business Data:")
            print(f"    Business Type: {response.business_data.business_type}")
            print(f"    Business Name: {response.business_data.business_name}")
            print(f"    Products/Services: {response.business_data.products_services}")
            print(f"    Pricing: {response.business_data.pricing}")
            print(f"    Completeness: {response.business_data.completeness_score:.2f}")
        
        if response.metadata.trace_id:
            print(f"\n  🔍 Metadata:")
            print(f"    Trace ID: {response.metadata.trace_id}")
            print(f"    Processing Time: {response.metadata.processing_time_ms}ms")
            print(f"    Service Calls: {len(response.metadata.service_calls)}")
            
            for call in response.metadata.service_calls:
                print(f"      - {call.service_name}.{call.method}: {call.duration_ms}ms ({call.status})")
        
        # Validate response
        assert response.status == "success", f"Expected status 'success', got '{response.status}'"
        assert response.session_id == "test-session-001", "Session ID mismatch"
        assert len(response.milky_response) > 0, "Empty milky_response"
        
        print("\n✅ TEST 2 PASSED")
        print("Setup orchestrator is working correctly!")
        
        channel.close()
        return True
        
    except grpc.RpcError as e:
        print(f"\n❌ gRPC Error: {e.code()}")
        print(f"   Details: {e.details()}")
        return False
        
    except Exception as e:
        print(f"\n❌ TEST 2 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_process_setup_chat_continuation():
    """Test 3: Continuation Message"""
    print("\n" + "=" * 60)
    print("TEST 3: Process Continuation Message")
    print("=" * 60)
    
    try:
        channel = grpc.insecure_channel('localhost:7014')
        stub = setup_orchestrator_pb2_grpc.SetupOrchestratorStub(channel)
        
        # Create request for continuation
        request = setup_orchestrator_pb2.ProcessSetupChatRequest(
            user_id="test-user-001",
            tenant_id="test-tenant-001",
            session_id="test-session-002",
            message="Jam operasional setiap hari jam 8 pagi sampai 8 malam"
        )
        
        print("\n📤 Sending continuation request:")
        print(f"  Message: {request.message}")
        
        response = stub.ProcessSetupChat(request)
        
        print("\n📥 Response received:")
        print(f"  Status: {response.status}")
        print(f"  Milky Response: {response.milky_response[:100]}...")
        
        print("\n✅ TEST 3 PASSED")
        
        channel.close()
        return True
        
    except Exception as e:
        print(f"\n❌ TEST 3 FAILED: {e}")
        return False


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("SETUP ORCHESTRATOR TEST SUITE")
    print("=" * 60)
    print(f"Target: localhost:7014")
    print(f"Service: setup_orchestrator")
    print("=" * 60)
    
    results = []
    
    # Test 1: Health Check
    results.append(("Health Check", test_health_check()))
    
    # Test 2: Initial Message
    results.append(("Initial Setup Message", test_process_setup_chat_initial()))
    
    # Test 3: Continuation
    results.append(("Continuation Message", test_process_setup_chat_continuation()))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print("=" * 60)
    print(f"Result: {passed}/{total} tests passed")
    print("=" * 60)
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("Setup orchestrator is fully operational!")
        return 0
    else:
        print(f"\n⚠️ {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    exit(main())
