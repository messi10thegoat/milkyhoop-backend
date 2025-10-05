#!/usr/bin/env python3
"""
Test script to identify available classes in tenant_parser_pb2_grpc
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, '/app/backend/services/tenant_parser')

try:
    print("=== TENANT_PARSER_PB2_GRPC ANALYSIS ===")
    
    # Import the module
    from app import tenant_parser_pb2_grpc
    
    print(f"Module loaded: {tenant_parser_pb2_grpc.__file__}")
    print(f"Module name: {tenant_parser_pb2_grpc.__name__}")
    
    # Get all attributes
    all_attrs = dir(tenant_parser_pb2_grpc)
    
    print("\n=== ALL AVAILABLE ATTRIBUTES ===")
    for attr in sorted(all_attrs):
        if not attr.startswith('_'):
            attr_obj = getattr(tenant_parser_pb2_grpc, attr)
            attr_type = type(attr_obj).__name__
            print(f"  {attr}: {attr_type}")
    
    print("\n=== SERVICE-RELATED ATTRIBUTES ===")
    service_attrs = [attr for attr in all_attrs if 'service' in attr.lower() or 'Service' in attr]
    for attr in service_attrs:
        attr_obj = getattr(tenant_parser_pb2_grpc, attr)
        print(f"  {attr}: {type(attr_obj).__name__}")
    
    print("\n=== SERVICER CLASSES ===")
    servicer_attrs = [attr for attr in all_attrs if 'Servicer' in attr]
    for attr in servicer_attrs:
        attr_obj = getattr(tenant_parser_pb2_grpc, attr)
        print(f"  {attr}: {type(attr_obj).__name__}")
        if hasattr(attr_obj, '__bases__'):
            print(f"    Bases: {[base.__name__ for base in attr_obj.__bases__]}")
    
    print("\n=== ADD_TO_SERVER FUNCTIONS ===")
    add_funcs = [attr for attr in all_attrs if 'add_' in attr and 'to_server' in attr]
    for attr in add_funcs:
        print(f"  {attr}")
    
    print("\n=== DESCRIPTOR INFO ===")
    if hasattr(tenant_parser_pb2_grpc, 'DESCRIPTOR'):
        descriptor = tenant_parser_pb2_grpc.DESCRIPTOR
        print(f"  DESCRIPTOR: {descriptor}")
        if hasattr(descriptor, 'services_by_name'):
            print(f"  Services: {list(descriptor.services_by_name.keys())}")
    
    print("\n🎯 ANALYSIS COMPLETE!")
    
except Exception as e:
    print(f"❌ Error analyzing proto: {e}")
    import traceback
    traceback.print_exc()
