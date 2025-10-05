import os
import grpc
import asyncio
import logging
import json
from typing import List, Tuple, Optional
import hashlib
import redis
from openai import OpenAI
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from fuzzywuzzy import fuzz
from app.prisma_client import prisma
from milkyhoop_prisma.models import RagDocument
from milkyhoop_protos import ragindex_service_pb2 as index_pb
from milkyhoop_protos import ragindex_service_pb2_grpc as index_pb_grpc

# ===== CONFIGURATION =====
openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=openai_api_key) if openai_api_key else None

# Fuzzy search thresholds - tuned for production
WORD_OVERLAP_THRESHOLD = 0.4  # 40% word overlap minimum
SEMANTIC_SIMILARITY_THRESHOLD = 0.60  # 75% semantic similarity
FUZZY_STRING_THRESHOLD = 70  # 70% string similarity (FuzzyWuzzy)
MAX_RESULTS = 5  # Maximum results to return

# Setup logging
logger = logging.getLogger(__name__)

# ===== CORE CRUD OPERATIONS =====

async def create_rag_document(tenant_id: str, title: str, content: str) -> RagDocument:
    """Create document with tenant isolation and vector indexing"""
    
    # Generate embedding first
    embedding_list = []
    if client:
        try:
            embedding_response = await asyncio.to_thread(
                client.embeddings.create,
                model="text-embedding-ada-002",
                input=content
            )
            embedding = embedding_response.data[0].embedding
            embedding_list = json.dumps(embedding)  # Serialize as JSON string
        except Exception as e:
            logger.warning(f"Failed to generate embedding: {e}")
    
    # Create document with embeddings
    doc = await prisma.ragdocument.create(data={
        "tenantId": tenant_id,
        "title": title,
        "content": content,
        "embeddings": embedding_list  # Save embeddings to DB as string
    })
    
    # Index to vector service (if needed)
    if client and embedding_list:
        try:
            async with grpc.aio.insecure_channel("ragindex_service:5006") as channel:
                stub = index_pb_grpc.RagIndexServiceStub(channel)
                await stub.IndexDocument(index_pb.IndexDocumentRequest(
                    doc_id=doc.id,
                    embedding=json.loads(embedding_list)  # Convert back to list for gRPC
                ))
        except Exception as e:
            logger.warning(f"Failed to index document {doc.id}: {e}")
    
    return doc

async def get_rag_document(id: int) -> Optional[RagDocument]:
    """Get document by ID"""
    return await prisma.ragdocument.find_unique(where={"id": id})

async def list_rag_documents(tenant_id: str) -> List[RagDocument]:
    """List all documents for tenant"""
    return await prisma.ragdocument.find_many(where={"tenantId": tenant_id})

async def update_rag_document(id: int, title: str, content: str) -> RagDocument:
    """Update document by ID"""
    return await prisma.ragdocument.update(
        where={"id": id},
        data={"title": title, "content": content}
    )

async def delete_rag_document(id: int) -> RagDocument:
    """Delete document by ID"""
    return await prisma.ragdocument.delete(where={"id": id})

# ===== DIVINE-LEVEL FUZZY SEARCH IMPLEMENTATION =====

async def fuzzy_search_rag_documents(
    tenant_id: str, 
    search_content: str, 
    similarity_threshold: float = SEMANTIC_SIMILARITY_THRESHOLD
) -> List[RagDocument]:
    """
    🎯 DIVINE-LEVEL FUZZY SEARCH ALGORITHM
    
    Multi-tier fuzzy matching strategy:
    1. Exact matching (highest priority)
    2. Word overlap similarity (fast)
    3. Fuzzy string matching (typo tolerance)
    4. Semantic vector similarity (context understanding)
    5. Hybrid scoring with confidence ranking
    
    Returns up to 5 best matches sorted by relevance score
    """
    
    # CRITICAL FIX: Ensure prisma connection 
    if not prisma.is_connected():                
        await prisma.connect()                  

    # Get all tenant documents
    docs = await prisma.ragdocument.find_many(where={"tenantId": tenant_id})
    
    if not docs:
        logger.info(f"No documents found for tenant: {tenant_id}")
        return []
    
    logger.info(f"🔍 Fuzzy searching {len(docs)} documents for: '{search_content}'")
    
    # Prepare search data
    search_lower = search_content.lower().strip()
    search_words = search_lower.split()
    
    # Results container: [(doc, score, match_type)]
    scored_matches = []
    
    # Generate query embedding with Redis caching
    query_embedding = None

    # Create cache key
    query_hash = hashlib.md5(search_content.encode()).hexdigest()
    cache_key = f"faq:{tenant_id}:{query_hash}"

    # Try Redis cache first
    try:
        redis_client = redis.Redis(host='milkyhoop-dev-redis-1', port=6379, password='MilkyRedis2025Secure', decode_responses=True)
        cached_embedding = redis_client.get(cache_key)
        if cached_embedding:
            query_embedding = np.array(json.loads(cached_embedding))
            logger.debug("✅ Query embedding loaded from cache")
        else:
            # Generate and cache
            if client:
                query_response = await asyncio.to_thread(
                    client.embeddings.create,
                    model="text-embedding-ada-002", 
                    input=search_content
                )
                query_embedding = np.array(query_response.data[0].embedding)
                redis_client.setex(cache_key, 3600, json.dumps(query_embedding.tolist()))
                logger.debug("✅ Query embedding generated and cached")
    except Exception as e:
        logger.warning(f"Cache failed, using API: {e}")
        if client:
            query_response = await asyncio.to_thread(
                client.embeddings.create,
                model="text-embedding-ada-002",
                input=search_content
            )
            query_embedding = np.array(query_response.data[0].embedding)
    
    # ===== MULTI-TIER MATCHING ALGORITHM =====
    
    for doc in docs:
        doc_text = f"{doc.title} {doc.content}".lower()
        doc_content_lower = doc.content.lower()
        doc_title_lower = doc.title.lower()
        
        max_score = 0.0
        best_match_type = "no_match"
        
        # ===== TIER 1: EXACT MATCHING (Priority: 1.0) =====
        if search_lower in doc_text:
            max_score = 1.0
            best_match_type = "exact"
            logger.debug(f"📍 Exact match found in doc {doc.id}: {doc.title[:30]}...")
        
        # ===== TIER 2: WORD OVERLAP SIMILARITY (Priority: 0.9) =====
        else:
            overlap_score = calculate_word_overlap(search_words, doc_text)
            if overlap_score >= WORD_OVERLAP_THRESHOLD:
                max_score = max(max_score, overlap_score * 0.9)
                best_match_type = "word_overlap"
                logger.debug(f"📝 Word overlap match ({overlap_score:.2f}) in doc {doc.id}")
        
        # ===== TIER 3: FUZZY STRING MATCHING (Priority: 0.8) =====
        fuzzy_title_score = fuzz.token_sort_ratio(search_lower, doc_title_lower) / 100.0
        fuzzy_content_score = fuzz.partial_ratio(search_lower, doc_content_lower) / 100.0
        fuzzy_score = max(fuzzy_title_score, fuzzy_content_score)
        
        if fuzzy_score >= (FUZZY_STRING_THRESHOLD / 100.0):
            max_score = max(max_score, fuzzy_score * 0.8)
            if best_match_type == "no_match":
                best_match_type = "fuzzy_string"
            logger.debug(f"🎯 Fuzzy string match ({fuzzy_score:.2f}) in doc {doc.id}")
        
        # ===== TIER 4: SEMANTIC VECTOR SIMILARITY (Priority: 0.85) =====
        if query_embedding is not None:
            try:             
                # Use cached embeddings via raw SQL (bypass prisma limitation)
                result = await prisma.query_raw(
                    'SELECT embeddings FROM "RagDocument" WHERE id = $1', doc.id
                )
                if result and result[0]['embeddings'] and result[0]['embeddings'] not in [None, "", "[]"]:
                    doc_embedding = np.array(result[0]["embeddings"])  # JSONB returns Python list
                else:
                    # Generate only if missing (backward compatibility)
                    doc_response = await asyncio.to_thread(
                        client.embeddings.create,
                        model="text-embedding-ada-002",
                        input=doc.content
                    )
                    doc_embedding = np.array(doc_response.data[0].embedding)

                # Calculate cosine similarity
                semantic_score = cosine_similarity(
                    query_embedding.reshape(1, -1),
                    doc_embedding.reshape(1, -1)
                )[0][0]

                if semantic_score >= similarity_threshold:
                    max_score = max(max_score, semantic_score * 0.85)
                    if best_match_type == "no_match":
                        best_match_type = "semantic"
                    logger.debug(f"🧠 Semantic match ({semantic_score:.2f}) in doc {doc.id}")
                    
            except Exception as e:
                logger.warning(f"Semantic similarity failed for doc {doc.id}: {e}")
        
        # ===== TIER 5: HYBRID SCORING BOOST =====
        # Boost score if multiple matching methods agree
        if max_score > 0:
            # Count matching methods
            methods_count = sum([
                search_lower in doc_text,  # exact
                overlap_score >= WORD_OVERLAP_THRESHOLD if 'overlap_score' in locals() else False,  # word overlap
                fuzzy_score >= (FUZZY_STRING_THRESHOLD / 100.0),  # fuzzy
                # semantic handled separately due to async nature
            ])
            
            if methods_count >= 2:
                max_score = min(max_score * 1.1, 1.0)  # 10% boost, capped at 1.0
                logger.debug(f"🚀 Hybrid boost applied to doc {doc.id}")
        
        # Add to results if score is significant
        if max_score > 0.3:  # Minimum confidence threshold
            scored_matches.append((doc, max_score, best_match_type))
    
    # ===== RANKING & RESULT SELECTION =====
    
    # Sort by score (highest first), then by match type priority
    match_type_priority = {
        "exact": 4,
        "semantic": 3,
        "word_overlap": 2,
        "fuzzy_string": 1,
        "no_match": 0
    }
    
    scored_matches.sort(
        key=lambda x: (x[1], match_type_priority.get(x[2], 0)), 
        reverse=True
    )
    
    # Get top results
    top_matches = scored_matches[:MAX_RESULTS]
    
    # Log results summary
    if top_matches:
        logger.info(f"✅ Found {len(top_matches)} matches:")
        for i, (doc, score, match_type) in enumerate(top_matches):
            logger.info(f"  {i+1}. Doc {doc.id} - Score: {score:.3f} - Type: {match_type} - Title: {doc.title[:40]}...")
    else:
        logger.info(f"❌ No fuzzy matches found for: '{search_content}'")
    
    return [(doc, score) for doc, score, match_type in top_matches]

def calculate_word_overlap(search_words: List[str], doc_text: str) -> float:
    """Calculate word overlap score between search terms and document"""
    if not search_words:
        return 0.0
    
    doc_words = set(doc_text.split())
    matches = sum(1 for word in search_words if word in doc_words)
    return matches / len(search_words)

async def update_rag_document_by_search(
    tenant_id: str, 
    search_content: str, 
    new_content: str
) -> RagDocument:
    """
    🎯 DIVINE-LEVEL UPDATE WITH FUZZY SEARCH
    
    Sophisticated document updating with multi-tier fallback strategy:
    1. Advanced fuzzy search (primary)
    2. Exact string search (fallback)
    3. Partial matching (last resort)
    """
    
    logger.info(f"🔄 Updating document for tenant {tenant_id}: '{search_content}' → '{new_content[:50]}...'")
    
    # ===== TIER 1: ADVANCED FUZZY SEARCH =====
    try:
        docs = await fuzzy_search_rag_documents(
            tenant_id=tenant_id, 
            search_content=search_content, 
            similarity_threshold=0.6  # Lower threshold for updates
        )
        
        if docs:
            doc = docs[0]  # Use highest-scored match
            logger.info(f"✅ Fuzzy search found doc {doc.id}: {doc.title[:40]}...")
        else:
            logger.info("📋 Fuzzy search returned no results, trying exact search...")
            docs = None
            
    except Exception as e:
        logger.error(f"❌ Fuzzy search failed: {e}")
        docs = None
    
    # ===== TIER 2: EXACT STRING SEARCH FALLBACK =====
    if not docs:
        try:
            docs = await prisma.ragdocument.find_many(
                where={
                    "tenantId": tenant_id,
                    "OR": [
                        {"content": {"contains": search_content, "mode": "insensitive"}},
                        {"title": {"contains": search_content, "mode": "insensitive"}}
                    ]
                }
            )
            
            if docs:
                doc = docs[0]
                logger.info(f"✅ Exact search found doc {doc.id}: {doc.title[:40]}...")
            else:
                logger.warning("📋 Exact search also returned no results, trying partial matching...")
                
        except Exception as e:
            logger.error(f"❌ Exact search failed: {e}")
            docs = None
    
    # ===== TIER 3: PARTIAL MATCHING LAST RESORT =====
    if not docs:
        try:
            # Try searching with individual words
            search_words = search_content.lower().split()
            if search_words:
                conditions = []
                for word in search_words:
                    conditions.extend([
                        {"content": {"contains": word, "mode": "insensitive"}},
                        {"title": {"contains": word, "mode": "insensitive"}}
                    ])
                
                docs = await prisma.ragdocument.find_many(
                    where={
                        "tenantId": tenant_id,
                        "OR": conditions
                    }
                )
                
                if docs:
                    doc = docs[0]
                    logger.info(f"✅ Partial search found doc {doc.id}: {doc.title[:40]}...")
                    
        except Exception as e:
            logger.error(f"❌ Partial search failed: {e}")
    
    # ===== FINAL VALIDATION =====
    if not docs:
        error_msg = f"No document found for tenant '{tenant_id}' containing: '{search_content}'"
        logger.error(f"❌ {error_msg}")
        raise Exception(error_msg)
    
    # ===== DOCUMENT UPDATE =====
    doc = docs[0]
    
    try:
        updated_doc = await prisma.ragdocument.update(
            where={"id": doc.id},
            data={"content": new_content}
        )
        
        logger.info(f"✅ Successfully updated doc {doc.id}: {doc.title[:40]}...")
        logger.info(f"📝 Content updated: '{doc.content[:50]}...' → '{new_content[:50]}...'")
        
        return updated_doc
        
    except Exception as e:
        error_msg = f"Failed to update document {doc.id}: {e}"
        logger.error(f"❌ {error_msg}")
        raise Exception(error_msg)

# ===== PERFORMANCE MONITORING =====

async def search_performance_benchmark(tenant_id: str, test_queries: List[str]) -> dict:
    """Benchmark fuzzy search performance for optimization"""
    results = {
        "total_queries": len(test_queries),
        "successful_matches": 0,
        "average_response_time": 0.0,
        "match_types": {"exact": 0, "fuzzy_string": 0, "word_overlap": 0, "semantic": 0}
    }
    
    import time
    total_time = 0.0
    
    for query in test_queries:
        start_time = time.time()
        
        try:
            matches = await fuzzy_search_rag_documents(tenant_id, query)
            if matches:
                results["successful_matches"] += 1
        except Exception as e:
            logger.error(f"Benchmark query failed: {query} - {e}")
        
        query_time = time.time() - start_time
        total_time += query_time
    
    results["average_response_time"] = total_time / len(test_queries) if test_queries else 0.0
    
    return results

# ===== ADVANCED CRUD OPERATIONS =====

async def bulk_create_rag_documents(tenant_id: str, documents: List[dict]) -> List[RagDocument]:
    """Bulk create documents with optimized embedding generation"""
    created_docs = []
    
    for doc_data in documents:
        try:
            doc = await create_rag_document(
                tenant_id=tenant_id,
                title=doc_data.get("title", ""),
                content=doc_data.get("content", "")
            )
            created_docs.append(doc)
            logger.info(f"✅ Created document {doc.id}: {doc.title[:30]}...")
        except Exception as e:
            logger.error(f"❌ Failed to create document: {e}")
    
    logger.info(f"📝 Bulk created {len(created_docs)}/{len(documents)} documents for tenant {tenant_id}")
    return created_docs

async def search_documents_by_tag(tenant_id: str, tag: str) -> List[RagDocument]:
    """Search documents by tag"""
    return await prisma.ragdocument.find_many(
        where={
            "tenantId": tenant_id,
            "tags": {"contains": tag, "mode": "insensitive"}
        }
    )

async def get_document_statistics(tenant_id: str) -> dict:
    """Get document statistics for tenant"""
    docs = await prisma.ragdocument.find_many(where={"tenantId": tenant_id})
    
    total_docs = len(docs)
    total_content_length = sum(len(doc.content) for doc in docs)
    avg_content_length = total_content_length / total_docs if total_docs > 0 else 0
    
    # Count documents with embeddings
    docs_with_embeddings = sum(1 for doc in docs if hasattr(doc, 'embeddings') and doc.embeddings)
    
    return {
        "total_documents": total_docs,
        "total_content_length": total_content_length,
        "average_content_length": avg_content_length,
        "documents_with_embeddings": docs_with_embeddings,
        "embedding_coverage": (docs_with_embeddings / total_docs * 100) if total_docs > 0 else 0
    }

# ===== DATABASE MAINTENANCE =====

async def regenerate_embeddings(tenant_id: str) -> dict:
    """Regenerate embeddings for all documents of a tenant"""
    if not client:
        raise Exception("OpenAI client not configured")
    
    docs = await prisma.ragdocument.find_many(where={"tenantId": tenant_id})
    
    updated_count = 0
    failed_count = 0
    
    for doc in docs:
        try:
            # Generate new embedding
            embedding_response = await asyncio.to_thread(
                client.embeddings.create,
                model="text-embedding-ada-002",
                input=doc.content
            )
            embedding_list = json.dumps(embedding_response.data[0].embedding)
            
            # Update document
            await prisma.ragdocument.update(
                where={"id": doc.id},
                data={"embeddings": embedding_list}
            )
            
            updated_count += 1
            logger.info(f"✅ Regenerated embedding for doc {doc.id}")
            
        except Exception as e:
            failed_count += 1
            logger.error(f"❌ Failed to regenerate embedding for doc {doc.id}: {e}")
    
    logger.info(f"📊 Embedding regeneration complete: {updated_count} updated, {failed_count} failed")
    
    return {
        "total_documents": len(docs),
        "updated_count": updated_count,
        "failed_count": failed_count
    }

async def cleanup_orphaned_documents(tenant_id: str) -> int:
    """Clean up documents without content or with empty content"""
    deleted_docs = await prisma.ragdocument.delete_many(
        where={
            "tenantId": tenant_id,
            "OR": [
                {"content": ""},
                {"content": None}
            ]
        }
    )
    
    logger.info(f"🧹 Cleaned up {deleted_docs.count} orphaned documents for tenant {tenant_id}")
    return deleted_docs.count