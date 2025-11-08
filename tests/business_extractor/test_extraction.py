#!/usr/bin/env python3
"""
Test business_extractor - GPT-3.5 Extraction
Phase 1 verification script
"""
import sys
import grpc
import json

# Add proto path
sys.path.insert(0, './backend/services/business_extractor/app')

import business_extractor_pb2 as pb
import business_extractor_pb2_grpc as pb_grpc


def test_extract_kue_custom():
    """Test Case 1: Kue custom business"""
    print("\n" + "="*60)
    print("TEST 1: Kue Custom Business Extraction")
    print("="*60)
    
    # Connect to service
    channel = grpc.insecure_channel('localhost:7015')
    stub = pb_grpc.BusinessExtractorStub(channel)
    
    # Test input
    message = "Gue jualan kue custom untuk ultah dan wedding, biasanya customer tanya harga dan delivery area"
    
    print(f"\nInput: {message}")
    print("\nCalling ExtractBusinessInfo RPC...")
    
    try:
        # Call RPC
        request = pb.ExtractBusinessInfoRequest(
            message=message,
            session_id="test-001"
        )
        
        response = stub.ExtractBusinessInfo(request)
        
        # Display results
        print("\n✅ EXTRACTION SUCCESSFUL!\n")
        print(f"Status: {response.status}")
        print(f"Business Name: {response.business_name or 'Not mentioned'}")
        print(f"Business Type: {response.business_type}")
        print(f"Target Customers: {response.target_customers or 'Not mentioned'}")
        print(f"Products/Services: {list(response.products_services) or 'None'}")
        print(f"Common Questions: {list(response.common_questions)}")
        print(f"Pricing Info: {response.pricing_info or 'Not mentioned'}")
        print(f"Operating Hours: {response.operating_hours or 'Not mentioned'}")
        print(f"Location/Delivery: {response.location_delivery or 'Not mentioned'}")
        print(f"Confidence Score: {response.confidence_score:.2f}")
        
        # Validate extraction
        assert response.status == "success", "Status should be success"
        assert response.business_type != "", "Business type should be extracted"
        assert len(response.common_questions) > 0, "Should extract common questions"
        
        print("\n✅ All assertions passed!")
        return True
        
    except grpc.RpcError as e:
        print(f"\n❌ gRPC Error: {e.code()}")
        print(f"Details: {e.details()}")
        return False
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False
    finally:
        channel.close()


def test_extract_skincare():
    """Test Case 2: Skincare business"""
    print("\n" + "="*60)
    print("TEST 2: Skincare Business Extraction")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7015')
    stub = pb_grpc.BusinessExtractorStub(channel)
    
    message = "Saya jual skincare natural untuk kulit sensitif, buka jam 9-5 sore, customer sering tanya halal certified dan bisa COD"
    
    print(f"\nInput: {message}")
    print("\nCalling ExtractBusinessInfo RPC...")
    
    try:
        request = pb.ExtractBusinessInfoRequest(
            message=message,
            session_id="test-002"
        )
        
        response = stub.ExtractBusinessInfo(request)
        
        print("\n✅ EXTRACTION SUCCESSFUL!\n")
        print(f"Business Type: {response.business_type}")
        print(f"Products: {list(response.products_services)}")
        print(f"Operating Hours: {response.operating_hours}")
        print(f"Common Questions: {list(response.common_questions)}")
        print(f"Confidence: {response.confidence_score:.2f}")
        
        assert response.status == "success"
        assert "skincare" in response.business_type.lower() or len(response.products_services) > 0
        
        print("\n✅ Test passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False
    finally:
        channel.close()


def test_generate_faq_suggestions():
    """Test Case 3: FAQ Generation"""
    print("\n" + "="*60)
    print("TEST 3: FAQ Suggestions Generation")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7015')
    stub = pb_grpc.BusinessExtractorStub(channel)
    
    print("\nGenerating FAQ suggestions for custom cake shop...")
    
    try:
        request = pb.GenerateFAQSuggestionsRequest(
            business_type="custom_cake_shop",
            business_name="Rina Cakes",
            products_services=["kue custom", "kue ultah", "wedding cake"],
            common_questions=["harga", "delivery"]
        )
        
        response = stub.GenerateFAQSuggestions(request)
        
        print(f"\n✅ Generated {len(response.suggested_faqs)} FAQ suggestions:\n")
        
        for i, faq in enumerate(response.suggested_faqs, 1):
            print(f"{i}. [{faq.priority.upper()}] {faq.question}")
            print(f"   Category: {faq.category}")
            print(f"   Answer: {faq.suggested_answer[:80]}...")
            print()
        
        assert response.status == "success"
        assert len(response.suggested_faqs) > 0
        
        print("✅ FAQ generation test passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False
    finally:
        channel.close()


def test_validate_business_data():
    """Test Case 4: Data Validation"""
    print("\n" + "="*60)
    print("TEST 4: Business Data Validation")
    print("="*60)
    
    channel = grpc.insecure_channel('localhost:7015')
    stub = pb_grpc.BusinessExtractorStub(channel)
    
    print("\nValidating incomplete business data...")
    
    try:
        # Test with incomplete data
        request = pb.ValidateBusinessDataRequest(
            business_name="",  # Missing
            business_type="cafe",
            products_services=["coffee", "pastry"],
            common_questions=[]  # Missing
        )
        
        response = stub.ValidateBusinessData(request)
        
        print(f"\n✅ VALIDATION RESULT:\n")
        print(f"Is Complete: {response.is_complete}")
        print(f"Completeness Score: {response.completeness_score:.2f}")
        print(f"Missing Fields: {list(response.missing_fields)}")
        print(f"Suggestions:")
        for suggestion in response.suggestions:
            print(f"  - {suggestion}")
        
        assert not response.is_complete, "Should detect incomplete data"
        assert len(response.missing_fields) > 0
        
        print("\n✅ Validation test passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False
    finally:
        channel.close()


if __name__ == "__main__":
    print("\n" + "="*60)
    print("BUSINESS EXTRACTOR - GPT-3.5 EXTRACTION TESTS")
    print("Phase 1 Implementation Verification")
    print("="*60)
    
    results = []
    
    # Run all tests
    results.append(("Extract Kue Custom", test_extract_kue_custom()))
    results.append(("Extract Skincare", test_extract_skincare()))
    results.append(("Generate FAQ Suggestions", test_generate_faq_suggestions()))
    results.append(("Validate Business Data", test_validate_business_data()))
    
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
        print("\n🎉 ALL TESTS PASSED! business_extractor is SOLID!")
        sys.exit(0)
    else:
        print("\n⚠️ Some tests failed. Check errors above.")
        sys.exit(1)
