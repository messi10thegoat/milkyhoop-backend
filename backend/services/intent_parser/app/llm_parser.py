import os
import json
import re
from typing import Optional, Dict, Any
from openai import OpenAI

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Path to intent schema JSON
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "../intent_schema.json")

def load_intent_schema():
    """Load intent schema if exists, otherwise return default dual-domain schema"""
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Default dual-domain schema
        return {
            "domains": {
                "chatbot_setup": [
                    "business_setup",
                    "confirm_setup",
                    "faq_create",
                    "faq_update",
                    "faq_delete",
                    "faq_query"
                ],
                "financial_management": [
                    "transaction_record",
                    "financial_report",
                    "inventory_query",
                    "inventory_update",
                    "accounting_query"
                ],
                "general": [
                    "general_chat",
                    "others"
                ]
            }
        }

# Enhanced prompt template for dual-domain classification
PROMPT_TEMPLATE = """
PERAN: Kamu adalah asisten NLP untuk klasifikasi intent dual-domain di platform MilkyHoop.

DOMAIN YANG DIDUKUNG:
1. Chatbot Setup (FAQ management, business config)
2. Financial Management (transactions, reports, inventory)

TUJUAN: Klasifikasi intent pengguna dengan AKURAT dan ekstrak entitas relevan sesuai proto schema.

OUTPUT FORMAT: JSON dengan struktur:
{{
  "intent": "nama_intent",
  "entities": {{
    // Entity structure sesuai domain (lihat contoh per intent)
  }}
}}

═══════════════════════════════════════════
🎯 DOMAIN 1: CHATBOT SETUP INTENTS
═══════════════════════════════════════════

1️⃣ business_setup
   KAPAN: User mendeskripsikan informasi BISNIS BARU
   TRIGGER: Kata kerja inisiatif + deskripsi bisnis
   CONTOH POSITIF:
   - "Gue mau buka cafe nih, namanya Kopi Santai"
   - "Bisnis saya toko online fashion"
   - "Mau bikin resto Jepang namanya Sakura"
   - "Usaha laundry kiloan di Jakarta"
   ENTITIES STRUCTURE:
   {{
     "entities": {{
       "Business": {{
         "business_name": "Kopi Santai",
         "business_type": "cafe",
         "location": "Jakarta",
         "products_services": ["kopi", "snack"],
         "target_customers": "pekerja kantoran"
       }}
     }}
   }}

2️⃣ confirm_setup
   KAPAN: User HANYA konfirmasi/setuju (tanpa info baru)
   TRIGGER: Kata konfirmasi singkat/tegas
   CONTOH: "Oke", "Ya", "Lanjut", "Siap", "Setuju"
   ENTITIES: {{"entities": {{"confirmation_type": "positive"}}}}

3️⃣ faq_create
   KAPAN: User membuat FAQ entry baru
   TRIGGER: "buat", "tambah", "bikin" + FAQ content
   ENTITIES STRUCTURE:
   {{
     "entities": {{
       "FAQ": {{
         "faq_question": "Jam operasional?",
         "faq_answer": "Buka 24 jam",
         "faq_category": "operasional"
       }}
     }}
   }}

4️⃣ faq_update
   KAPAN: User mengubah FAQ yang sudah ada
   TRIGGER: "ganti", "ubah", "update" + referensi FAQ lama
   ENTITIES STRUCTURE:
   {{
     "entities": {{
       "FAQ": {{
         "old_item": "delivery",  // CORE KEYWORDS only
         "new_item": "bisa COD",
         "faq_category": "delivery"
       }}
     }}
   }}

5️⃣ faq_delete
   KAPAN: User menghapus FAQ
   ENTITIES: {{"entities": {{"FAQ": {{"item": "promo"}}}}}}

6️⃣ faq_query
   KAPAN: User mencari/membaca FAQ
   ENTITIES: {{"entities": {{"FAQ": {{"query": "jam buka"}}}}}}

═══════════════════════════════════════════
💰 DOMAIN 2: FINANCIAL MANAGEMENT INTENTS
═══════════════════════════════════════════

7️⃣ transaction_record
   KAPAN: User mencatat transaksi keuangan (penjualan/pembelian/beban)
   TRIGGER: "jual", "beli", "bayar", "terima", "keluar", "masuk" + nominal
   
   CONTOH:
   - "jual 100 kaos @45rb ke Bu Sari DP 60%"
   - "bayar listrik 500rb cash"
   - "terima modal 10 juta dari investor"
   - "beli kain 2 juta dari supplier tempo 30 hari"
   
   ENTITIES STRUCTURE (ALIGNED dengan transaction_service.proto):
   {{
     "entities": {{
       "jenis_transaksi": "penjualan",  // penjualan|pembelian|beban|modal|prive
       "total_nominal": 450000000,  // IN CENTS (4.5 juta = 450000000)
       "metode_pembayaran": "transfer",  // cash|transfer|tempo|giro|cicilan
       "status_pembayaran": "dp",  // lunas|dp|tempo|cicilan|dibayar_sebagian
       "nominal_dibayar": 270000000,  // Amount paid (cents)
       "sisa_piutang_hutang": 180000000,  // Remaining (cents)
       "nama_pihak": "Bu Sari",
       "kontak_pihak": "081234567890",  // Optional
       "pihak_type": "customer",  // customer|supplier|karyawan|owner|bank
       "kategori_arus_kas": "operasi",  // operasi|investasi|pendanaan (REQUIRED)
       "items": [
         {{
           "nama_produk": "Kaos Polos Hitam",
           "jumlah": 100.0,
           "satuan": "pcs",  // pcs|kg|meter|jam|porsi|lusin|set
           "harga_satuan": 45000,  // Unit price (cents)
           "subtotal": 4500000
         }}
       ],
       "inventory_impact": {{
         "is_tracked": true,
         "jenis_movement": "keluar",  // masuk|keluar|none
         "lokasi_gudang": "gudang_bandung",
         "items_inventory": [
           {{
             "produk_id": "KAOS-001",
             "jumlah_movement": -100.0,  // Negative for keluar
             "stok_setelah": 150.0
           }}
         ]
       }},
       "periode_pelaporan": "2025-11",  // YYYY-MM format
       "keterangan": "Penjualan kaos ke Bu Sari, DP 60%"
     }}
   }}
   
   CRITICAL FIELD MAPPING:
   - total_nominal: ALWAYS in cents (multiply Rupiah by 100)
   - jenis_transaksi: MUST be one of [penjualan, pembelian, beban, modal, prive]
   - metode_pembayaran: MUST be lowercase
   - kategori_arus_kas: REQUIRED for cash flow reporting
   - items: Array of ItemTransaksi (nama_produk, jumlah, satuan, harga_satuan)

   ═══════════════════════════════════════════
   🔥 CRITICAL CLASSIFICATION RULES (UNIVERSAL)
   ═══════════════════════════════════════════
   
   Classification is based on TRANSACTION DIRECTION + VERB, NOT item type!
   
   1. PENJUALAN (Revenue - Money FROM customer TO business):
      Trigger verbs: "jual", "terima", "dapat", "dibayar"
      Pihak: customer, pembeli, client
      


      
      Examples (ANY business type):
      ✅ "jual 50 kaos @30rb" → penjualan (product)
      ✅ "jual konseling 2 sesi @150rb" → penjualan (service)
      ✅ "jual membership gym 3 bulan" → penjualan (subscription)
      ✅ "terima 5jt dari Bu Sari" → penjualan (payment received)
      ✅ "dapat uang dari customer 10jt" → penjualan
      ✅ "dibayar client 20jt untuk proyek" → penjualan
      ✅ "modal awal 50 juta" → modal (capital injection)
      ✅ "tambah modal 20 juta" → modal
      ✅ "setoran modal dari owner" → modal
      ✅ "prive ambil 5 juta" → prive (owner withdrawal)
      ✅ "ambil uang pribadi 3 juta" → prive





      
      KEY RULE: If user is SELLING (product/service/subscription) TO customer = penjualan
   
   2. PEMBELIAN (Purchase - Money FROM business TO supplier):
      Trigger verbs: "beli", "order", "pembelian"
      Pihak: supplier, vendor, distributor
      
      Examples:
      ✅ "beli kain 100 meter" → pembelian (raw material)
      ✅ "order bahan dari supplier" → pembelian
      ✅ "beli software license" → pembelian (if reselling)
      ✅ "pembelian 50 rol benang" → pembelian
      
      KEY RULE: Buying inventory/materials FOR resale = pembelian
   
   3. BEBAN (Expense - Operational costs):
      Trigger verbs: "bayar" (when NOT to supplier for inventory)
      Categories: utility, salary, rent, fees, operational costs
      
      Examples:
      ✅ "bayar listrik 500rb" → beban (utility)
      ✅ "gaji karyawan 5jt" → beban (payroll)
      ✅ "bayar sewa gedung 15jt" → beban (rent)
      ✅ "biaya konsultan eksternal" → beban (hiring external service)
      ✅ "bayar iklan Facebook" → beban (marketing expense)
      
      KEY RULE: Operational costs that don't generate inventory = beban
   
   ═══════════════════════════════════════════
   🎯 DECISION TREE (Apply in order)
   ═══════════════════════════════════════════
   
   1. Does user say "jual" or "terima" or mention "customer"?
      → YES: penjualan (regardless of product/service type)
      → NO: continue to step 2
   
   2. Does user say "beli" or "order" or mention "supplier"?
      → YES: pembelian (buying for inventory)
      → NO: continue to step 3
   
   3. Does user say "bayar" + utility/salary/rent/fees?
      → YES: beban (operational expense)
      → NO: continue to step 4
   
   4. Check if there's a price per unit (@) with quantity:
      - If mentioned with "jual" verb → penjualan
      - If mentioned with "beli" verb → pembelian
      - If mentioned with "bayar" verb + utility → beban
      - Otherwise → analyze context
   
   5. If still unclear, default to beban for safety
   
   ═══════════════════════════════════════════
   ⚡ VERB PRIORITY RULE
   ═══════════════════════════════════════════
   
   The VERB determines direction, NOT the item type!
   
   Examples across different business types:
   - Konveksi: "jual kaos" → penjualan (selling product)
   - Psikolog: "jual konseling" → penjualan (selling service)
   - Gym: "jual membership" → penjualan (selling subscription)
   - Konsultan: "terima fee konsultasi" → penjualan (selling service)
   - Cafe: "jual kopi" → penjualan (selling product)
   
   Counter-examples (same items, different direction):
   - "bayar konsultan" → beban (buying external service)
   - "bayar membership gym" → beban (buying for employee benefit)
   - "beli kopi beans" → pembelian (buying inventory to resell)

8️⃣ financial_report
   KAPAN: User meminta laporan keuangan SAK EMKM
   TRIGGER: "untung", "rugi", "laba", "neraca", "kas", "aset", "laporan"
   
   CONTOH:
   - "untung bulan ini berapa?"
   - "lihat neraca Oktober"
   - "kas masuk bulan lalu?"
   - "total aset apa?"
   
   ENTITIES STRUCTURE (ALIGNED dengan reporting_service.proto):
   {{
     "entities": {{
       "report_type": "laba_rugi",  // laba_rugi|neraca|arus_kas|perubahan_ekuitas
       "periode_pelaporan": "2025-11",  // YYYY-MM or YYYY-QN or YYYY
       "time_reference": "bulan_ini",  // bulan_ini|bulan_lalu|tahun_ini|custom
       "specific_metric": "laba_bersih"  // Optional: laba_bersih|total_aset|kas_akhir
     }}
   }}
   
   REPORT TYPE MAPPING:
   - "untung/rugi/laba" → laba_rugi
   - "neraca/aset/liabilitas" → neraca
   - "kas masuk/keluar/arus kas" → arus_kas
   - "modal/ekuitas/prive" → perubahan_ekuitas

9️⃣ inventory_query
   KAPAN: User mengecek stok barang
   TRIGGER: "stok", "stock", "cek stok", "berapa stok", "persediaan"
   
   CONTOH:
   - "cek stok kaos hitam"
   - "berapa stok di gudang Bandung?"
   - "produk apa yang stocknya hampir habis?"
   
   ENTITIES STRUCTURE (ALIGNED dengan inventory_service.proto):
   {{
     "entities": {{
       "produk_id": "KAOS-001",  // Optional if product_name provided
       "product_name": "kaos hitam",
       "lokasi_gudang": "gudang_bandung",  // Optional
       "query_type": "stock_level"  // stock_level|low_stock_alert|movement_history
     }}
   }}

🔟 inventory_update
   KAPAN: User update stok manual (stock opname)
   TRIGGER: "tambah stok", "kurang stok", "set stok", "update stok"
   
   CONTOH:
   - "tambah stok 50 kaos di gudang Bandung"
   - "kurangi stok 20 karena rusak"
   - "set stok kaos hitam jadi 100"
   
   ENTITIES STRUCTURE (ALIGNED dengan inventory_service.proto):
   {{
     "entities": {{
       "produk_id": "KAOS-001",
       "product_name": "kaos hitam",
       "lokasi_gudang": "gudang_bandung",
       "new_quantity": 100.0,  // Absolute value
       "jumlah_movement": 50.0,  // Relative change (optional)
       "jenis_movement": "masuk",  // masuk|keluar|adjustment
       "reason": "opname",  // opname|correction|damage|loss (REQUIRED)
       "keterangan": "Stock opname bulanan - ada yang rusak 5pcs"
     }}
   }}

1️⃣1️⃣ accounting_query
   KAPAN: User mengecek jurnal/bagan akun
   TRIGGER: "jurnal", "bagan akun", "debit", "kredit", "balance"
   
   CONTOH:
   - "lihat jurnal bulan ini"
   - "cek bagan akun"
   
   ENTITIES STRUCTURE:
   {{
     "entities": {{
       "query_type": "journal_entries",  // journal_entries|chart_of_accounts|balance_check
       "periode_pelaporan": "2025-11"
     }}
   }}

═══════════════════════════════════════════
🔥 CRITICAL RULES
═══════════════════════════════════════════

✅ DECISION TREE:
   1. Ada nominal + kata transaksi (jual/beli/bayar)? → transaction_record
   2. Ada kata laporan/untung/rugi/neraca/kas? → financial_report
   3. Ada kata stok/stock + cek/berapa? → inventory_query
   4. Ada kata stok + tambah/kurang/set? → inventory_update
   5. Ada kata jurnal/bagan akun? → accounting_query
   6. Ada deskripsi bisnis baru? → business_setup
   7. Hanya kata setuju tanpa info? → confirm_setup
   8. Operasi FAQ (CRUD)? → faq_* yang sesuai
   9. Sapaan/unclear? → general_chat

✅ FIELD CONVERSION RULES:
   - Nominal: ALWAYS convert to cents (Rp 45.000 → 4500000)
   - Dates: YYYY-MM format for periode_pelaporan
   - Lowercase: jenis_transaksi, metode_pembayaran, kategori_arus_kas
   - Signed values: inventory jumlah_movement (+ for masuk, - for keluar)

✅ OUTPUT REQUIREMENTS:
   - Valid JSON tanpa markdown formatting
   - Intent WAJIB dari list di atas
   - Entities sesuai proto field mapping (camelCase sensitive)
   - total_nominal in CENTS (multiply by 100)

═══════════════════════════════════════════

SCHEMA YANG TERSEDIA:
{schema}

USER MESSAGE:
{user_input}

CONTEXT (if any):
{context_info}

JAWABAN JSON (no markdown, direct JSON only):
"""

def parse_intent_entities(text: str, context: str = None) -> Dict[str, Any]:
    """
    Parse user message to extract intent and entities using GPT-4o
    Supports dual-domain: Chatbot Setup + Financial Management
    
    Args:
        text: User input message
        context: Optional conversation context (previous business/financial data)
        
    Returns:
        dict: Parsed intent and entities with proto-aligned field mapping
    """
    schema = load_intent_schema()
    
    # Build context info
    context_info = "None"
    if context:
        context_info = f"""Previous conversation data:
{context}

IMPORTANT: 
- If context shows business_type/name, classify follow-ups as 'business_setup'
- If context shows transaction history, classify financial queries accordingly
"""
    
    prompt = PROMPT_TEMPLATE.format(
        schema=json.dumps(schema, ensure_ascii=False, indent=2),
        user_input=text.strip(),
        context_info=context_info
    )

    # SURGICAL FIX: Replace hardcoded dates with current period
    from datetime import datetime
    current_period = datetime.now().strftime("%Y-%m")
    prompt = prompt.replace("2025-11", current_period)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini-2024-07-18",
            messages=[
                {
                    "role": "system", 
                    "content": f"""You are a precise NLP intent classifier for dual-domain business platform. Always output valid JSON without markdown.
                    CRITICAL DATE CONTEXT: Today is {datetime.now().strftime('%Y-%m-%d')}. For financial reports, use periode_pelaporan: "{current_period}" unless user specifies different period.

                    CRITICAL CONVERSION RULES (Step-by-step):
                    1. Parse Indonesian shorthand FIRST:
                      - "rb" or "ribu" = × 1.000
                      - "jt" or "juta" = × 1.000.000

                    2. Then convert to cents (× 100):
                      
                      Examples:
                      ✅ "150rb" → 150 × 1.000 = 150.000 rupiah → 150.000 × 100 = 15.000.000 cents
                      ✅ "30rb" → 30 × 1.000 = 30.000 rupiah → 30.000 × 100 = 3.000.000 cents
                      ✅ "2jt" → 2 × 1.000.000 = 2.000.000 rupiah → 2.000.000 × 100 = 200.000.000 cents
                      
                      For quantity × unit price:
                      ✅ "2 sesi @150rb"
                          Step 1: 150rb = 150 × 1.000 = 150.000 per unit
                          Step 2: 2 × 150.000 = 300.000 rupiah total
                          Step 3: 300.000 × 100 = 30.000.000 cents ← OUTPUT THIS
                          
                      ❌ WRONG: "150rb" = 150.000 (forgot × 100 for cents)
                      ❌ WRONG: "150rb" = 15.000 (forgot rb = × 1.000)
                    """
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=1500
        )
        content = response.choices[0].message.content.strip()

        print(f"Raw LLM output: {content[:300]}...")

        # Clean markdown code blocks if present
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()
        elif content.startswith("```"):
            content = content.replace("```", "").strip()

        # Parse JSON response
        parsed = json.loads(content)

        # Validate: intent must exist
        if not parsed.get("intent"):
            raise ValueError("Intent is missing or null")
        
        # Normalize intent name
        intent = parsed.get("intent", "").lower().strip()
        
        # Map common variations to standard names
        intent_mapping = {
            # Chatbot setup
            "confirmation": "confirm_setup",
            "setup": "business_setup",
            "business": "business_setup",
            "create_faq": "faq_create",
            "update_faq": "faq_update",
            "delete_faq": "faq_delete",
            "query_faq": "faq_query",
            "read_faq": "faq_query",
            
            # Financial
            "transaction": "transaction_record",
            "record_transaction": "transaction_record",
            "financial_transaction": "transaction_record",
            "report": "financial_report",
            "get_report": "financial_report",
            "inventory": "inventory_query",
            "check_stock": "inventory_query",
            "stock_query": "inventory_query",
            "update_stock": "inventory_update",
            "adjust_stock": "inventory_update",
            "journal": "accounting_query",
            "accounting": "accounting_query",
            
            # General
            "chat": "general_chat",
            "greeting": "general_chat"
        }
        
        # Apply mapping if exists
        normalized_intent = intent_mapping.get(intent, intent)
        parsed["intent"] = normalized_intent

        # Post-process financial entities (ensure cents conversion)
        if normalized_intent == "transaction_record":
            parsed = _post_process_transaction_entities(parsed, text)
        
        print(f"Classified intent: {normalized_intent}")
        
        return parsed

    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}")
        print(f"Raw content: {content[:500]}")
        
        # Fallback: Try rule-based extraction for financial transactions
        if any(kw in text.lower() for kw in ["jual", "beli", "bayar", "terima"]):
            return _fallback_transaction_extraction(text)
        
        return {
            "intent": "general_chat",
            "entities": {}
        }
        
    except Exception as e:
        print(f"OpenAI API error: {e}")
        import traceback
        traceback.print_exc()

        return {
            "intent": "general_chat",
            "entities": {}
        }


def _post_process_transaction_entities(parsed: Dict[str, Any], original_text: str) -> Dict[str, Any]:
    """
    Post-process transaction entities to ensure proto compliance
    
    CRITICAL FIX: Line 457 - Remove double nesting .get("entities", {}).get("entities", {})
    ADDED: Service classification override based on verbs (universal, not tenant-specific)
    
    Args:
        parsed: Parsed result from LLM
        original_text: Original user input
        
    Returns:
        Enhanced parsed result with validated fields
    """
    # ✅ FIX: Single .get() only - entities already at top level from GPT-4o
    entities = parsed.get("entities", {})
    
    print(f"DEBUG _post_process line 457 - entities extracted: {entities}")
    
    # ✅ NEW: Fix service classification based on verbs (universal logic)
    jenis = entities.get("jenis_transaksi")
    text_lower = original_text.lower()
    
    if jenis == "beban":
        # Check for revenue verbs (selling TO customer)
        revenue_verbs = ["jual", "terima pembayaran", "dapat uang", "dibayar customer", "terima fee"]
        if any(verb in text_lower for verb in revenue_verbs):
            print(f"DEBUG: User said revenue verb '{[v for v in revenue_verbs if v in text_lower]}' but classified as beban. Overriding to penjualan")
            entities["jenis_transaksi"] = "penjualan"
            
            # Fix keterangan if exists
            if "keterangan" in entities:
                entities["keterangan"] = entities["keterangan"].replace(
                    "Pembayaran", "Penjualan"
                ).replace(
                    "Biaya", "Penjualan"
                )
    
    elif jenis == "penjualan":
        # Check for expense keywords (buying FROM vendor)
        expense_keywords = ["bayar listrik", "bayar air", "bayar gaji", "bayar sewa", "biaya operasional"]
        if any(keyword in text_lower for keyword in expense_keywords):
            print(f"DEBUG: User said expense keyword but classified as penjualan. Overriding to beban")
            entities["jenis_transaksi"] = "beban"
    
    # Ensure total_nominal is in cents
    if "total_nominal" in entities:
        nominal = entities["total_nominal"]
        # If looks like Rupiah (< 1 million), multiply by 100
        if nominal < 1000000:
            entities["total_nominal"] = int(nominal * 100)
            print(f"Converted nominal: {nominal} → {entities['total_nominal']} (cents)")
    
    # Set default kategori_arus_kas if missing (REQUIRED field)
    if "kategori_arus_kas" not in entities:
        jenis = entities.get("jenis_transaksi", "")
        if jenis == "penjualan" or jenis == "pembelian" or jenis == "beban":
            entities["kategori_arus_kas"] = "operasi"
        elif "modal" in original_text.lower() or "pinjam" in original_text.lower():
            entities["kategori_arus_kas"] = "pendanaan"
        else:
            entities["kategori_arus_kas"] = "operasi"  # Default
        print(f"Set default kategori_arus_kas: {entities['kategori_arus_kas']}")
    
    # Ensure lowercase for enum fields
    for field in ["jenis_transaksi", "metode_pembayaran", "status_pembayaran", "pihak_type"]:
        if field in entities and entities[field]:
            entities[field] = entities[field].lower()
    
    # ✅ FIX: Preserve all fields, just update the processed ones
    parsed["entities"] = entities
    
    print(f"DEBUG _post_process line 490 - final parsed['entities']: {parsed.get('entities', {})}")
    
    return parsed


def _fallback_transaction_extraction(text: str) -> Dict[str, Any]:
    """
    Fallback rule-based extraction for financial transactions
    Enhanced with verb-based classification (universal logic)
    
    Args:
        text: User input message
        
    Returns:
        Basic transaction entity structure
    """
    text_lower = text.lower()
    
    # ✅ ENHANCED: Detect jenis_transaksi using verb-based logic (universal)
    jenis = None
    
    # Priority 1: PENJUALAN verbs (money IN from customer)
    penjualan_verbs = ["jual", "terima", "dapat", "dibayar"]
    if any(verb in text_lower for verb in penjualan_verbs):
        # Exception: "terima invoice" might be pembelian
        if not any(word in text_lower for word in ["invoice dari", "tagihan dari", "bill dari"]):
            jenis = "penjualan"
    
    # Priority 2: PEMBELIAN verbs (buying inventory)
    if jenis is None:
        pembelian_verbs = ["beli", "order", "pembelian"]
        if any(verb in text_lower for verb in pembelian_verbs):
            jenis = "pembelian"
    
    # Priority 3: BEBAN indicators (operational expenses)
    if jenis is None:
        beban_keywords = [
            "bayar listrik", "bayar air", "bayar internet",
            "bayar gaji", "gaji karyawan",
            "bayar sewa", "sewa gedung", "sewa kantor",
            "biaya", "pengeluaran", "ongkos"
        ]
        if any(keyword in text_lower for keyword in beban_keywords):
            jenis = "beban"
    
    # Priority 4: Check pihak mentions
    if jenis is None:
        if "customer" in text_lower or "pembeli" in text_lower or "client" in text_lower:
            jenis = "penjualan"
        elif "supplier" in text_lower or "vendor" in text_lower:
            jenis = "pembelian"
    
    # Priority 5: Context inference
    if jenis is None:
        # If has price (@) and no expense keywords → likely penjualan
        if "@" in text_lower and "bayar" not in text_lower:
            jenis = "penjualan"
        else:
            # Default: beban (safest for ambiguous operational costs)
            jenis = "beban"
    
    # Extract nominal (basic regex)
    nominal = 0
    nominal_match = re.search(r'(\d+)\s*(rb|ribu|k)', text_lower)
    if nominal_match:
        nominal = int(nominal_match.group(1)) * 1000 * 100  # Convert to cents
    else:
        nominal_match = re.search(r'(\d+)\s*(jt|juta|m)', text_lower)
        if nominal_match:
            nominal = int(nominal_match.group(1)) * 1000000 * 100  # Convert to cents
    
    # Detect metode_pembayaran
    metode = "cash"
    if "transfer" in text_lower:
        metode = "transfer"
    elif "tempo" in text_lower:
        metode = "tempo"
    
    return {
        "intent": "transaction_record",
        "entities": {
            "jenis_transaksi": jenis,
            "total_nominal": nominal,
            "metode_pembayaran": metode,
            "kategori_arus_kas": "operasi",
            "keterangan": text[:200]
        }
    }