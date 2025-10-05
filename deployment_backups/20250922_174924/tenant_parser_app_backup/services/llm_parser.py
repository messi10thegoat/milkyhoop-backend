import os
import json
from openai import OpenAI

# ✅ Inisialisasi client OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ✅ Path schema intent JSON
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "../intent_schema.json")

def load_intent_schema():
   with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
       return json.load(f)

# ✅ Prompt template untuk CUSTOMER SERVICE & TENANT INTERACTION
PROMPT_TEMPLATE = """
PERAN: Kamu adalah asisten NLP untuk memahami maksud (intent) customer yang bertanya tentang produk/layanan bisnis dan mengekstrak entitas penting dari pertanyaan customer secara akurat.

TUJUAN: Hasilkan JSON terstruktur yang memiliki dua komponen:
1. intent → jenis maksud customer (wajib diisi, pilih dari daftar schema)
2. entities → detail informasi yang disebutkan oleh customer (boleh kosong jika tidak ada)

KONTEKS: Ini adalah customer service chatbot untuk melayani pertanyaan customer tentang:
- Produk dan layanan yang tersedia
- Harga dan pricing
- Jam operasional dan lokasi
- Cara pemesanan dan booking
- Status order dan delivery
- Keluhan dan support

PANDUAN KETAT:
- Hanya gunakan intent yang tersedia di schema.
- Fokus pada intent customer service: product_inquiry, pricing_inquiry, booking_request, service_info, dll.
- Jangan isi intent dengan null, unknown, atau error.
- Jika customer tidak eksplisit, pilih intent paling relevan berdasarkan makna pertanyaan.
- Semua output harus valid JSON, tidak boleh ada komentar atau format markdown.
- Output hanya JSON saja, tidak perlu tambahan penjelasan.
- Ekstrak entitas berdasarkan semantic meaning dari pertanyaan customer.
- Pertahankan context customer inquiry dalam ekstraksi.

CONTOH YANG BENAR UNTUK CUSTOMER SERVICE:
Customer: "Berapa harga konseling per sesi?"
→ {{"intent": "pricing_inquiry", "entities": {{"MenuItems": {{"name": "konseling", "category": "per sesi"}}}}}}

Customer: "Jam berapa warung buka?"
→ {{"intent": "business_hours", "entities": {{"Business": {{"business_type": "warung"}}}}}}

Customer: "Gimana cara pesan makanan?"
→ {{"intent": "booking_request", "entities": {{"MenuItems": {{"category": "makanan"}}}}}}

Customer: "Ada layanan delivery gak?"
→ {{"intent": "delivery_inquiry", "entities": {{"Delivery": {{"delivery_method": "delivery"}}}}}}

Customer: "Lokasi toko dimana?"
→ {{"intent": "location_info", "entities": {{"Business": {{"location": "toko"}}}}}}

INI ADALAH SCHEMA INTENT YANG HARUS DIIKUTI:
{schema}

INI ADALAH PERTANYAAN CUSTOMER:
{user_input}

JAWABAN (dalam format JSON):
"""

def parse_intent_entities(text: str) -> dict:
   schema = load_intent_schema()
   prompt = PROMPT_TEMPLATE.format(
       schema=json.dumps(schema, ensure_ascii=False, indent=2),
       user_input=text.strip()
   )
   
   try:
       response = client.chat.completions.create(
           model="gpt-3.5-turbo",
           messages=[
               {"role": "system", "content": "Kamu adalah asisten NLP untuk customer service chatbot yang memahami pertanyaan customer tentang produk dan layanan bisnis."},
               {"role": "user", "content": prompt}
           ],
           temperature=0.0,
       )
       
       content = response.choices[0].message.content.strip()
       print("🟡 Raw LLM content:\n", content)
       
       # ✅ Bersihkan triple backtick jika ada
       if content.startswith("```json"):
           content = content.replace("```json", "").replace("```", "").strip()
       elif content.startswith("```"):
           content = content.replace("```", "").strip()
       
       # ✅ Parse hasil JSON
       parsed = json.loads(content)
       
       # ✅ Validasi minimal: intent harus ada dan bukan null
       if not parsed.get("intent"):
           raise ValueError("Intent kosong atau tidak dikenali.")
       
       return parsed
   
   except Exception as e:
       print(f"🔥 OpenAI API error: {e}")
       import traceback; traceback.print_exc()
       
       # ✅ Fallback: struktur JSON dengan intent customer service default
       fallback_output = {
           "intent": "general_inquiry",
           "entities": {key: {} for key in schema.get("entities", {})}
       }
       return fallback_output