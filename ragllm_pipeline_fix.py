import logging
import os
import asyncio
import numpy as np
import grpc
from openai import OpenAI
from fuzzywuzzy import fuzz
from sklearn.metrics.pairwise import cosine_similarity
# ✅ Import stub gRPC index_service dari path absolut
from milkyhoop_protos import ragindex_service_pb2 as index_pb
from milkyhoop_protos import ragindex_service_pb2_grpc as index_pb_grpc

logger = logging.getLogger("ragllm_service.llm_pipeline")

# ✅ Load OpenAI API Key
openai_api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=openai_api_key) if openai_api_key else None

# ✅ DIVINE LEVEL CONSTANTS
WORD_OVERLAP_THRESHOLD = 0.4      # 40% word overlap minimum
SEMANTIC_SIMILARITY_THRESHOLD = 0.75  # 75% semantic similarity
FUZZY_STRING_THRESHOLD = 70        # 70% string similarity (FuzzyWuzzy)
MIN_MATCH_THRESHOLD = 0.3          # 30% minimum match threshold

# ✅ Fungsi generate_embedding (real API call ke OpenAI)
async def generate_embedding(text: str) -> list:
    logger.info(f"💡 Generating embedding for text: {text}")
    if client:
        response = await asyncio.to_thread(
            client.embeddings.create,
            model="text-embedding-ada-002",
            input=text
        )
        embedding = response.data[0].embedding
    else:
        logger.warning("⚠️ OPENAI_API_KEY not set! Using random embedding as fallback.")
        embedding = np.random.rand(768).tolist()
    return embedding

# ✅ Fungsi utama: generate_answer → index → fetch doc → ekstrak isi → jawaban final
async def generate_answer(question: str, tenant_id: str = "tenant_001") -> str:
    # Use milkybot_onboarding for onboarding conversations, otherwise use provided tenant_id
    search_tenant = tenant_id  # Always use provided tenant
    logger.info(f"💡 Generating answer for tenant={tenant_id} | question={question}")

    # 1️⃣ Buat embedding dari pertanyaan user
    query_embedding = await generate_embedding(question)

    # 2️⃣ Cari dokumen terdekat dari ragindex_service
    async with grpc.aio.insecure_channel("ragindex_service:5006") as index_channel:
        index_stub = index_pb_grpc.RagIndexServiceStub(index_channel)
        search_request = index_pb.SearchDocumentRequest(
            embedding=query_embedding,
            tenant_id=search_tenant,
            top_k=3
        )
        search_response = await index_stub.SearchDocument(search_request)
        logger.info(f"🔎 Retrieved {len(search_response.results)} search results from ragindex_service.")

    # 3️⃣ Ambil isi dokumen dari ragcrud_service
    if search_response.results:
        top_doc_id = search_response.results[0].doc_id
        
        # Import stub crud di dalam fungsi
        from milkyhoop_protos import ragcrud_service_pb2 as crud_pb
        from milkyhoop_protos import ragcrud_service_pb2_grpc as crud_pb_grpc
        
        async with grpc.aio.insecure_channel("ragcrud_service:5001") as crud_channel:
            crud_stub = crud_pb_grpc.RagCrudServiceStub(crud_channel)
            doc_request = crud_pb.GetRagDocumentRequest(id=top_doc_id)
            doc_response = await crud_stub.GetRagDocument(doc_request)

        # 4️⃣ Susun jawaban final → ekstrak hanya isi `A:` dari dokumen
        lines = doc_response.content.strip().splitlines()
        answer_lines = [line for line in lines if line.strip().startswith("A:")]
        
        if answer_lines:
            final_answer = answer_lines[0].replace("A:", "").strip()
        else:
            final_answer = doc_response.content.strip()

        # 5️⃣ LLM Reasoning dengan context yang valid
        from app.llm.llm_client import call_llm_reasoning
        prompt = f"""
Kamu adalah Milky, mainbot platform MilkyHoop yang membantu user setup chatbot. Berdasarkan konteks berikut di bawah, jawablah dengan lugas, tapi tetap ramah, singkat-singkat saja jawabnya. Pastikan untuk menjawab pertanyaan se-relevan mungkin. Langsung direct pertanyaan user, JANGAN kasih kata pengantar sebelum menjawab, misalnya "tentu, berikut adalah respon alami ... dan seterusnya". langsung saja ke jawaban atas pertanyaan atau perintah. 

Context:
{doc_response.content.strip()}

Pertanyaan:
{question}

Jawaban:"""
        logger.info("=== DEBUG PROMPT ===")
        logger.info(prompt)
        logger.info("=== END DEBUG ===")
        final_answer = await call_llm_reasoning(prompt)

    else:
        # 6️⃣ Fallback jika tidak ada dokumen ditemukan
        final_answer = "Maaf, tidak ada dokumen yang relevan ditemukan untuk pertanyaan ini."

    return final_answer


async def divine_fuzzy_search(query: str, documents: list, similarity_threshold: float = 0.75) -> tuple:
    """
    🚀 DIVINE LEVEL 5-TIER FUZZY SEARCH ALGORITHM
    Returns: (best_match_doc, max_score, match_type)
    """
    if not documents:
        return None, 0, "no_docs"
    
    best_match = None
    max_score = 0
    best_match_type = "none"
    
    # Prepare query for processing
    query_lower = query.lower()
    query_words = set(query_lower.split())
    
    logger.info(f"🔍 DIVINE FUZZY SEARCH: '{query}' across {len(documents)} documents")
    
    try:
        # Get query embedding for semantic search (OPTIMIZATION: Only once)
        query_embedding = None
        if client:
            response = await asyncio.to_thread(
                client.embeddings.create,
                model="text-embedding-ada-002",
                input=query
            )
            query_embedding = np.array(response.data[0].embedding)
    except Exception as e:
        logger.warning(f"⚠️ Embedding generation failed: {e}")
        query_embedding = None
    
    for doc in documents:
        doc_text = f"{doc.title} {doc.content}".lower()
        doc_words = set(doc_text.split())
        current_score = 0
        match_type = "none"
        
        # TIER 1: Exact String Matching (Priority: 1.0)
        if query_lower in doc_text:
            current_score = 1.0
            match_type = "exact"
            logger.debug(f"✅ TIER 1 EXACT: '{doc.title}' - Score: 1.0")
        
        # TIER 2: Word Overlap Similarity (Priority: 0.9)
        elif query_words:
            overlap = len(query_words.intersection(doc_words))
            overlap_score = overlap / len(query_words)
            
            if overlap_score >= WORD_OVERLAP_THRESHOLD:
                current_score = max(current_score, overlap_score * 0.9)
                match_type = "word_overlap"
                logger.debug(f"✅ TIER 2 OVERLAP: '{doc.title}' - Score: {current_score:.3f}")
        
        # TIER 3: Fuzzy String Matching (Priority: 0.8)
        try:
            fuzzy_score = fuzz.token_sort_ratio(query_lower, doc.content.lower()) / 100.0
            
            if fuzzy_score >= (FUZZY_STRING_THRESHOLD / 100.0):
                current_score = max(current_score, fuzzy_score * 0.8)
                match_type = "fuzzy_string"
                logger.debug(f"✅ TIER 3 FUZZY: '{doc.title}' - Score: {current_score:.3f}")
        except Exception as e:
            logger.warning(f"⚠️ Fuzzy matching failed for doc {doc.title}: {e}")
        
        # TIER 4: Semantic Vector Similarity (Priority: 0.85)
        if query_embedding is not None:
            try:
                # Generate document embedding
                doc_response = await asyncio.to_thread(
                    client.embeddings.create,
                    model="text-embedding-ada-002",
                    input=f"{doc.title} {doc.content}"
                )
                doc_embedding = np.array(doc_response.data[0].embedding)
                
                semantic_score = cosine_similarity([query_embedding], [doc_embedding])[0][0]
                
                if semantic_score >= similarity_threshold:
                    current_score = max(current_score, semantic_score * 0.85)
                    match_type = "semantic"
                    logger.debug(f"✅ TIER 4 SEMANTIC: '{doc.title}' - Score: {current_score:.3f}")
            except Exception as e:
                logger.warning(f"⚠️ Semantic matching failed for doc {doc.title}: {e}")
        
        # TIER 5: Hybrid Scoring Boost (10% boost if multiple methods agree)
        methods_count = sum([
            query_lower in doc_text,  # exact match
            query_words and len(query_words.intersection(doc_words)) / len(query_words) >= WORD_OVERLAP_THRESHOLD,  # word overlap
            fuzz.token_sort_ratio(query_lower, doc.content.lower()) >= FUZZY_STRING_THRESHOLD  # fuzzy
        ])
        
        if methods_count >= 2:
            current_score = min(current_score * 1.1, 1.0)  # Capped at 1.0
            logger.debug(f"🚀 TIER 5 HYBRID BOOST: '{doc.title}' - Final Score: {current_score:.3f}")
        
        # Select best match
        if current_score > max_score and current_score > MIN_MATCH_THRESHOLD:
            max_score = current_score
            best_match = doc
            best_match_type = match_type
    
    logger.info(f"🎯 DIVINE SEARCH RESULT: Score={max_score:.3f}, Type={best_match_type}, Match='{best_match.title if best_match else 'None'}'")
    return best_match, max_score, best_match_type


async def generate_conversational_response(query: str, context: str, mode: str = "conversation"):
    """
    🚀 DIVINE LEVEL CONVERSATIONAL RESPONSE GENERATOR
    Mode: 
    - conversation = natural chat (Setup Mode)
    - customer_service = FAQ-based response (Customer Mode)
    - execution = action trigger detected
    """
    from app.llm.llm_client import call_llm_reasoning

    logger.info(f"🎯 GENERATE RESPONSE: Mode={mode}, Query='{query}', Context='{context[:100]}...'")

    if mode == "conversation":
        prompt = f"""Kamu adalah Milky, asisten MilkyHoop yang membantu pemilik bisnis setup chatbot. Kamu BUKAN customer service bot.

User adalah pemilik bisnis yang sedang manage FAQ chatbot mereka.

Context: {context}
Query: {query}

Jawab sebagai asisten setup yang membantu manage FAQ. Kalau ada FAQ yang relevan, sebutkan isinya dan tawarkan untuk update/edit. Singkat, ramah, pakai emoticon.

Jawaban:"""

    elif mode == "customer_service":
        # Extract tenant_id from context
        tenant_id = context.replace("tenant: ", "").strip()
        
        # 🚀 DIVINE CUSTOMER MODE: Advanced RAG CRUD fuzzy search
        faq_context = None
        search_debug = {}
        
        try:
            from milkyhoop_protos import ragcrud_service_pb2 as crud_pb
            from milkyhoop_protos import ragcrud_service_pb2_grpc as crud_pb_grpc
            
            async with grpc.aio.insecure_channel("ragcrud_service:5001") as crud_channel:
                crud_stub = crud_pb_grpc.RagCrudServiceStub(crud_channel)
                docs_request = crud_pb.ListRagDocumentsRequest(tenant_id=tenant_id)
                docs_response = await crud_stub.ListRagDocuments(docs_request)
                
                search_debug['total_docs'] = len(docs_response.documents)
                search_debug['tenant_id'] = tenant_id
                search_debug['query'] = query
                
                # 🚀 DIVINE LEVEL FUZZY SEARCH
                best_match, max_score, match_type = await divine_fuzzy_search(
                    query, 
                    docs_response.documents, 
                    similarity_threshold=SEMANTIC_SIMILARITY_THRESHOLD
                )
                
                search_debug['max_score'] = max_score
                search_debug['match_type'] = match_type
                search_debug['best_match_title'] = best_match.title if best_match else None
                
                if best_match:
                    faq_context = best_match.content
                    logger.info(f"🎯 DIVINE MATCH FOUND: '{best_match.title}' (Score: {max_score:.3f}, Type: {match_type})")
                else:
                    faq_context = f"Tidak ditemukan FAQ untuk: {query}"
                    logger.info(f"❌ NO MATCH FOUND for query: '{query}'")
                    
        except Exception as e:
            logger.error(f"❌ DIVINE Customer Mode search error: {e}")
            faq_context = f"Terjadi kesalahan saat mencari FAQ: {query}"
            search_debug['error'] = str(e)
        
        # 🚀 DIVINE CUSTOMER SERVICE RESPONSE
        prompt = f"""Jawab pertanyaan customer berdasarkan informasi FAQ.

Informasi FAQ: {faq_context}
Pertanyaan: {query}

ATURAN KETAT:
- HANYA jawab jika pertanyaan EKSAKT match dengan topik yang ada di FAQ
- Periksa kesesuaian topik: pertanyaan harus tentang hal yang sama dengan FAQ
- Jika FAQ tentang produk A tapi pertanyaan tentang produk B: WAJIB defleksi
- Jika tidak ada FAQ yang eksakt relevan: "Informasi tersebut tidak tersedia"
- DILARANG menjawab dengan FAQ yang topiknya berbeda meski masih satu kategori bisnis

Jawaban:"""

        # 🚀 DIVINE DEBUG LOGGING
        logger.info(f"=== DIVINE CUSTOMER MODE DEBUG ===")
        logger.info(f"Search Debug: {search_debug}")
        logger.info(f"FAQ Context: '{faq_context}'")
        logger.info(f"Query: '{query}'")
        logger.info(f"=== END DIVINE CUSTOMER DEBUG ===")
        
    else:
        # Execution mode untuk action triggers
        prompt = f"""Extract action intent to JSON format:
Query: {query}
Context: {context}

Return JSON with intent and parameters."""

    # 🚀 DIVINE DEBUG LOGGING
    logger.info("=== DIVINE CONVERSATIONAL DEBUG ===")
    logger.info(f"Mode: {mode}")
    logger.info(f"Query: {query}")
    logger.info(f"Context: {context}")
    logger.info("=== END DIVINE DEBUG ===")

    # 🎯 CRITICAL FIX: Return the LLM response
    return await call_llm_reasoning(prompt)


async def detect_action_trigger(message: str) -> bool:
    """Detect: update aja, bikin dong, ganti, hapus"""
    triggers = ["update aja", "bikin dong", "ganti", "ubah", "hapus", "delete", "bikin", "buat"]
    detected = any(trigger in message.lower() for trigger in triggers)
    logger.debug(f"🔍 Action trigger detection for '{message}': {detected}")
    return detected