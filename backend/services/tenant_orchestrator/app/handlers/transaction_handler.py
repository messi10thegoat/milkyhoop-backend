"""
Transaction Handler for Tenant Orchestrator
Handles financial transaction recording with inventory integration

Adapted from setup_orchestrator for tenant mode:
- Stateless operation (no session management)
- Returns string instead of ProcessSetupChatResponse
- Idempotency key uses tenant_id instead of session_id
- Direct business data access
"""

import logging
import json
import random
import time
from datetime import datetime, timezone, timedelta
from functools import lru_cache

import transaction_service_pb2
import inventory_service_pb2

# Atomic transaction imports
from backend.services.tenant_orchestrator.app.database import (
    get_tenant_config,
    create_transaction_atomic
)

# WIB Timezone (UTC+7)
WIB = timezone(timedelta(hours=7))


logger = logging.getLogger(__name__)

# ============================================
# MOTIVATIONAL CLOSINGS (69 messages)
# ============================================
MOTIVATIONAL_CLOSINGS = [
    # Doa & Berkah (1-15)
    "Semoga lancar usahanya ya kak 💪",
    "Terima kasih. Sehat selalu kak 🌟",
    "Mantap kak. Ditunggu transaksi selanjutnya 🚀",
    "Berkah terus ya kak 🙏",
    "Semoga cuan terus ya kak 💰",
    "Barakallah, semoga usahanya makin maju 🌙",
    "Sehat, berkah, dan sukses selalu! ✨",
    "Semoga rezeki makin lancar ya kak 🍀",
    "Usahanya diberkahi terus ya kak 🌺",
    "Semoga untung terus bisnisnya! 📈",
    "Barokah untuk usaha kakak 🤲",
    "Lancar jaya terus ya kak! 🎯",
    "Semoga omzetnya naik terus 💹",
    "Diberkahi selalu ya kak 🌸",
    "Rezeki melimpah untuk kakak! 🌊",

    # Motivasi & Semangat (16-30)
    "Keren! Terus semangat ya kak 🔥",
    "Mantap jiwa! Keep going kak 💪",
    "Luar biasa! Sukses terus ya 🌟",
    "Gaskeun terus kak! 🚀",
    "Semangat ngejar target ya kak! 🎯",
    "Top markotop kak! 👍",
    "Kereen banget! Lanjutkan 🔥",
    "Bisnis makin gacor ya kak! ⚡",
    "Mantul kak! Terus maju 🚀",
    "Salut buat konsistensinya kak! 👏",
    "Kece badai kak! 💯",
    "Jos gandos! Semangat terus 🌟",
    "Solid kak! Keep it up 💪",
    "Inspiring sekali! Lanjut ya 🔥",
    "Outstanding! Terus berkarya 🎨",

    # Apresiasi & Terima Kasih (31-45)
    "Makasih ya kak sudah percaya 🙏",
    "Terima kasih banyak kak! 💖",
    "Senang bisa bantu bisnis kakak 😊",
    "Appreciate your trust kak! 🤝",
    "Thanks for your business! 🌟",
    "Terima kasih sudah pakai MilkyHoop 💜",
    "Makasih ya kak, sukses selalu! 🙌",
    "Seneng deh bisa support kakak 🥰",
    "Thank you & good luck kak! 🍀",
    "Terima kasih, semoga membantu ya 💫",
    "Makasih udah manage bisnis di sini 📊",
    "Thanks! Semoga makin efisien 🎯",
    "Senang bisa jadi partner kakak 🤝",
    "Terima kasih, sukses terus! 🚀",
    "Appreciate it kak! Stay awesome 😎",

    # Wisdom & Tips (46-60)
    "Konsisten adalah kunci kesuksesan 🔑",
    "Small progress is still progress 📈",
    "Tracking itu penting, keep recording! 📝",
    "Data ga bohong, trust the process 📊",
    "Every transaction counts! 💯",
    "Pencatatan rapi = bisnis sehat 🏥",
    "Keep your books clean kak! 📚",
    "Financial discipline = success 💼",
    "Hari ini lebih baik dari kemarin 🌅",
    "Progress over perfection kak! ⭐",
    "Satu langkah lebih dekat ke goal 🎯",
    "Record today, profit tomorrow 📈",
    "Bisnis besar mulai dari pencatatan 📝",
    "Your dedication shows kak! 👀",
    "Consistency beats intensity 🔄",

    # Fun & Light (61-69)
    "Sip lah pokoknya! 👌",
    "Oke sip mantap jiwa! 😎",
    "Siap lanjut gas pol! 🏎️",
    "Cakep! Next level nih 🆙",
    "Woke! Bisnis modern banget 💻",
    "Smooth operator nih kak! 😏",
    "Pro banget sih! 🎮",
    "Chad energy detected! 💪",
    "Boss move! Respect 🫡"
]

# ============================================
# PHASE 1.2: LRU CACHE FOR PRODUCT LOOKUPS
# ============================================
# In-memory product cache (thread-safe dict)
# Stores {cache_key: produk_id} mapping
_product_cache: dict[str, str] = {}

@lru_cache(maxsize=1000)
def _get_product_cache_key(tenant_id: str, product_name: str) -> str:
    """Generate normalized cache key for product lookup"""
    return f"{tenant_id}:{product_name.lower().strip()}"




async def lookup_or_create_product(tenant_id: str, nama_produk: str, client_manager, trace_id: str) -> str:
    """
    Lookup product by name, create if not exists.
    Returns: produk_id (UUID string)

    PHASE 1.2: Added LRU cache for product lookups to reduce gRPC calls
    """
    import logging
    logger = logging.getLogger(__name__)

    # PHASE 1.2: Check cache first
    cache_key = _get_product_cache_key(tenant_id, nama_produk)

    if cache_key in _product_cache:
        logger.debug(f"[{trace_id}] 🚀 Product cache HIT: {nama_produk}")
        return _product_cache[cache_key]

    # Cache miss - proceed with gRPC call
    logger.debug(f"[{trace_id}] 💾 Product cache MISS: {nama_produk}")

    try:
        # Try to find existing product by name (fuzzy search)
        search_req = inventory_service_pb2.SearchProductsRequest(
            tenant_id=tenant_id,
            query=nama_produk,
            limit=1
        )
        search_resp = await client_manager.stubs['inventory'].SearchProducts(search_req)

        if search_resp.matches and len(search_resp.matches) > 0:
            # Use exact or high-confidence match
            match = search_resp.matches[0]
            if match.similarity_score >= 80:  # Only use if high confidence
                produk_id = match.produk_id
                logger.info(f"[{trace_id}] ✅ Product found: {nama_produk} → {produk_id} (score={match.similarity_score}%)")

                # PHASE 1.2: Cache the result
                _product_cache[cache_key] = produk_id

                return produk_id

        # Product not found or low confidence, create new
        create_req = inventory_service_pb2.CreateProductRequest(
            tenant_id=tenant_id,
            nama_produk=nama_produk,
            satuan="pcs",  # default unit
            harga_jual=0
        )
        create_resp = await client_manager.stubs['inventory'].CreateProduct(create_req)

        if create_resp.success and create_resp.product_id:
            new_produk_id = create_resp.product_id
            logger.info(f"[{trace_id}] ✨ Product created: {nama_produk} → {new_produk_id}")

            # PHASE 1.2: Cache the newly created product
            _product_cache[cache_key] = new_produk_id

            return new_produk_id
        else:
            logger.error(f"[{trace_id}] ❌ Failed to create product: {nama_produk} - {create_resp.message}")
            return ""

    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Product lookup/create error: {e}")
        return ""







def format_rupiah(rupiah_amount):
    """
    Format rupiah to Indonesian Rupiah (PUEBI + SAK EMKM compliant)

    Args:
        rupiah_amount: Amount in rupiah (integer)

    Returns:
        Formatted string: "Rp300.000" (titik sebagai pemisah ribuan)
    """
    formatted = f"{int(rupiah_amount):,}".replace(",", ".")
    return f"Rp{formatted}"


def format_number(amount):
    """
    Format number with thousand separator (no Rp prefix)

    Args:
        amount: Amount in rupiah (integer)

    Returns:
        Formatted string: "300.000" (titik sebagai pemisah ribuan)
    """
    return f"{int(amount):,}".replace(",", ".")


def wrap_product_name(name: str, max_words: int = 3) -> str:
    """
    Wrap product name after max_words words.
    If name has more than max_words, split into two lines.

    Args:
        name: Product name string
        max_words: Maximum words before wrapping (default 3)

    Returns:
        HTML string with <br> for line break if needed
    """
    words = name.split()
    if len(words) <= max_words:
        return name

    # Split into first line (3 words) and second line (rest)
    first_line = ' '.join(words[:max_words])
    second_line = ' '.join(words[max_words:])
    return f"{first_line}<br><span style='font-weight:400;font-size:12px;color:#737373'>{second_line}</span>"


# ============================================
# FORM MODE: DIRECT DATA EXTRACTION
# ============================================

def extract_form_data_directly(conversation_context: str, trace_id: str) -> dict:
    """
    Extract form data directly from conversation_context JSON.

    This bypasses LLM parsing for FORM mode - we trust the form data
    since it comes from structured frontend input.

    Args:
        conversation_context: JSON string with form_data key
        trace_id: Request trace ID for logging

    Returns:
        dict with extracted entities for transaction_handler:
        {
            "intent": "transaction_record",
            "entities": {
                "jenis_transaksi": "pembelian",
                "items": [{
                    "nama_produk": "...",
                    "jumlah": 10,
                    "satuan": "pcs",
                    "harga_satuan": 50000
                }],
                "metode_pembayaran": "tunai",
                "pihak": "Supplier Name",
                "keterangan": "Notes here",
                "total_nilai": 500000
            }
        }

        Returns None if not valid form data.
    """
    if not conversation_context:
        return None

    try:
        context_data = json.loads(conversation_context)
        form_data = context_data.get("form_data")

        if not form_data:
            return None

        logger.info(f"[{trace_id}] 📝 FORM MODE: Extracting data directly from form_data")

        # Map payment method to Indonesian
        payment_method_map = {
            "tunai": "tunai",
            "transfer": "transfer",
            "kredit": "kredit",
            "qris": "qris"  # Added for POS
        }

        # ============================================
        # HANDLE BOTH FORMATS:
        # 1. Kulakan (single-item): {product_name, quantity, unit, price_per_unit}
        # 2. POS (multi-item): {items: [{nama_produk, jumlah, satuan, harga_satuan}, ...]}
        # ============================================
        items = []

        if "items" in form_data and isinstance(form_data["items"], list):
            # POS Format: Multi-item array
            logger.info(f"[{trace_id}] 🛒 POS FORMAT: {len(form_data['items'])} items")
            for item in form_data["items"]:
                items.append({
                    "nama_produk": item.get("nama_produk", ""),
                    "product_id": item.get("product_id"),  # POS sends product_id
                    "barcode": item.get("barcode"),
                    "jumlah": item.get("jumlah", 0),
                    "satuan": item.get("satuan", "pcs"),
                    "harga_satuan": item.get("harga_satuan", 0),
                    "subtotal": item.get("subtotal", 0),
                    # HPP fields (if present)
                    "hpp_per_unit": item.get("hpp_per_unit"),
                    "harga_jual": item.get("harga_jual"),
                    "margin": item.get("margin"),
                    "margin_percent": item.get("margin_percent"),
                    "retail_unit": item.get("retail_unit")
                })
        else:
            # Kulakan Format: Single item
            logger.info(f"[{trace_id}] 📦 KULAKAN FORMAT: Single item")
            items.append({
                "nama_produk": form_data.get("product_name", ""),
                "jumlah": form_data.get("quantity", 0),
                "satuan": form_data.get("unit", "pcs"),
                "harga_satuan": form_data.get("price_per_unit", 0),
                # HPP & Margin fields (V006)
                "hpp_per_unit": form_data.get("hpp_per_unit"),
                "harga_jual": form_data.get("harga_jual"),
                "margin": form_data.get("margin"),
                "margin_percent": form_data.get("margin_percent"),
                "retail_unit": form_data.get("retail_unit")
            })

        # Build extracted entities in the same format as LLM parser output
        # NOTE: Use "nama_pihak" (not "pihak") to match existing handler expectations
        extracted = {
            "intent": "transaction_record",
            "entities": {
                "jenis_transaksi": form_data.get("transaction_type", "pembelian"),
                "items": items,
                "metode_pembayaran": payment_method_map.get(
                    form_data.get("payment_method", "tunai"),
                    "tunai"
                ),
                "nama_pihak": form_data.get("vendor_name", "") or form_data.get("supplier_name", ""),
                "keterangan": form_data.get("notes", ""),
                "total_nilai": form_data.get("total", 0) or form_data.get("total_amount", 0),
                "total_nominal": form_data.get("total", 0) or form_data.get("total_amount", 0),  # Alias for compatibility
                # Discount & PPN fields (V005)
                "discount_type": form_data.get("discount_type"),
                "discount_value": form_data.get("discount_value", 0),
                "include_vat": form_data.get("include_vat", False),
                # POS-specific fields
                "payment_amount": form_data.get("payment_amount"),
                "change": form_data.get("change")
            }
        }

        logger.info(f"[{trace_id}] ✅ Form data extracted ({len(items)} items): {json.dumps(extracted, ensure_ascii=False)[:300]}...")

        return extracted

    except json.JSONDecodeError as e:
        logger.warning(f"[{trace_id}] ⚠️ Failed to parse conversation_context as JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Error extracting form data: {e}")
        return None


# ============================================
# SPRINT 2.1: SMART PRODUCT RESOLUTION
# ============================================

async def _resolve_product(
    tenant_id: str,
    nama_produk: str,
    client_manager,
    trace_id: str
) -> dict:
    """
    Resolve product name to produk_id using fuzzy matching (Sprint 2.1)

    Uses inventory_service.SearchProducts with Levenshtein distance scoring.

    Args:
        tenant_id: Tenant identifier
        nama_produk: Product name from user input (e.g., "kopi", "kpi arabica")
        client_manager: gRPC client manager
        trace_id: Request trace ID for logging

    Returns:
        dict with resolution result:

        EXACT MATCH (similarity >90%):
        {
            'resolution': 'exact',
            'produk_id': 'uuid-string',
            'nama_produk': 'Kopi Arabica',
            'satuan': 'kg',
            'similarity_score': 95
        }

        AMBIGUOUS (similarity 70-90%):
        {
            'resolution': 'ambiguous',
            'matches': [
                {'produk_id': '...', 'nama_produk': 'Kopi Arabica', 'satuan': 'kg', 'similarity_score': 85},
                {'produk_id': '...', 'nama_produk': 'Kopi Robusta', 'satuan': 'kg', 'similarity_score': 82}
            ],
            'query': 'kopi'
        }

        NO MATCH (similarity <70%):
        {
            'resolution': 'no_match',
            'query': 'laptop'
        }

    Thresholds:
        >90%: Auto-select (exact match)
        70-90%: Ambiguous (ask user to choose)
        <70%: No match (propose new product creation)
    """
    try:
        # Call inventory_service.SearchProducts
        search_request = inventory_service_pb2.SearchProductsRequest(
            tenant_id=tenant_id,
            query=nama_produk,
            limit=10  # Get top 10 matches for ambiguity handling
        )

        search_response = await client_manager.stubs['inventory'].SearchProducts(search_request)

        matches = search_response.matches
        total_found = search_response.total_found

        logger.info(f"[{trace_id}] 🔍 Product search: query='{nama_produk}', found={total_found}")

        # No matches found
        if not matches or total_found == 0:
            logger.info(f"[{trace_id}] ❌ NO MATCH: '{nama_produk}' (no products in inventory)")
            return {
                'resolution': 'no_match',
                'query': nama_produk
            }

        # Get top match
        top_match = matches[0]
        top_score = top_match.similarity_score

        # EXACT MATCH: Auto-select if >90% similarity
        if top_score > 90:
            logger.info(
                f"[{trace_id}] ✅ EXACT MATCH: '{nama_produk}' → '{top_match.nama_produk}' "
                f"(score={top_score}%, produk_id={top_match.produk_id})"
            )
            return {
                'resolution': 'exact',
                'produk_id': top_match.produk_id,
                'nama_produk': top_match.nama_produk,
                'satuan': top_match.satuan,
                'similarity_score': top_score
            }

        # AMBIGUOUS: Multiple possible matches (60-90% similarity)
        # SPRINT 2.1 FIX: Check for ALL matches >= 60%, not just >= 70%
        if top_score >= 60:
            # Filter matches with score >= 60%
            potential_matches = [
                {
                    'produk_id': match.produk_id,
                    'nama_produk': match.nama_produk,
                    'satuan': match.satuan,
                    'current_stock': match.current_stock,
                    'similarity_score': match.similarity_score
                }
                for match in matches
                if match.similarity_score >= 60
            ]

            # If there are multiple matches >= 60%, show multiple choice
            if len(potential_matches) > 1:
                logger.info(
                    f"[{trace_id}] ⚠️ AMBIGUOUS: '{nama_produk}' has {len(potential_matches)} matches "
                    f"(top score={top_score}%)"
                )

                # Log top 3 matches for debugging
                for i, match in enumerate(potential_matches[:3], 1):
                    logger.info(f"[{trace_id}]   {i}. {match['nama_produk']} ({match['similarity_score']}%)")

                return {
                    'resolution': 'ambiguous',
                    'matches': potential_matches,
                    'query': nama_produk
                }

            # If only 1 match with 60-69%, ask "Apakah sama?"
            else:
                logger.info(
                    f"[{trace_id}] ⚠️ SOFT MATCH: '{nama_produk}' close to '{top_match.nama_produk}' "
                    f"(score={top_score}%)"
                )
                return {
                    'resolution': 'no_match',
                    'query': nama_produk,
                    'closest_match': {
                        'produk_id': top_match.produk_id,
                        'nama_produk': top_match.nama_produk,
                        'satuan': top_match.satuan,
                        'similarity_score': top_score
                    }
                }

        # NO MATCH: Top score <60%
        logger.info(
            f"[{trace_id}] ❌ NO MATCH: '{nama_produk}' (top score={top_score}% < 60% threshold)"
        )
        return {
            'resolution': 'no_match',
            'query': nama_produk,
            'closest_match': None  # No close match
        }

    except Exception as e:
        logger.error(f"[{trace_id}] ❌ Product resolution error: {e}", exc_info=True)
        # Fallback: treat as no match to allow user to continue
        return {
            'resolution': 'no_match',
            'query': nama_produk,
            'error': str(e)
        }








class TransactionHandler:
    """Handler for transaction recording operations in tenant mode"""
    
    @staticmethod
    async def handle_transaction_record(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle financial transaction recording - returns string response
        Route to transaction_service for SAK EMKM compliant transaction creation
        """
        t_handler_start = time.perf_counter()
        logger.info(f"[{trace_id}] Handling transaction_record intent")

        # ============================================
        # FORM MODE DETECTION
        # If message starts with [FORM], process directly
        # Otherwise redirect to form
        # ============================================
        raw_message = request.message or ""
        is_form_mode = raw_message.startswith("[FORM]")

        # If NOT from form (conversational), redirect to form
        if not is_form_mode:
            logger.info(f"[{trace_id}] 🔀 Redirecting conversational transaction to form")
            return (
                "Untuk transaksi pembelian, silakan gunakan form ya! 📝\n\n"
                "Klik tombol [+] di pojok kanan bawah, lalu pilih:\n"
                "• 'Beli Barang' untuk pembelian produk jadi\n"
                "• 'Beli Bahan' untuk pembelian bahan baku\n\n"
                "Form lebih cepat dan akurat! 😊"
            )

        logger.info(f"[{trace_id}] 📝 FORM MODE: Processing transaction directly")

        # ============================================
        # FORM MODE: Try direct extraction first
        # This bypasses LLM parser for better accuracy and speed
        # ============================================
        t_form_extract = time.perf_counter()
        form_extracted = extract_form_data_directly(
            conversation_context=request.conversation_context,
            trace_id=trace_id
        )
        logger.info(f"[{trace_id}] [PERF] form_extract: {(time.perf_counter() - t_form_extract)*1000:.0f}ms")

        if form_extracted:
            # Use directly extracted form data (bypasses LLM parser)
            transaction_entities = form_extracted.get("entities", {})
            logger.info(f"[{trace_id}] ✅ FORM MODE: Using direct extraction (bypassed LLM parser)")
        else:
            # Fallback to LLM parser output
            try:
                transaction_entities = json.loads(intent_response.entities_json)
            except:
                transaction_entities = {}
            logger.info(f"[{trace_id}] ⚠️ FORM MODE: Using LLM parser fallback")

        logger.info(f"[{trace_id}] Transaction entities: {transaction_entities}")
        
        # Validate required fields
        jenis_transaksi = transaction_entities.get("jenis_transaksi")
        total_nominal = transaction_entities.get("total_nominal")
        items = transaction_entities.get("items", [])
        
        # ============================================
        # PHASE 1.5: FIELD COMPLETENESS CHECK
        # Check if transaction has all required fields
        # If incomplete → save draft + ask question
        # ============================================
        from backend.services.tenant_orchestrator.app.services.field_validator import field_validator

        # CRITICAL FIX: Flatten items array into entities for field validation
        # field_validator expects flat structure: entities['nama_produk'], entities['jumlah']
        # but business_parser returns nested: entities['items'][0]['nama_produk']
        # IMPORTANT: Always flatten, even if items are incomplete (e.g., only nama_produk)
        if items and len(items) > 0:
            first_item = items[0]
            # Copy fields from first item to top level for validation
            # Use direct assignment (not setdefault) to ensure None values are set correctly
            if 'nama_produk' in first_item:
                transaction_entities['nama_produk'] = first_item.get('nama_produk')
            if 'jumlah' in first_item:
                transaction_entities['jumlah'] = first_item.get('jumlah')
            elif 'jumlah' not in transaction_entities:
                transaction_entities['jumlah'] = None  # Explicitly set None if missing
            if 'satuan' in first_item:
                transaction_entities['satuan'] = first_item.get('satuan')
            elif 'satuan' not in transaction_entities:
                transaction_entities['satuan'] = None
            if 'harga_satuan' in first_item:
                transaction_entities['harga_satuan'] = first_item.get('harga_satuan')
            elif 'harga_satuan' not in transaction_entities:
                transaction_entities['harga_satuan'] = None  # Explicitly set None if missing
            
            logger.info(f"[{trace_id}] Flattened items: nama_produk={transaction_entities.get('nama_produk')}, jumlah={transaction_entities.get('jumlah')}, harga_satuan={transaction_entities.get('harga_satuan')}")

        # ============================================
        # SPRINT 2.1: SMART PRODUCT RESOLUTION
        # Resolve product name to produk_id using fuzzy matching
        # This runs BEFORE field validation to ensure product is resolved
        # SKIP if produk_id already exists (from confirmation flow or draft)
        # FORM MODE: Skip fuzzy matching, auto-create product
        # ============================================
        nama_produk = transaction_entities.get('nama_produk')
        already_has_produk_id = transaction_entities.get('produk_id') is not None and transaction_entities.get('produk_id') != ''

        if nama_produk and not already_has_produk_id and is_form_mode:
            # FORM MODE: Auto-create product using lookup_or_create
            logger.info(f"[{trace_id}] 📝 FORM MODE: Auto-creating product '{nama_produk}'")

            try:
                t_product_lookup = time.perf_counter()
                produk_id = await lookup_or_create_product(
                    tenant_id=request.tenant_id,
                    nama_produk=nama_produk,
                    client_manager=client_manager,
                    trace_id=trace_id
                )
                logger.info(f"[{trace_id}] [PERF] product_lookup_or_create: {(time.perf_counter() - t_product_lookup)*1000:.0f}ms")

                if produk_id:
                    transaction_entities['produk_id'] = produk_id
                    logger.info(f"[{trace_id}] ✅ FORM MODE: Product resolved/created: {produk_id}")
                else:
                    logger.warning(f"[{trace_id}] ⚠️ FORM MODE: Product creation returned None, continuing anyway")

            except Exception as e:
                logger.error(f"[{trace_id}] ❌ FORM MODE: Product creation error: {str(e)}")
                # Continue without produk_id - transaction_service will handle it

        elif nama_produk and not already_has_produk_id:
            logger.info(f"[{trace_id}] 🔍 SPRINT 2.1: Resolving product '{nama_produk}'")

            product_resolution = await _resolve_product(
                tenant_id=request.tenant_id,
                nama_produk=nama_produk,
                client_manager=client_manager,
                trace_id=trace_id
            )

            resolution_type = product_resolution.get('resolution')

            if resolution_type == 'exact':
                # AUTO-SELECT: Use resolved product
                produk_id = product_resolution['produk_id']
                resolved_name = product_resolution['nama_produk']
                satuan = product_resolution['satuan']

                logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Exact match - '{nama_produk}' → '{resolved_name}' (produk_id={produk_id})")

                # Update entities with resolved product
                transaction_entities['produk_id'] = produk_id
                transaction_entities['nama_produk'] = resolved_name
                if not transaction_entities.get('satuan'):
                    transaction_entities['satuan'] = satuan

            elif resolution_type == 'ambiguous':
                # AMBIGUOUS: Save draft and ask user to choose
                matches = product_resolution['matches']
                query = product_resolution['query']

                logger.info(f"[{trace_id}] ⚠️ SPRINT 2.1: Ambiguous - '{query}' has {len(matches)} matches")

                # Generate consistent session_id
                request_session_id = getattr(request, 'session_id', '')
                if request_session_id and request_session_id.strip():
                    session_id = request_session_id
                else:
                    user_id = getattr(request, 'user_id', '')
                    session_id = f"{request.tenant_id}_{user_id}" if user_id else f"{request.tenant_id}_session"

                # Save draft with product resolution state
                draft_data = {
                    "jenis_transaksi": jenis_transaksi,
                    "entities": transaction_entities,
                    "product_resolution": product_resolution,
                    "awaiting": "product_selection"
                }

                import conversation_manager_pb2
                save_draft_request = conversation_manager_pb2.SaveDraftRequest(
                    tenant_id=request.tenant_id,
                    session_id=session_id,
                    draft_json=json.dumps(draft_data, ensure_ascii=False)
                )

                save_draft_response = await client_manager.stubs['conversation_manager'].SaveDraft(save_draft_request)

                if save_draft_response.success:
                    logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Draft saved with ambiguous product matches")

                    # Format product options
                    question = f"🔍 Ada beberapa produk mirip '{query}':\n\n"
                    for idx, match in enumerate(matches[:5], 1):  # Show max 5 options
                        question += f"{idx}. {match['nama_produk']} ({match['satuan']})\n"

                    question += f"\nMana yang dimaksud kak? Ketik angka 1-{min(len(matches), 5)}"

                    return question
                else:
                    logger.error(f"[{trace_id}] ❌ SPRINT 2.1: Failed to save draft for ambiguous product")
                    # Fallback: continue without product resolution

            elif resolution_type == 'no_match':
                # NO MATCH: Propose new product creation
                query = product_resolution['query']
                logger.info(f"[{trace_id}] ❌ SPRINT 2.1: No match - '{query}' (similarity <70%)")

                # Generate session_id
                request_session_id = getattr(request, 'session_id', '')
                if request_session_id and request_session_id.strip():
                    session_id = request_session_id
                else:
                    user_id = getattr(request, 'user_id', '')
                    session_id = f"{request.tenant_id}_{user_id}" if user_id else f"{request.tenant_id}_session"

                # Save draft with new product flag
                draft_data = {
                    "jenis_transaksi": jenis_transaksi,
                    "entities": transaction_entities,
                    "product_resolution": product_resolution,
                    "awaiting": "new_product_unit"
                }

                import conversation_manager_pb2
                save_draft_request = conversation_manager_pb2.SaveDraftRequest(
                    tenant_id=request.tenant_id,
                    session_id=session_id,
                    draft_json=json.dumps(draft_data, ensure_ascii=False)
                )

                save_draft_response = await client_manager.stubs['conversation_manager'].SaveDraft(save_draft_request)

                if save_draft_response.success:
                    logger.info(f"[{trace_id}] ✅ SPRINT 2.1: Draft saved for new product creation")

                    # Check for potential duplicates (60-69% similarity)
                    closest_match = product_resolution.get('closest_match')
                    if closest_match and closest_match.get('similarity_score', 0) >= 60:
                        similar_name = closest_match['nama_produk']
                        score = closest_match['similarity_score']
                        question = f"🆕 Produk '{query}' belum ada di inventory.\n\n"
                        question += f"⚠️ Mirip dengan '{similar_name}' ({score}%). Apakah sama?\n\n"
                        question += f"Kalau beda, berapa satuannya? (contoh: pcs, kg, liter)"
                    else:
                        question = f"🆕 Produk '{query}' belum ada di inventory.\n\n"
                        question += f"Berapa satuannya kak? (contoh: pcs, kg, liter)"

                    return question
                else:
                    logger.error(f"[{trace_id}] ❌ SPRINT 2.1: Failed to save draft for new product")
                    # Fallback: continue without product resolution

        # Check field completeness using new validator
        # CRITICAL: Only trigger multi-turn if transaction is INCOMPLETE
        # If complete, proceed directly to transaction creation (no questions!)
        missing_fields = field_validator.detect_missing_fields(jenis_transaksi, transaction_entities)
        logger.info(f"[{trace_id}] Field validation: jenis_transaksi={jenis_transaksi}, missing_fields={missing_fields}")
        
        if missing_fields:
            logger.info(f"[{trace_id}] 🔄 PHASE1.5: Transaction incomplete, starting multi-turn flow")
            logger.info(f"[{trace_id}] Missing fields: {missing_fields}")

            # Save draft to Redis
            # CRITICAL FIX: Generate consistent session_id for multi-turn continuity
            # Use request.session_id if provided, otherwise generate from tenant_id + user_id
            request_session_id = getattr(request, 'session_id', '')
            if request_session_id and request_session_id.strip():
                session_id = request_session_id
            else:
                # Generate consistent session_id from tenant_id + user_id
                user_id = getattr(request, 'user_id', '')
                if user_id:
                    session_id = f"{request.tenant_id}_{user_id}"
                else:
                    # Fallback: use tenant_id only
                    session_id = f"{request.tenant_id}_session"

            draft_data = {
                "jenis_transaksi": jenis_transaksi,
                "entities": transaction_entities,
                "missing_fields": missing_fields,
                "asking_for_field": missing_fields[0] if missing_fields else None
            }

            import conversation_manager_pb2
            save_draft_request = conversation_manager_pb2.SaveDraftRequest(
                tenant_id=request.tenant_id,
                session_id=session_id,
                draft_json=json.dumps(draft_data, ensure_ascii=False)
            )

            save_draft_response = await client_manager.stubs['conversation_manager'].SaveDraft(save_draft_request)

            if save_draft_response.success:
                logger.info(f"[{trace_id}] ✅ PHASE1.5: Draft saved to Redis")

                # Generate first clarification question
                first_missing_field = missing_fields[0]
                question = field_validator.generate_question(jenis_transaksi, first_missing_field)

                logger.info(f"[{trace_id}] 📋 PHASE1.5: Asking for {first_missing_field}")
                return question
            else:
                logger.error(f"[{trace_id}] ❌ Failed to save draft, falling back to old clarification")
                # Fallback to old clarification system
                from app.handlers.clarification_handler import ClarificationHandler
                clarification = await ClarificationHandler.handle_clarification(
                    request, ctx_response, intent_response,
                    trace_id, service_calls, progress, client_manager
                )
                if clarification:
                    return clarification
        
        # ============================================
        # FIX: Recalculate total_nominal from items if items exist
        # This ensures multi-item transactions have correct total
        # ============================================
        if items and len(items) > 0:
            calculated_total = sum(
                int(item.get("subtotal", 0)) 
                for item in items 
                if item.get("subtotal")
            )
            # Use calculated total if it's different from provided total (parser might have error)
            if calculated_total > 0 and (not total_nominal or abs(calculated_total - int(total_nominal or 0)) > 100):
                logger.warning(f"[{trace_id}] ⚠️ Total mismatch: provided={total_nominal}, calculated={calculated_total}. Using calculated.")
                total_nominal = calculated_total
            elif not total_nominal or total_nominal == 0:
                total_nominal = calculated_total
                logger.info(f"[{trace_id}] ✅ Recalculated total_nominal from items: {total_nominal}")

        # ============================================
        # RULE ENGINE: Try deterministic rules first
        # FORM MODE: Skip rule engine for faster response
        # ============================================
        rule_matched_data = None
        if is_form_mode:
            logger.info(f"[{trace_id}] 📝 FORM MODE: Skipping rule engine for faster response")
        else:
            try:
                import rule_engine_pb2

                # Build context for rule evaluation
                rule_context = {
                    "jenis_transaksi": jenis_transaksi,
                    "total_nominal": int(total_nominal) if total_nominal else 0,
                    "items": items,
                    "product_count": len(items)
                }

                # Add product names/categories for product mapping rules
                if items and len(items) > 0:
                    first_item = items[0]
                    rule_context["product_name"] = first_item.get("nama_produk", "").lower()
                    rule_context["product_category"] = first_item.get("category", "")
                    rule_context["quantity"] = first_item.get("jumlah", 0)

                # Call rule_engine
                rule_request = rule_engine_pb2.RuleRequest(
                    tenant_id=request.tenant_id,
                    rule_context=json.dumps(rule_context),
                    rule_type="product_mapping",  # Start with product mapping
                    trace_id=trace_id
                )

                rule_response = await client_manager.stubs['rule_engine'].EvaluateRule(rule_request)

                if rule_response.rule_matched and rule_response.confidence >= 0.95:
                    rule_matched_data = json.loads(rule_response.action_json)
                    logger.info(f"[{trace_id}] ✅ Rule matched: {rule_response.rule_id}, action={rule_matched_data}")

                    # Apply rule action to transaction_entities
                    if "akun_pendapatan" in rule_matched_data:
                        transaction_entities["akun_pendapatan"] = rule_matched_data["akun_pendapatan"]
                    if "akun_hpp" in rule_matched_data:
                        transaction_entities["akun_hpp"] = rule_matched_data["akun_hpp"]
                    if "apply_discount" in rule_matched_data:
                        transaction_entities["apply_discount"] = rule_matched_data["apply_discount"]
                    if "discount_rate" in rule_matched_data:
                        transaction_entities["discount_rate"] = rule_matched_data["discount_rate"]
                else:
                    logger.info(f"[{trace_id}] ℹ️ No rule matched (fallback to LLM entities)")

            except Exception as e:
                logger.warning(f"[{trace_id}] ⚠️ Rule engine error (non-critical): {e}")
                # Graceful fallback: continue with LLM entities if rule_engine fails

        if not jenis_transaksi:
            return "Hmm, transaksinya jenis apa nih? Penjualan, pembelian, atau beban?"
        
        # Allow negative total_nominal for returns (retur_penjualan, retur_pembelian)
        is_return = jenis_transaksi in ["retur_penjualan", "retur_pembelian"]
        if total_nominal is None or (total_nominal == 0 and not is_return):
            return "Nominalnya berapa nih? Biar aku catat dengan benar."
        
        # ============================================
        # DETECT MODAL & PRIVE (Bug Fix #3)
        # ============================================
        is_modal = transaction_entities.get("is_modal", False)
        is_prive = transaction_entities.get("is_prive", False)
        
        # Fallback: detect from jenis_transaksi or keywords
        if not is_modal and not is_prive:
            message_lower = request.message.lower()
            if "setor modal" in message_lower or "tambah modal" in message_lower or jenis_transaksi == "modal":
                is_modal = True
                jenis_transaksi = "beban"  # Use beban with is_modal flag
            elif "prive" in message_lower or "ambil" in message_lower and "pribadi" in message_lower:
                is_prive = True
                jenis_transaksi = "beban"  # Use beban with is_prive flag
        
        logger.info(f"[{trace_id}] is_modal={is_modal}, is_prive={is_prive}")

        # Build confirmation message
        nominal_display = format_rupiah(int(total_nominal))
        pihak = transaction_entities.get("nama_pihak", "")
        metode = transaction_entities.get("metode_pembayaran", "cash")
        status_bayar = transaction_entities.get("status_pembayaran", "lunas")
        
        # Build natural language confirmation based on type
        if is_modal:
            milky_response = f"Ok setor modal {nominal_display} "
        elif is_prive:
            milky_response = f"Ok ambil {nominal_display} untuk keperluan pribadi "
        elif jenis_transaksi == "retur_penjualan":
            milky_response = f"Ok retur penjualan {nominal_display} "
        elif jenis_transaksi == "retur_pembelian":
            milky_response = f"Ok retur pembelian ke supplier {nominal_display} "
        elif jenis_transaksi == "pembayaran_hutang":
            sisa = transaction_entities.get("sisa_hutang", 0)
            sisa_display = format_rupiah(int(sisa)) if sisa > 0 else "Lunas"
            milky_response = f"Ok bayar hutang {nominal_display}, sisa {sisa_display} "
        else:
            jenis_display = {
                "penjualan": "jual",
                "pembelian": "beli",
                "beban": "bayar"
            }.get(jenis_transaksi, jenis_transaksi)
            
            # Special formatting for beban_gaji with multiple employees
            skip_salary_formatting = False
            if jenis_transaksi == "beban" and transaction_entities.get("kategori_beban") == "beban_gaji":
                detail_karyawan = transaction_entities.get("detail_karyawan", "")
                periode_gaji = transaction_entities.get("periode_gaji", "")
                employee_salaries = transaction_entities.get("employee_salaries", {})
                
                if detail_karyawan and employee_salaries:
                    # Format with markdown table
                    milky_response = f"dengan rekapan sebagai berikut:\n\n"
                    milky_response += f"Bayar gaji bulan {periode_gaji}:\n\n"
                    
                    # Add each employee with their salary
                    for name, salary in employee_salaries.items():
                        # Format salary display (e.g., "3juta" or "3 juta")
                        if salary >= 1000000:
                            salary_display = f"{int(salary / 1000000)}juta"
                        elif salary >= 1000:
                            salary_display = f"{int(salary / 1000)}rb"
                        else:
                            salary_display = format_rupiah(salary)
                        milky_response += f"{name} Rp {salary_display}\n"
                    
                    milky_response += f"\nTotal {format_rupiah(int(total_nominal))}"
                    skip_salary_formatting = True  # Skip payment method and total addition
                else:
                    milky_response = f"Ok {jenis_display} "
            else:
                milky_response = f"Ok {jenis_display} "
        
        # Add items if present
        items = transaction_entities.get("items", [])
        if items and len(items) > 0:
            if len(items) == 1:
                # Single item: "1000 pcs kaos"
                first_item = items[0]
                jumlah = int(first_item.get("jumlah", 0))
                satuan = first_item.get("satuan", "pcs")
                nama_produk = first_item.get("nama_produk", "item").lower()
                milky_response += f"{jumlah} {satuan} {nama_produk} "
            else:
                # Multiple items: formatted list with icons
                # Determine icon based on transaction type
                if jenis_transaksi == "beban":
                    kategori = transaction_entities.get("kategori_beban", "")
                    if "gaji" in kategori.lower():
                        icon = "👥"
                        label = "Pembayaran Gaji Karyawan"
                    else:
                        icon = "💸"
                        label = "Pembayaran Beban"
                elif jenis_transaksi == "penjualan":
                    icon = "📦"
                    label = "Item Terjual"
                elif jenis_transaksi == "pembelian":
                    icon = "🛒"
                    label = "Item Dibeli"
                else:
                    icon = "📋"
                    label = "Item"
                
                milky_response = f"✅ Transaksi Dicatat!\n\n{icon} {label}:\n"
                for idx, item in enumerate(items):
                    nama = item.get("nama_produk", "item")
                    harga = int(item.get("harga_satuan", 0))
                    # Use tree characters for clean look
                    prefix = "└─" if idx == len(items) - 1 else "├─"
                    milky_response += f"{prefix} {nama}: {format_rupiah(harga)}\n"
                
                milky_response += f"\n💰 Total: {nominal_display}\n\n"
                milky_response += f"Bilang ya kak kalau ada koreksi 😊"
                
                # Skip the rest of single-item logic
                skip_single_item_logic = True
        
        # Add pihak (only for single item)
        if pihak and not locals().get('skip_single_item_logic', False):
            if jenis_transaksi == "penjualan":
                milky_response += f"ke {pihak} "
            elif jenis_transaksi == "pembelian":
                milky_response += f"dari {pihak} "
        
        # Add payment info (only for single item, skip for salary with breakdown)
        if not locals().get('skip_single_item_logic', False) and not locals().get('skip_salary_formatting', False):
            if status_bayar == "dp":
                nominal_dibayar = transaction_entities.get("nominal_dibayar", 0)
                if nominal_dibayar > 0:
                    bayar_display = format_rupiah(int(nominal_dibayar))
                    milky_response += f"DP {bayar_display} "
            
            # Indonesian payment method
            # Only add metode for single item
            metode_display = {
                "cash": "secara tunai",
                "transfer": "secara transfer",
                "tempo": "secara tempo"
            }.get(metode.lower(), metode)
            
            milky_response += f"{metode_display}. "
            milky_response += f"Total {nominal_display}, Bilang ya kak kalau ada koreksi 😊"

        # Call transaction_service to create transaction
        trans_start = datetime.now()
        
        try:
            # Items already extracted above (line 130)
            # ============================================================
            # BUILD TRANSACTION PAYLOAD BASED ON TYPE
            # ============================================================
            transaction_payload = None
            
            if jenis_transaksi == "penjualan":
                # Build ItemPenjualan array
                items_penjualan = []
                for item in items:
                    item_proto = transaction_service_pb2.ItemPenjualan(
                        name=item.get("nama_produk", ""),
                        quantity=int(item.get("jumlah", 0)),
                        unit=item.get("satuan", "pcs"),
                        unit_price=int(item.get("harga_satuan", 0)),
                        subtotal=int(item.get("subtotal", 0))
                    )
                    items_penjualan.append(item_proto)

                # ============================================
                # DISCOUNT & PPN CALCULATION (V005)
                # ============================================
                subtotal_before_discount = int(total_nominal)
                discount_type = transaction_entities.get("discount_type")
                discount_value = float(transaction_entities.get("discount_value", 0) or 0)
                include_vat = bool(transaction_entities.get("include_vat", False))

                # Calculate discount amount
                discount_amount = 0
                if discount_type == "percentage" and discount_value > 0:
                    discount_amount = int(subtotal_before_discount * discount_value / 100)
                elif discount_type == "nominal" and discount_value > 0:
                    discount_amount = int(discount_value)

                subtotal_after_discount = subtotal_before_discount - discount_amount

                # Calculate VAT (PPN 11%)
                vat_amount = 0
                if include_vat:
                    vat_amount = int(subtotal_after_discount * 0.11)

                grand_total = subtotal_after_discount + vat_amount

                logger.info(f"[{trace_id}] 💰 Pricing: subtotal={subtotal_before_discount}, discount={discount_amount} ({discount_type}), VAT={vat_amount}, grand={grand_total}")

                # Build TransaksiPenjualan
                transaction_payload = transaction_service_pb2.TransaksiPenjualan(
                    customer_name=transaction_entities.get("nama_pihak", ""),
                    items=items_penjualan,
                    subtotal=subtotal_before_discount,
                    discount=discount_amount,
                    tax=vat_amount,
                    total_nominal=grand_total,
                    payment_method=metode,
                    payment_status=status_bayar,
                    amount_paid=int(transaction_entities.get("nominal_dibayar", grand_total)),
                    amount_due=int(transaction_entities.get("sisa_piutang_hutang", 0)),
                    notes=transaction_entities.get("keterangan", request.message)
                )

                # Store calculated values for receipt generation
                transaction_entities['subtotal_before_discount'] = subtotal_before_discount
                transaction_entities['discount_amount'] = discount_amount
                transaction_entities['subtotal_after_discount'] = subtotal_after_discount
                transaction_entities['vat_amount'] = vat_amount
                transaction_entities['grand_total'] = grand_total

                logger.info(f"[{trace_id}] 📦 Built penjualan payload with {len(items_penjualan)} items")
            
            elif jenis_transaksi == "pembelian":
                # Build ItemPembelian array
                items_pembelian = []
                for item in items:
                    item_proto = transaction_service_pb2.ItemPembelian(
                        name=item.get("nama_produk", ""),
                        quantity=int(item.get("jumlah", 0)),
                        unit=item.get("satuan", "pcs"),
                        unit_price=int(item.get("harga_satuan", 0)),
                        subtotal=int(item.get("subtotal", 0)),
                        # HPP & Margin fields (V006)
                        hpp_per_unit=float(item.get("hpp_per_unit") or 0),
                        harga_jual=float(item.get("harga_jual") or 0),
                        margin=float(item.get("margin") or 0),
                        margin_percent=float(item.get("margin_percent") or 0)
                    )
                    items_pembelian.append(item_proto)

                # ============================================
                # DISCOUNT & PPN CALCULATION (V005)
                # ============================================
                subtotal_before_discount = int(total_nominal)
                discount_type = transaction_entities.get("discount_type")
                discount_value = float(transaction_entities.get("discount_value", 0) or 0)
                include_vat = bool(transaction_entities.get("include_vat", False))

                # Calculate discount amount
                discount_amount = 0
                if discount_type == "percentage" and discount_value > 0:
                    discount_amount = int(subtotal_before_discount * discount_value / 100)
                elif discount_type == "nominal" and discount_value > 0:
                    discount_amount = int(discount_value)

                subtotal_after_discount = subtotal_before_discount - discount_amount

                # Calculate VAT (PPN 11%)
                vat_amount = 0
                if include_vat:
                    vat_amount = int(subtotal_after_discount * 0.11)

                grand_total = subtotal_after_discount + vat_amount

                logger.info(f"[{trace_id}] 💰 Pricing: subtotal={subtotal_before_discount}, discount={discount_amount} ({discount_type}), VAT={vat_amount}, grand={grand_total}")

                # Build TransaksiPembelian
                transaction_payload = transaction_service_pb2.TransaksiPembelian(
                    vendor_name=transaction_entities.get("nama_pihak", "supplier"),
                    items=items_pembelian,
                    subtotal=subtotal_before_discount,
                    discount=discount_amount,
                    tax=vat_amount,
                    total_nominal=grand_total,
                    payment_method=metode,
                    payment_status=status_bayar,
                    amount_paid=int(transaction_entities.get("nominal_dibayar", grand_total)),
                    amount_due=int(transaction_entities.get("sisa_piutang_hutang", 0)),
                    notes=transaction_entities.get("keterangan", request.message)
                )

                # Store calculated values for receipt generation
                transaction_entities['subtotal_before_discount'] = subtotal_before_discount
                transaction_entities['discount_amount'] = discount_amount
                transaction_entities['subtotal_after_discount'] = subtotal_after_discount
                transaction_entities['vat_amount'] = vat_amount
                transaction_entities['grand_total'] = grand_total

                logger.info(f"[{trace_id}] 📦 Built pembelian payload with {len(items_pembelian)} items")
            
            elif is_modal or is_prive:
                # Modal/Prive menggunakan TransaksiBeban dengan kategori khusus
                kategori_khusus = "modal" if is_modal else "prive"
                transaction_payload = transaction_service_pb2.TransaksiBeban(
                    kategori=kategori_khusus,
                    deskripsi=transaction_entities.get("keterangan", request.message),
                    nominal=int(total_nominal),
                    payment_method=metode,
                    recipient=transaction_entities.get("nama_pihak", "owner"),
                    notes=request.message
                )
                logger.info(f"[{trace_id}] 📦 Built {kategori_khusus} as TransaksiBeban payload")

            elif jenis_transaksi == "beban":
                # Build TransaksiBeban (no items)
                kategori = transaction_entities.get("kategori_beban", "operasional")
                deskripsi = transaction_entities.get("keterangan", request.message)
                
                # For beban_gaji, store detail_karyawan and periode_gaji in notes with structured format
                notes = request.message
                if kategori == "beban_gaji":
                    detail_karyawan = transaction_entities.get("detail_karyawan", "")
                    periode_gaji = transaction_entities.get("periode_gaji", "")
                    employee_salaries = transaction_entities.get("employee_salaries", {})
                    
                    # Build structured notes for parsing in response
                    notes_parts = []
                    if detail_karyawan:
                        notes_parts.append(f"detail_karyawan:{detail_karyawan}")
                    if periode_gaji:
                        notes_parts.append(f"periode_gaji:{periode_gaji}")
                    if employee_salaries:
                        # Store as JSON string for easy parsing
                        import json as json_lib
                        notes_parts.append(f"employee_salaries:{json_lib.dumps(employee_salaries)}")
                    
                    if notes_parts:
                        notes = f"{request.message}|{'|'.join(notes_parts)}"
                
                transaction_payload = transaction_service_pb2.TransaksiBeban(
                    kategori=kategori,
                    deskripsi=deskripsi,
                    nominal=int(total_nominal),
                    payment_method=metode,
                    recipient=transaction_entities.get("nama_pihak", ""),
                    notes=notes
                )
                logger.info(f"[{trace_id}] 📦 Built beban payload")
            
            elif jenis_transaksi == "retur_penjualan":
                # Build ItemPenjualan array for return
                items_retur = []
                for item in items:
                    item_proto = transaction_service_pb2.ItemPenjualan(
                        name=item.get("nama_produk", ""),
                        quantity=int(item.get("jumlah", 0)),
                        unit=item.get("satuan", "pcs"),
                        unit_price=int(item.get("harga_satuan", 0)),
                        subtotal=int(item.get("subtotal", 0))
                    )
                    items_retur.append(item_proto)
                
                # Extract metadata
                metadata_dict = transaction_entities.get("metadata", {})
                keterangan_retur = transaction_entities.get("keterangan", "Retur penjualan")
                
                # Build TransaksiPenjualan with negative total
                transaction_payload = transaction_service_pb2.TransaksiPenjualan(
                    customer_name=transaction_entities.get("nama_pihak", "customer"),
                    items=items_retur,
                    subtotal=int(total_nominal),  # Already negative from parser
                    discount=0,
                    tax=0,
                    total_nominal=int(total_nominal),  # Negative value
                    payment_method=metode,
                    payment_status="lunas",
                    amount_paid=int(total_nominal),  # Negative = refund
                    amount_due=0,
                    notes=keterangan_retur
                )
                logger.info(f"[{trace_id}] 🔄 Built retur_penjualan payload with {len(items_retur)} items (refund: {format_rupiah(abs(int(total_nominal)))})")
            
            elif jenis_transaksi == "retur_pembelian":
                # Build ItemPembelian array for return to supplier
                items_retur = []
                for item in items:
                    item_proto = transaction_service_pb2.ItemPembelian(
                        name=item.get("nama_produk", ""),
                        quantity=int(item.get("jumlah", 0)),
                        unit=item.get("satuan", "pcs"),
                        unit_price=int(item.get("harga_satuan", 0)),
                        subtotal=int(item.get("subtotal", 0))
                    )
                    items_retur.append(item_proto)
                
                keterangan_retur = transaction_entities.get("keterangan", "Retur pembelian")
                
                # Build TransaksiPembelian with negative total
                transaction_payload = transaction_service_pb2.TransaksiPembelian(
                    vendor_name=transaction_entities.get("nama_pihak", "supplier"),
                    items=items_retur,
                    subtotal=int(total_nominal),  # Already negative
                    discount=0,
                    tax=0,
                    total_nominal=int(total_nominal),  # Negative value
                    payment_method=metode,
                    payment_status="lunas",
                    amount_paid=int(total_nominal),  # Negative = money back
                    amount_due=0,
                    notes=keterangan_retur
                )
                logger.info(f"[{trace_id}] 🔄 Built retur_pembelian payload with {len(items_retur)} items (refund: {format_rupiah(abs(int(total_nominal)))})")
            
            elif jenis_transaksi == "pembayaran_hutang":
                # Payment of debt/liability
                nama_supplier = transaction_entities.get("nama_pihak", "supplier")
                total_hutang_awal = transaction_entities.get("total_hutang_awal", 0)
                sisa_hutang = transaction_entities.get("sisa_hutang", 0)
                
                # Store metadata about original debt
                metadata_hutang = {
                    "total_hutang_awal": total_hutang_awal,
                    "sisa_hutang": sisa_hutang,
                    "tipe_pembayaran": "cicilan" if sisa_hutang > 0 else "pelunasan"
                }
                
                # Build TransaksiBeban for payment
                transaction_payload = transaction_service_pb2.TransaksiBeban(
                    kategori="pembayaran_hutang",
                    deskripsi=f"Pembayaran hutang ke {nama_supplier}",
                    nominal=int(total_nominal),
                    payment_method=metode,
                    recipient=nama_supplier,
                    notes=f"Bayar {format_rupiah(int(total_nominal))} dari total hutang {format_rupiah(int(total_hutang_awal))}. Sisa: {format_rupiah(int(sisa_hutang))}"
                )
                logger.info(f"[{trace_id}] 💳 Built pembayaran_hutang payload: {format_rupiah(int(total_nominal))} to {nama_supplier}")

            # ============================================================
            # BUILD INVENTORY IMPACT PROTO
            # ============================================================
            inventory_impact_proto = None
            inventory_impact_data = transaction_entities.get("inventory_impact")
            logger.info(f"[{trace_id}] DEBUG: inventory_impact_data = {inventory_impact_data}")
            
            # Force inventory tracking for pembelian/penjualan
            if inventory_impact_data and isinstance(inventory_impact_data, dict) and inventory_impact_data.get("is_tracked") and jenis_transaksi in ["pembelian", "penjualan"]:
                # Build items_inventory list
                items_inventory_proto = []
                
                for idx, item_inv in enumerate(inventory_impact_data.get("items_inventory", [])):
                    # Handle 'unknown' stok_setelah for penjualan
                    stok_setelah_value = item_inv.get("stok_setelah", 0)
                    
                    # Get produk_id from parser (usually empty)
                    produk_id = item_inv.get("produk_id", "")
                    
                    # FIX: If produk_id empty, lookup/create from nama_produk
                    if not produk_id and idx < len(items):
                        nama_produk = items[idx].get("nama_produk", "")
                        if nama_produk:
                            try:
                                # Try to find or create product
                                logger.info(f"[{trace_id}] 🔍 Looking up product: {nama_produk}")
                                
                                # For now, create UUID from name hash (deterministic)
                                import hashlib
                                import uuid
                                name_hash = hashlib.md5(f"{request.tenant_id}:{nama_produk}".encode()).hexdigest()
                                produk_id = str(uuid.UUID(name_hash))
                                
                                # Update item_inv with resolved produk_id
                                item_inv["produk_id"] = produk_id
                                logger.info(f"[{trace_id}] ✅ Product resolved: {nama_produk} → {produk_id}")
                                
                            except Exception as e:
                                logger.error(f"[{trace_id}] ❌ Failed to resolve product {nama_produk}: {e}")
                                produk_id = ""
                    
                    # If stok_setelah is 'unknown' or 0, try cache first then query
                    # FORM MODE: Skip stock check for faster response
                    if produk_id:
                        if is_form_mode:
                            # FORM MODE: Skip stock validation for faster response
                            # Set placeholder - will be calculated by outbox_worker
                            jumlah_movement = float(item_inv.get("jumlah_movement", 0))
                            stok_setelah_value = jumlah_movement  # Just use movement as placeholder
                            logger.info(f"[{trace_id}] 📝 FORM MODE: Skipping stock check, using placeholder stok_setelah={stok_setelah_value}")
                        else:
                            try:
                                # Normalize lokasi_gudang (underscore to dash)
                                lokasi = (inventory_impact_data.get("lokasi_gudang") or "gudang-utama").replace("_", "-")

                                # Always query inventory (no cache for accuracy)
                                stock_req = inventory_service_pb2.GetStockLevelRequest(
                                    tenant_id=request.tenant_id,
                                    produk_id=produk_id,
                                    lokasi_gudang=lokasi
                                )
                                stock_resp = await client_manager.stubs['inventory'].GetStockLevel(stock_req)
                                current_stock = stock_resp.current_stock

                                # Calculate stok_setelah
                                jumlah_movement = float(item_inv.get("jumlah_movement", 0))
                                stok_setelah_value = current_stock + jumlah_movement  # movement is negative for keluar

                                logger.info(f"[{trace_id}] 📊 Calculated stok_setelah: {current_stock} + ({jumlah_movement}) = {stok_setelah_value}")

                            except Exception as e:
                                logger.error(f"[{trace_id}] Failed to query stock for {produk_id}: {e}")
                                stok_setelah_value = 0  # Fallback to 0
                    
                    # Only add to proto if produk_id is valid
                    if produk_id:
                        item_inv_proto = inventory_service_pb2.ItemInventory(
                            produk_id=produk_id,
                            jumlah_movement=float(item_inv.get("jumlah_movement", 0)),
                            stok_setelah=float(stok_setelah_value),
                            nilai_per_unit=float(item_inv.get("nilai_per_unit", 0))
                        )
                        items_inventory_proto.append(item_inv_proto)
                        logger.info(f"[{trace_id}] DEBUG: ItemInventory created - produk_id={produk_id}, jumlah_movement={item_inv.get('jumlah_movement')}")
                    else:
                        logger.warning(f"[{trace_id}] ⚠️ Skipping inventory item - no valid produk_id")
                
                # Build InventoryImpact proto only if we have valid items
                if items_inventory_proto:
                    # Normalize lokasi_gudang at source: underscore to dash
                    raw_lokasi = inventory_impact_data.get("lokasi_gudang") or "gudang-utama"
                    normalized_lokasi = raw_lokasi.replace('_', '-')
                    
                    inventory_impact_proto = inventory_service_pb2.InventoryImpact(
                        is_tracked=True,
                        jenis_movement=inventory_impact_data.get("jenis_movement", ""),
                        lokasi_gudang=normalized_lokasi,
                        items_inventory=items_inventory_proto
                    )
                    logger.info(f"[{trace_id}] ✅ InventoryImpact built with {len(items_inventory_proto)} items")
                else:
                    logger.warning(f"[{trace_id}] ⚠️ No valid inventory items, skipping InventoryImpact")
    
    

            # ============================================================
            # BUILD CREATE TRANSACTION REQUEST (CORRECT ONEOF)
            # CRITICAL: Use tenant_id for idempotency_key (not session_id)
            # ============================================================
            idempotency_key = f"tenant_{request.tenant_id}_{trace_id}"
            
            logger.info(f"[{trace_id}] DEBUG: request.user_id={request.user_id}, request.tenant_id={request.tenant_id}")
            
            if jenis_transaksi == "penjualan":
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    penjualan=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=idempotency_key,
                    inventory_impact=inventory_impact_proto,
                    is_modal=is_modal,
                    is_prive=is_prive,
                    # Discount & PPN Fields (V005)
                    discount_type=transaction_entities.get('discount_type') or '',
                    discount_value=float(transaction_entities.get('discount_value', 0) or 0),
                    discount_amount=int(transaction_entities.get('discount_amount', 0)),
                    subtotal_before_discount=int(transaction_entities.get('subtotal_before_discount', 0)),
                    subtotal_after_discount=int(transaction_entities.get('subtotal_after_discount', 0)),
                    include_vat=bool(transaction_entities.get('include_vat', False)),
                    vat_amount=int(transaction_entities.get('vat_amount', 0)),
                    grand_total=int(transaction_entities.get('grand_total', 0)),
                )
            elif jenis_transaksi == "pembelian":
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    pembelian=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=idempotency_key,
                    inventory_impact=inventory_impact_proto,
                    # Discount & PPN Fields (V005)
                    discount_type=transaction_entities.get('discount_type') or '',
                    discount_value=float(transaction_entities.get('discount_value', 0) or 0),
                    discount_amount=int(transaction_entities.get('discount_amount', 0)),
                    subtotal_before_discount=int(transaction_entities.get('subtotal_before_discount', 0)),
                    subtotal_after_discount=int(transaction_entities.get('subtotal_after_discount', 0)),
                    include_vat=bool(transaction_entities.get('include_vat', False)),
                    vat_amount=int(transaction_entities.get('vat_amount', 0)),
                    grand_total=int(transaction_entities.get('grand_total', 0)),
                )

            elif jenis_transaksi == "beban" or is_modal or is_prive:
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    beban=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=idempotency_key,
                    is_modal=is_modal,
                    is_prive=is_prive,
                )
            
            elif jenis_transaksi == "retur_penjualan":
                # Retur penjualan uses TransaksiPenjualan with negative total
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    penjualan=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=idempotency_key,
                    inventory_impact=inventory_impact_proto,
                    total_nominal=int(total_nominal),  # Negative value for return
                )
                logger.info(f"[{trace_id}] ✅ Built create_request for retur_penjualan")
            
            elif jenis_transaksi == "retur_pembelian":
                # Retur pembelian uses TransaksiPembelian with negative total
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    pembelian=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=idempotency_key,
                    inventory_impact=inventory_impact_proto,
                    total_nominal=int(total_nominal),  # Negative value for return
                )
                logger.info(f"[{trace_id}] ✅ Built create_request for retur_pembelian")
            
            elif jenis_transaksi == "pembayaran_hutang":
                # Pembayaran hutang uses TransaksiBeban
                create_request = transaction_service_pb2.CreateTransactionRequest(
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    beban=transaction_payload,
                    raw_text=request.message,
                    idempotency_key=idempotency_key,
                    total_nominal=int(total_nominal),
                    sisa_piutang_hutang=int(transaction_entities.get("sisa_hutang", 0)),
                )
                logger.info(f"[{trace_id}] ✅ Built create_request for pembayaran_hutang")
            
            else:
                logger.error(f"[{trace_id}] ❌ Unknown jenis_transaksi: {jenis_transaksi}")
                return f"Maaf, jenis transaksi '{jenis_transaksi}' belum didukung."
            
            logger.info(f"[{trace_id}] DEBUG: CreateTransactionRequest built - inventory_impact={'SET' if inventory_impact_proto else 'NULL'}")

            # ============================================================
            # PHASE 2: CHECK FEATURE FLAG FOR ATOMIC FUNCTION
            # If enabled, bypass gRPC and call DB directly (~10ms vs ~3000ms)
            # ============================================================
            t_create_tx = time.perf_counter()

            tenant_config = await get_tenant_config(request.tenant_id)
            use_atomic = tenant_config.get("use_atomic_function", False)

            logger.info(f"[{trace_id}] 🔧 Tenant config: use_atomic_function={use_atomic}")

            if use_atomic and is_form_mode and jenis_transaksi in ["pembelian", "penjualan"]:
                # ============================================================
                # ATOMIC PATH: Direct DB call (~10ms)
                # ============================================================
                logger.info(f"[{trace_id}] ⚡ ATOMIC PATH: Using create_transaction_atomic()")

                import uuid
                tx_id = f"tx_{uuid.uuid4().hex[:5]}"

                # Build items array for atomic function
                items_for_atomic = []
                for item in items:
                    items_for_atomic.append({
                        "id": str(uuid.uuid4()),
                        "nama_produk": item.get("nama_produk", ""),
                        "jumlah": float(item.get("jumlah", 0)),
                        "satuan": item.get("satuan", "pcs"),
                        "harga_satuan": int(item.get("harga_satuan", 0)),
                        "subtotal": int(item.get("jumlah", 0) * item.get("harga_satuan", 0)),
                        "produk_id": item.get("produk_id") or transaction_entities.get("produk_id"),
                        "keterangan": item.get("keterangan"),
                        "hpp_per_unit": item.get("hpp_per_unit"),
                        "harga_jual": item.get("harga_jual"),
                        "margin": item.get("margin"),
                        "margin_percent": item.get("margin_percent")
                    })

                # Build outbox events
                outbox_events = [
                    {
                        "event_type": "inventory.update",
                        "payload": {
                            "transaksi_id": tx_id,
                            "tenant_id": request.tenant_id,
                            "jenis_movement": "masuk" if jenis_transaksi == "pembelian" else "keluar",
                            "items": items_for_atomic
                        }
                    },
                    {
                        "event_type": "accounting.create",
                        "payload": {
                            "transaksi_id": tx_id,
                            "tenant_id": request.tenant_id,
                            "jenis_transaksi": jenis_transaksi,
                            "total_nominal": int(total_nominal)
                        }
                    }
                ]

                # Build payload dict
                payload_dict = {
                    "items": items,
                    "metode_pembayaran": metode,
                    "nama_pihak": pihak,
                    "keterangan": transaction_entities.get("keterangan", "")
                }

                atomic_result = await create_transaction_atomic(
                    tx_id=tx_id,
                    tenant_id=request.tenant_id,
                    created_by=request.user_id,
                    actor_role="owner",
                    jenis_transaksi=jenis_transaksi,
                    payload=payload_dict,
                    total_nominal=int(total_nominal),
                    metode_pembayaran=metode or "tunai",
                    nama_pihak=pihak or "",
                    keterangan=transaction_entities.get("keterangan", ""),
                    idempotency_key=idempotency_key,
                    items=items_for_atomic,
                    outbox_events=outbox_events,
                    discount_type=transaction_entities.get('discount_type'),
                    discount_value=float(transaction_entities.get('discount_value', 0) or 0),
                    discount_amount=int(transaction_entities.get('discount_amount', 0)),
                    subtotal_before_discount=int(transaction_entities.get('subtotal_before_discount', 0)),
                    subtotal_after_discount=int(transaction_entities.get('subtotal_after_discount', 0)),
                    include_vat=bool(transaction_entities.get('include_vat', False)),
                    vat_amount=int(transaction_entities.get('vat_amount', 0)),
                    grand_total=int(transaction_entities.get('grand_total', 0) or total_nominal)
                )

                logger.info(f"[{trace_id}] [PERF] ATOMIC_CreateTransaction: {(time.perf_counter() - t_create_tx)*1000:.0f}ms (DB: {atomic_result.get('execution_time_ms', 0):.1f}ms)")

                if atomic_result.get("success"):
                    # Build fake trans_response for compatibility with existing code
                    class FakeTransaction:
                        def __init__(self):
                            self.id = atomic_result["transaction_id"]
                            self.created_at = atomic_result["created_at"]

                    class FakeResponse:
                        def __init__(self):
                            self.success = True
                            self.transaction = FakeTransaction()

                    trans_response = FakeResponse()
                else:
                    logger.error(f"[{trace_id}] ❌ Atomic function failed: {atomic_result.get('error')}")
                    # Fallback to gRPC
                    trans_response = await client_manager.stubs['transaction'].CreateTransaction(create_request)
            else:
                # ============================================================
                # LEGACY PATH: gRPC call to transaction_service (~3000ms)
                # ============================================================
                logger.info(f"[{trace_id}] 📡 LEGACY PATH: Using gRPC CreateTransaction")
                trans_response = await client_manager.stubs['transaction'].CreateTransaction(
                    create_request
                )

            logger.info(f"[{trace_id}] [PERF] gRPC_CreateTransaction: {(time.perf_counter() - t_create_tx)*1000:.0f}ms")

            trans_duration = (datetime.now() - trans_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "transaction",
                "method": "CreateTransaction",
                "duration_ms": int(trans_duration),
                "status": "success"
            })

            # ============================================================
            # BUILD SUCCESS RESPONSE
            # ============================================================
            if trans_response.success:
                transaction_id = trans_response.transaction.id
                
                # Special handling for beban_gaji with employee breakdown
                if jenis_transaksi == "beban" and transaction_entities.get("kategori_beban") == "beban_gaji":
                    detail_karyawan = transaction_entities.get("detail_karyawan", "")
                    employee_salaries = transaction_entities.get("employee_salaries", {})
                    
                    if detail_karyawan and employee_salaries:
                        # Already formatted with markdown in milky_response (starts with "dengan rekapan...")
                        # Prepend "Ok bayar secara tunai" to complete the sentence
                        milky_response = f"✅ Transaksi dicatat! Ok bayar secara tunai {milky_response}"
                        milky_response += f"\n\nBilang ya kak kalau ada koreksi 😊"
                    else:
                        # Fallback to simple format
                        if not locals().get('skip_single_item_logic', False):
                            milky_response = f"✅ Transaksi dicatat! {milky_response}"
                else:
                    # Only prepend if NOT multi-item (multi-item already has "✅ Transaksi Dicatat!")
                    if not locals().get('skip_single_item_logic', False):
                        milky_response = f"✅ Transaksi dicatat! {milky_response}"
                
                milky_response += f"\n\nID: {transaction_id[:8]}..."
                
                # Store transaction_id for context (will be saved in grpc_server.py)
                # Add to service_calls for metadata extraction
                service_calls[-1]["transaction_id"] = transaction_id
                # ⚡ OPTIMIZED: Show calculated stock (no blocking query)
                # Use stok_setelah from inventory_impact that was already calculated
                if inventory_impact_proto and inventory_impact_proto.items_inventory:



                    for item in inventory_impact_proto.items_inventory:
                        # Get product name from original items list
                        nama_produk = "produk"
                        for orig_item in items:
                            if items.index(orig_item) < len(inventory_impact_proto.items_inventory):
                                nama_produk = orig_item.get("nama_produk", "produk")
                                break
                        
                        # Display calculated stock (instant, no query needed)
                        milky_response += f"\n📦 Stok {nama_produk} sekarang: {int(item.stok_setelah)} pcs"

                # ============================================
                # FORM MODE: Return HTML Receipt
                # ============================================
                if is_form_mode:
                    # Extract data for HTML receipt
                    first_item = items[0] if items else {}
                    product_name = first_item.get("nama_produk", "Produk")
                    # Get product_id from transaction_entities or inventory_impact
                    product_id = transaction_entities.get("produk_id", "")
                    if not product_id:
                        # Try to get from inventory_impact items
                        inventory_impact_data = transaction_entities.get("inventory_impact", {})
                        if inventory_impact_data and inventory_impact_data.get("items_inventory"):
                            product_id = inventory_impact_data["items_inventory"][0].get("produk_id", "")
                    quantity = first_item.get("jumlah", 0)
                    unit = first_item.get("satuan", "pcs")
                    unit_price = first_item.get("harga_satuan", 0)
                    subtotal = quantity * unit_price
                    payment_method = metode.capitalize() if metode else "Tunai"
                    vendor_name = pihak if pihak else ""
                    notes = transaction_entities.get("keterangan", "")
                    transaction_type = jenis_transaksi.upper() if jenis_transaksi else "PEMBELIAN"

                    # Extract HPP & Margin fields (V006)
                    hpp_per_unit = first_item.get("hppPerUnit") or first_item.get("hpp_per_unit")
                    harga_jual = first_item.get("hargaJual") or first_item.get("harga_jual")
                    margin = first_item.get("margin")
                    margin_percent = first_item.get("marginPercent") or first_item.get("margin_percent")
                    retail_unit = first_item.get("retailUnit") or first_item.get("retail_unit") or unit

                    # Get discount/PPN values from calculated entities
                    subtotal_before_discount = transaction_entities.get('subtotal_before_discount', subtotal)
                    discount_amount = transaction_entities.get('discount_amount', 0)
                    discount_type = transaction_entities.get('discount_type')
                    discount_value = transaction_entities.get('discount_value', 0)
                    subtotal_after_discount = transaction_entities.get('subtotal_after_discount', subtotal_before_discount)
                    vat_amount = transaction_entities.get('vat_amount', 0)
                    grand_total = transaction_entities.get('grand_total', subtotal)
                    include_vat = transaction_entities.get('include_vat', False)

                    logger.info(f"[{trace_id}] 📝 HTML Receipt data: product={product_name}, vendor={vendor_name}, discount={discount_amount}, vat={vat_amount}")

                    # Random motivational closing
                    closing_message = random.choice(MOTIVATIONAL_CLOSINGS)

                    # Build HTML receipt with conditional rows
                    supplier_row = f'<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:8px 0;color:#666">Supplier</td><td style="padding:8px 0;text-align:right;font-weight:500">{vendor_name}</td></tr>' if vendor_name else ''
                    notes_row = f'<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:8px 0;color:#666">Catatan</td><td style="padding:8px 0;text-align:right;color:#888;font-style:italic;font-size:12px">{notes}</td></tr>' if notes else ''

                    # Build discount row if applicable
                    discount_row = ''
                    if discount_amount > 0:
                        if discount_type == 'percentage':
                            discount_label = f"Diskon ({int(discount_value)}%)"
                        else:
                            discount_label = "Diskon"
                        discount_row = f'<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:8px 0;color:#e11d48">{discount_label}</td><td style="padding:8px 0;text-align:right;font-weight:500;color:#e11d48">-{format_number(discount_amount)}</td></tr>'

                    # Build subtotal after discount row if there's a discount
                    after_discount_row = ''
                    if discount_amount > 0:
                        after_discount_row = f'<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:8px 0;color:#666">Setelah Diskon</td><td style="padding:8px 0;text-align:right;font-weight:500">{format_number(subtotal_after_discount)}</td></tr>'

                    # Build VAT row if applicable
                    vat_row = ''
                    if vat_amount > 0:
                        vat_row = f'<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:8px 0;color:#666">PPN 11%</td><td style="padding:8px 0;text-align:right;font-weight:500">{format_number(vat_amount)}</td></tr>'

                    # Build HPP & Margin rows (V006)
                    hpp_row = ''
                    if hpp_per_unit and hpp_per_unit > 0:
                        hpp_row = f'<tr style="border-bottom:1px solid #f0f0f0;background:#f8f9fa"><td style="padding:8px 0;color:#666">💰 HPP per {retail_unit}</td><td style="padding:8px 0;text-align:right;font-weight:500">{format_number(int(hpp_per_unit))}</td></tr>'

                    harga_jual_row = ''
                    if harga_jual and harga_jual > 0:
                        harga_jual_row = f'<tr style="border-bottom:1px solid #f0f0f0;background:#f8f9fa"><td style="padding:8px 0;color:#666">🏷️ Harga Jual</td><td style="padding:8px 0;text-align:right;font-weight:500">{format_number(int(harga_jual))}</td></tr>'

                    margin_row = ''
                    if margin and harga_jual and margin > 0:
                        margin_pct = margin_percent if margin_percent else 0
                        margin_row = f'<tr style="border-bottom:1px solid #f0f0f0;background:#e8f5e9"><td style="padding:8px 0;color:#2e7d32;font-weight:500">📊 Margin Profit</td><td style="padding:8px 0;text-align:right;font-weight:600;color:#2e7d32">{format_number(int(margin))} ({margin_pct:.1f}%)</td></tr>'

                    # Map transaction type to display label
                    type_display_map = {
                        "PEMBELIAN": "Kulakan",
                        "PENJUALAN": "Penjualan",
                        "BEBAN": "Beban",
                        "MODAL": "Modal"
                    }
                    type_display = type_display_map.get(transaction_type, transaction_type.capitalize())

                    # Use grand_total for final display
                    final_total = grand_total if (discount_amount > 0 or vat_amount > 0) else int(total_nominal)

                    # Format datetime with WIB timezone
                    wib_time = datetime.now(WIB)
                    date_str = wib_time.strftime('%d %b %Y • %H:%M') + ' WIB'

                    # Build barcode status element
                    # Query product's barcode from database
                    product_barcode = None
                    logger.info(f"[{trace_id}] 🔍 Barcode lookup: product_id={product_id}, product_name={product_name}")

                    # Tenant display name for receipt header
                    tenant_display_name = None

                    try:
                        # Use raw SQL via asyncpg (bypasses Prisma "client not generated" issue)
                        from app.database import fetch_product_barcode, update_product_harga_jual, fetch_tenant_display_name

                        # Get tenant_id from context (use request.tenant_id as fallback)
                        tenant_id_for_query = transaction_entities.get("tenant_id", request.tenant_id)

                        # Fetch tenant display_name for receipt header
                        tenant_display_name = await fetch_tenant_display_name(tenant_id_for_query)
                        if tenant_display_name:
                            logger.info(f"[{trace_id}] ✅ Found tenant display_name: {tenant_display_name}")

                        # Fetch barcode using raw SQL
                        product_barcode = await fetch_product_barcode(
                            product_id=product_id,
                            product_name=product_name,
                            tenant_id=tenant_id_for_query
                        )
                        if product_barcode:
                            logger.info(f"[{trace_id}] ✅ Found barcode: {product_barcode}")

                        # V007: Update product's harga_jual if provided from Kulakan form
                        if product_id and harga_jual and harga_jual > 0:
                            await update_product_harga_jual(product_id, float(harga_jual))
                            logger.info(f"[{trace_id}] ✅ Updated product harga_jual: {product_id} -> {harga_jual}")

                    except Exception as e:
                        logger.warning(f"[{trace_id}] Could not fetch product barcode: {e}")

                    # Show registered barcode or registration button
                    if product_barcode:
                        barcode_element = f'<div style="font-size:12px;color:#10b981;margin-top:4px">✅ Barcode: {product_barcode}</div>'
                    else:
                        barcode_element = f'<div data-product-id="{product_id}" data-product-name="{product_name}" style="font-size:12px;color:#f59e0b;cursor:pointer;margin-top:4px" class="barcode-register-btn">❎ Daftarkan Barcode</div>'

                    # Use tenant display_name as header, fallback to type_display
                    receipt_header = tenant_display_name if tenant_display_name else type_display

                    html_receipt = f"""
<div style="background:transparent;font-family:'Hiragino Kaku Gothic ProN',-apple-system,sans-serif;max-width:400px" class="milky-receipt">
  <!-- Store Header -->
  <div style="text-align:center;margin-bottom:16px;padding-bottom:16px;border-bottom:1px solid #E5E5E5">
    <div style="font-weight:700;font-size:18px;color:#262626">{receipt_header}</div>
    <div style="font-size:13px;color:#737373;margin-top:4px">{type_display}</div>
  </div>

  <!-- Item Details -->
  <table style="width:100%;border-collapse:collapse;font-size:14px">
    <thead>
      <tr style="border-bottom:1px solid #E5E5E5">
        <th style="text-align:left;padding:8px 0;color:#737373;font-weight:500;width:30px">No.</th>
        <th style="text-align:left;padding:8px 0;color:#737373;font-weight:500">Item</th>
        <th style="text-align:right;padding:8px 0;color:#737373;font-weight:500">Harga (Rp)</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="vertical-align:top;padding:8px 0;color:#525252">01.</td>
        <td style="padding:8px 0">
          <div style="font-weight:500;color:#262626">{wrap_product_name(product_name)}</div>
          {barcode_element}
          <div style="font-size:12px;color:#737373">{format_number(unit_price)} x {quantity} {unit}</div>
        </td>
        <td style="vertical-align:top;padding:8px 0;text-align:right;color:#262626">{format_number(subtotal_before_discount)}</td>
      </tr>
    </tbody>
  </table>

  <!-- Totals -->
  <div style="border-top:1px solid #E5E5E5;margin-top:12px;padding-top:12px">
    <table style="width:100%;font-size:14px">
      {supplier_row}
      {discount_row}
      {after_discount_row}
      {vat_row}
      {hpp_row}
      {harga_jual_row}
      {margin_row}
      <tr>
        <td style="padding:4px 0;color:#737373">Total</td>
        <td style="padding:4px 0;text-align:right;font-weight:600;color:#262626">{format_number(final_total)}</td>
      </tr>
      <tr>
        <td style="padding:4px 0;color:#737373">{payment_method}</td>
        <td style="padding:4px 0;text-align:right;color:#262626">{format_number(final_total)}</td>
      </tr>
      {notes_row}
    </table>
  </div>

  <!-- Transaction Info -->
  <div style="border-top:1px solid #E5E5E5;margin-top:12px;padding-top:12px;text-align:center;font-size:13px;color:#737373">
    <div>ID Transaksi {transaction_id[:8]}</div>
    <div style="margin-top:4px">{date_str}</div>
    <div style="margin-top:8px;font-style:italic;color:#666">{closing_message}</div>
  </div>
</div>
"""
                    logger.info(f"[{trace_id}] [PERF] HANDLER_TOTAL: {(time.perf_counter() - t_handler_start)*1000:.0f}ms")
                    return html_receipt

                logger.info(f"[{trace_id}] [PERF] HANDLER_TOTAL: {(time.perf_counter() - t_handler_start)*1000:.0f}ms")
                return milky_response
            else:
                logger.info(f"[{trace_id}] [PERF] HANDLER_TOTAL: {(time.perf_counter() - t_handler_start)*1000:.0f}ms (error)")
                return f"⚠️ Gagal catat transaksi: {trans_response.message}"

        except Exception as e:
            logger.error(f"[{trace_id}] Transaction creation failed: {e}")
            logger.info(f"[{trace_id}] [PERF] HANDLER_TOTAL: {(time.perf_counter() - t_handler_start)*1000:.0f}ms (exception)")
            return f"Maaf, ada kendala catat transaksi. Error: {str(e)[:100]}"
    
    @staticmethod
    async def handle_query_transaksi(
        request,
        ctx_response,
        intent_response,
        trace_id: str,
        service_calls: list,
        progress: int,
        client_manager
    ) -> str:
        """
        Handle transaction filtering query
        Examples:
        - "tampilkan transaksi supplier Toko Kain Jaya bulan Oktober"
        - "transaksi pembelian bulan lalu"
        - "semua transaksi customer Bu Sari"
        """
        logger.info(f"[{trace_id}] Handling query_transaksi intent")
        
        # Parse entities
        try:
            query_entities = json.loads(intent_response.entities_json)
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to parse entities: {e}")
            query_entities = {}
        
        logger.info(f"[{trace_id}] Query entities: {query_entities}")
        
        # Extract filters
        supplier_name = query_entities.get("supplier_name") or query_entities.get("nama_supplier")
        customer_name = query_entities.get("customer_name") or query_entities.get("nama_customer")
        date_range = query_entities.get("date_range")
        jenis_transaksi = query_entities.get("jenis_transaksi")

        # Parse date range if provided
        start_timestamp = None
        end_timestamp = None

        # Detect "hari ini" (today) in the message
        message_lower = request.message.lower()
        if "hari ini" in message_lower or "today" in message_lower:
            # Set to start of today
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_timestamp = int(today.timestamp() * 1000)
            # Set to end of today (23:59:59.999)
            end_of_today = today.replace(hour=23, minute=59, second=59, microsecond=999999)
            end_timestamp = int(end_of_today.timestamp() * 1000)
            logger.info(f"[{trace_id}] Detected 'hari ini' - filtering from {start_timestamp} to {end_timestamp}")
        elif date_range:
            try:
                # datetime already imported at module level (line 14)
                year, month = date_range.split("-")
                start_timestamp = int(datetime(int(year), int(month), 1).timestamp() * 1000)
                if int(month) == 12:
                    end_timestamp = int(datetime(int(year) + 1, 1, 1).timestamp() * 1000) - 1
                else:
                    end_timestamp = int(datetime(int(year), int(month) + 1, 1).timestamp() * 1000) - 1
            except Exception as e:
                logger.warning(f"[{trace_id}] Failed to parse date_range '{date_range}': {e}")

        # Build ListTransactionsRequest
        # IMPORTANT: Only set timestamp filters if they exist (don't send 0 which means epoch time)
        if start_timestamp is not None and end_timestamp is not None:
            list_request = transaction_service_pb2.ListTransactionsRequest(
                tenant_id=request.tenant_id,
                jenis_transaksi=jenis_transaksi if jenis_transaksi else "",
                status="approved",
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                page=1,
                page_size=50
            )
        else:
            # No timestamp filter - get all transactions
            list_request = transaction_service_pb2.ListTransactionsRequest(
                tenant_id=request.tenant_id,
                jenis_transaksi=jenis_transaksi if jenis_transaksi else "",
                status="approved",
                page=1,
                page_size=50
            )
        
        try:
            query_start = datetime.now()
            
            list_response = await client_manager.stubs['transaction'].ListTransactions(
                list_request
            )
            
            query_duration = (datetime.now() - query_start).total_seconds() * 1000
            service_calls.append({
                "service_name": "transaction",
                "method": "ListTransactions",
                "duration_ms": int(query_duration),
                "status": "success"
            })
            
            # Filter by supplier/customer name if provided (client-side filter)
            filtered_transactions = []
            for tx in list_response.transactions:
                # Check supplier/customer from payload
                payload = {}
                if hasattr(tx, 'payload') and tx.payload:
                    try:
                        payload = json.loads(tx.payload) if isinstance(tx.payload, str) else tx.payload
                    except:
                        pass
                
                nama_pihak = payload.get('nama_pihak', '') or payload.get('namaPihak', '')
                
                # Apply filters
                if supplier_name and supplier_name.lower() not in nama_pihak.lower():
                    continue
                if customer_name and customer_name.lower() not in nama_pihak.lower():
                    continue
                
                filtered_transactions.append(tx)
            
            # Format response
            if not filtered_transactions:
                filter_text = []
                if supplier_name:
                    filter_text.append(f"supplier {supplier_name}")
                if customer_name:
                    filter_text.append(f"customer {customer_name}")
                if date_range:
                    filter_text.append(f"periode {date_range}")
                if jenis_transaksi:
                    filter_text.append(f"jenis {jenis_transaksi}")
                
                filter_str = " dengan filter " + ", ".join(filter_text) if filter_text else ""
                return f"📋 Tidak ada transaksi yang ditemukan{filter_str}.\n\n💡 Pastikan filter yang digunakan sudah benar."
            
            # Build response
            milky_response = f"📋 Daftar Transaksi"
            if supplier_name:
                milky_response += f" Supplier: {supplier_name}"
            if customer_name:
                milky_response += f" Customer: {customer_name}"
            if date_range:
                milky_response += f" Periode: {date_range}"
            if jenis_transaksi:
                milky_response += f" ({jenis_transaksi})"
            milky_response += f":\n\n"
            
            total_amount = 0
            for tx in filtered_transactions[:20]:  # Limit to 20
                tx_date = datetime.fromtimestamp(tx.timestamp / 1000).strftime("%d %b %Y")
                tx_type = tx.jenisTransaksi if hasattr(tx, 'jenisTransaksi') else "transaksi"
                tx_amount = tx.totalNominal if hasattr(tx, 'totalNominal') else 0
                total_amount += tx_amount
                
                # Extract nama_pihak from payload
                payload = {}
                if hasattr(tx, 'payload') and tx.payload:
                    try:
                        payload = json.loads(tx.payload) if isinstance(tx.payload, str) else tx.payload
                    except:
                        pass
                nama_pihak = payload.get('nama_pihak', '') or payload.get('namaPihak', 'Tidak disebutkan')
                
                milky_response += f"• {tx_date}: {tx_type.title()}\n"
                milky_response += f"  {format_rupiah(tx_amount)}"
                if nama_pihak:
                    milky_response += f" - {nama_pihak}"
                milky_response += "\n\n"
            
            if len(filtered_transactions) > 20:
                milky_response += f"... dan {len(filtered_transactions) - 20} transaksi lainnya\n\n"
            
            milky_response += f"💰 Total: {format_rupiah(total_amount)}\n"
            milky_response += f"📊 Jumlah transaksi: {len(filtered_transactions)}"
            
            return milky_response
            
        except Exception as e:
            logger.error(f"[{trace_id}] Query transactions failed: {e}")
            return f"Maaf, ada kendala ambil data transaksi. Error: {str(e)[:100]}"