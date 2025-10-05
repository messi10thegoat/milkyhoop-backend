# Recommended Clean Build Sequence

## Phase 1: Infrastructure Services
**Priority: HIGHEST - Required by all others**
1. **postgres** - Database service
2. **redis** - Cache and session storage

### Assessment Focus:
- Database schema compatibility
- Data volume preservation
- Network connectivity

---

## Phase 2: Core Independent Services  
**Priority: CRITICAL - Core functionality**

### 2A. Authentication Foundation
3. **auth_service** - JWT token management
   - Dependencies: postgres
   - Risk: LOW (simple service)
   - Test: JWT token generation/validation

### 2B. Data Services
4. **ragcrud_service** - FAQ database operations
   - Dependencies: postgres, redis
   - Risk: MEDIUM (complex queries)
   - Test: FAQ search functionality

5. **ragllm_service** - AI response generation  
   - Dependencies: postgres (minimal)
   - Risk: MEDIUM (OpenAI integration)
   - Test: Response generation with context

---

## Phase 3: Intelligence Layer
**Priority: CRITICAL - Intelligence routing**

6. **tenant_parser** - Intent classification
   - Dependencies: postgres
   - Risk: MEDIUM (ML models)
   - Test: Intent classification accuracy

7. **intent_parser** - Natural language understanding
   - Dependencies: postgres  
   - Risk: MEDIUM (NLU processing)
   - Test: Entity extraction

---

## Phase 4: Orchestration Core
**Priority: CRITICAL - Core business logic**

8. **cust_orchestrator** - 4-tier intelligence routing
   - Dependencies: ragcrud_service, ragllm_service, tenant_parser
   - Risk: HIGH (complex dependencies, FAQ context critical)
   - Test: Complete FAQ context flow (3000+ chars)

---

## Phase 5: Gateway Layer
**Priority: CRITICAL - External interface**

9. **api_gateway** - Main entry point
   - Dependencies: cust_orchestrator, auth_service
   - Risk: HIGH (routing complexity)
   - Test: End-to-end customer queries

---

## Phase 6: Supporting Services
**Priority: MODERATE - Feature enhancements**

10. **memory_service** - Context management
11. **ragindex_service** - Search indexing
12. **complaint_service** - Complaint handling

---

## Phase 7: Development Services
**Priority: LOW - Development features**

13. **business-service**, **chatbot_service**, **conversation_service**
14. **order_service**, **payment_service**, **tenant_manager**
15. **flow-executor**, **hoop-registry**, **visualhoop-compiler**

