# MilkyHoop Service Dependency Matrix

## Service Dependencies
```
api_gateway → depends on:
  - ragcrud_service (gRPC)
  - ragllm_service (gRPC)  
  - tenant_parser (gRPC)
  - auth_service (gRPC)
  - context_service (gRPC) [DISABLED]

ragcrud_service → depends on:
  - postgres/supabase (database)
  - redis (cache)

tenant_parser → depends on:
  - ragindex_service (gRPC)
  - ragllm_service (gRPC)
```

## Protobuf Version Matrix
- protobuf==6.32.0 (locked across all services)
- grpcio==1.74.0 (locked across all services)
- grpcio-tools==1.71.2 (locked across all services)

## Rebuild Strategy
1. Single Service: Restart all dependent services
2. Multiple Services: Coordinated shutdown → rebuild → coordinated startup
3. Protobuf Changes: Full cluster restart required
