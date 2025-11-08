#!/usr/bin/env python3
"""
Test conversation_manager - State Machine & Redis Integration
"""
import sys
import grpc
import json

sys.path.insert(0, './backend/services/conversation_manager/app')

import conversation_manager_pb2 as pb
import conversation_manager_pb2_grpc as pb_grpc


def test_get_context_new_session():
    """Test 1: Get context for new session"""
    print("\n" + "="*60)
    print("TEST 1: Get Context - New Session")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7016')
    stub = pb_grpc.ConversationManagerStub(channel)
    
    try:
        request = pb.GetContextRequest(
            session_id="test-new-session",
            user_id="test-user-1"
        )
        
        response = stub.GetContext(request)
        
        print(f"\n✅ Status: {response.status}")
        print(f"Session ID: {response.session_id}")
        print(f"Current State: {response.current_state}")
        print(f"Extracted Data: {response.extracted_data_json}")
        print(f"History Length: {len(response.conversation_history)}")
        print(f"TTL Remaining: {response.ttl_remaining}s")
        
        assert response.status == "success"
        assert response.current_state == "initial"
        assert len(response.conversation_history) == 0
        
        print("\n✅ Test 1 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def test_update_state_transition():
    """Test 2: State transition"""
    print("\n" + "="*60)
    print("TEST 2: Update State - State Transition")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7016')
    stub = pb_grpc.ConversationManagerStub(channel)
    
    try:
        session_id = "test-transition"
        
        # Transition: initial → collecting_info
        request = pb.UpdateStateRequest(
            session_id=session_id,
            user_id="test-user-2",
            tenant_id="test-tenant",
            new_state="collecting_info",
            message="Gue jualan kue custom"
        )
        
        response = stub.UpdateState(request)
        
        print(f"\n✅ Status: {response.status}")
        print(f"Previous State: {response.previous_state}")
        print(f"Current State: {response.current_state}")
        print(f"Transition Allowed: {response.transition_allowed}")
        print(f"Message: {response.message}")
        
        assert response.status == "success"
        assert response.previous_state == "initial"
        assert response.current_state == "collecting_info"
        assert response.transition_allowed == True
        
        print("\n✅ Test 2 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def test_store_extracted_data():
    """Test 3: Store business data"""
    print("\n" + "="*60)
    print("TEST 3: Store Extracted Data")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7016')
    stub = pb_grpc.ConversationManagerStub(channel)
    
    try:
        session_id = "test-store-data"
        
        # Store business info
        business_data = {
            "business_type": "custom_cake_shop",
            "business_name": "Rina Cakes",
            "pricing": "mulai 250rb"
        }
        
        request = pb.StoreExtractedDataRequest(
            session_id=session_id,
            data_json=json.dumps(business_data),
            merge=True
        )
        
        response = stub.StoreExtractedData(request)
        
        print(f"\n✅ Status: {response.status}")
        print(f"Message: {response.message}")
        print(f"Updated Data: {response.updated_data_json}")
        
        updated = json.loads(response.updated_data_json)
        
        assert response.status == "success"
        assert updated["business_type"] == "custom_cake_shop"
        assert updated["business_name"] == "Rina Cakes"
        
        print("\n✅ Test 3 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def test_get_next_question():
    """Test 4: Intelligence - Next question"""
    print("\n" + "="*60)
    print("TEST 4: Get Next Question")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7016')
    stub = pb_grpc.ConversationManagerStub(channel)
    
    try:
        session_id = "test-next-question"
        
        request = pb.GetNextQuestionRequest(
            session_id=session_id
        )
        
        response = stub.GetNextQuestion(request)
        
        print(f"\n✅ Status: {response.status}")
        print(f"Next Question: {response.next_question}")
        print(f"Missing Fields: {list(response.missing_fields)}")
        print(f"Suggestion: {response.suggestion}")
        
        assert response.status == "success"
        assert len(response.next_question) > 0
        assert len(response.missing_fields) > 0
        
        print("\n✅ Test 4 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def test_full_workflow():
    """Test 5: Complete setup workflow"""
    print("\n" + "="*60)
    print("TEST 5: Full Workflow Simulation")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7016')
    stub = pb_grpc.ConversationManagerStub(channel)
    
    try:
        session_id = "test-full-workflow"
        
        # Step 1: Start at initial
        print("\n--- Step 1: Initial state ---")
        ctx = stub.GetContext(pb.GetContextRequest(session_id=session_id))
        print(f"State: {ctx.current_state}")
        
        # Step 2: Transition to collecting_info
        print("\n--- Step 2: Start collecting info ---")
        update1 = stub.UpdateState(pb.UpdateStateRequest(
            session_id=session_id,
            new_state="collecting_info",
            message="Gue jualan kue custom"
        ))
        print(f"New State: {update1.current_state}")
        
        # Step 3: Store business data
        print("\n--- Step 3: Store business data ---")
        store1 = stub.StoreExtractedData(pb.StoreExtractedDataRequest(
            session_id=session_id,
            data_json='{"business_type": "custom_cake_shop", "business_name": "Rina Cakes"}',
            merge=True
        ))
        print(f"Stored: {store1.updated_data_json}")
        
        # Step 4: Transition to confirming_data
        print("\n--- Step 4: Confirm data ---")
        update2 = stub.UpdateState(pb.UpdateStateRequest(
            session_id=session_id,
            new_state="confirming_data"
        ))
        print(f"New State: {update2.current_state}")
        
        # Step 5: Get final context
        print("\n--- Step 5: Get final context ---")
        final_ctx = stub.GetContext(pb.GetContextRequest(session_id=session_id))
        print(f"Final State: {final_ctx.current_state}")
        print(f"Final Data: {final_ctx.extracted_data_json}")
        print(f"History Turns: {len(final_ctx.conversation_history)}")
        
        assert final_ctx.current_state == "confirming_data"
        assert len(final_ctx.conversation_history) > 0
        
        print("\n✅ Test 5 PASSED - Full workflow working!")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def test_clear_session():
    """Test 6: Clear session"""
    print("\n" + "="*60)
    print("TEST 6: Clear Session")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7016')
    stub = pb_grpc.ConversationManagerStub(channel)
    
    try:
        session_id = "test-clear"
        
        # Create session
        stub.UpdateState(pb.UpdateStateRequest(
            session_id=session_id,
            new_state="collecting_info"
        ))
        
        # Clear it
        request = pb.ClearSessionRequest(session_id=session_id)
        response = stub.ClearSession(request)
        
        print(f"\n✅ Status: {response.status}")
        print(f"Message: {response.message}")
        
        assert response.status == "success"
        
        print("\n✅ Test 6 PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "="*60)
    print("CONVERSATION MANAGER - STATE MACHINE TESTS")
    print("Testing: Redis integration, state transitions, data storage")
    print("="*60)
    
    results = []
    results.append(("Get Context - New Session", test_get_context_new_session()))
    results.append(("Update State - Transition", test_update_state_transition()))
    results.append(("Store Extracted Data", test_store_extracted_data()))
    results.append(("Get Next Question", test_get_next_question()))
    results.append(("Full Workflow", test_full_workflow()))
    results.append(("Clear Session", test_clear_session()))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! State machine is SOLID!")
        sys.exit(0)
    else:
        print("\n⚠️ Some tests failed. Check errors above.")
        sys.exit(1)
