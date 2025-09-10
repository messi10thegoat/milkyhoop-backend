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

# ✅ Prompt template eksplisit & ketat
PROMPT_TEMPLATE = """
PERAN: Kamu adalah asisten NLP untuk memahami maksud (intent) pengguna dan mengekstrak entitas penting dari kalimat pengguna secara akurat dan rapi.

TUJUAN: Hasilkan JSON terstruktur yang memiliki dua komponen:
1. intent → jenis maksud pengguna (wajib diisi, pilih dari daftar schema)
2. entities → detail informasi yang disebutkan oleh pengguna (boleh kosong jika tidak ada)

PANDUAN KETAT:
- Hanya gunakan intent yang tersedia di schema.
- Jangan isi intent dengan null, unknown, atau error.
- Jika pengguna tidak eksplisit, pilih intent paling relevan berdasarkan makna kalimat.
- Semua output harus valid JSON, tidak boleh ada komentar atau format markdown.
- Output hanya JSON saja, tidak perlu tambahan penjelasan.
- old_item harus exact content dari database, bukan kategori umum (contoh: "WA 0811-234-5678" bukan "nomor WA").
- new_item harus content replacement lengkap, bukan fragmen.
- Ekstrak entitas berdasarkan semantic meaning, bukan keyword matching.
- Pertahankan context bisnis dan domain knowledge dalam ekstraksi.
- old_item harus berupa CORE TERMS/KEYWORDS yang semantically match dengan database content
- Ekstrak kata kunci utama dari conversational phrase, bukan ambil keseluruhan kalimat
- Prioritas: kata benda, frasa spesifik yang kemungkinan ada di database
- Hindari kata sambung, referensi temporal, atau filler words


# CONTOH SEMANTIC EXTRACTION:
User: "Ganti yang kemarin tentang delivery, ubah jadi bisa COD"
→ {{"intent": "faq_update", "entities": {{"entities": {{"FAQ": {{"old_item": "delivery", "new_item": "bisa COD"}}}}}}}}

User: "Update info kontak customer service, ganti nomor WA jadi 0812-345-6789"
→ {{"intent": "faq_update", "entities": {{"entities": {{"FAQ": {{"old_item": "kontak customer service", "new_item": "0812-345-6789"}}}}}}}}

User: "Yang tadi tentang jam operasional itu, perpanjang sampai jam 10 malam"
→ {{"intent": "faq_update", "entities": {{"entities": {{"FAQ": {{"old_item": "jam operasional", "new_item": "sampai jam 10 malam"}}}}}}}}

User: "Tambahin FAQ baru tentang garansi produk, jawabnya garansi 1 tahun"
→ {{"intent": "faq_create", "entities": {{"entities": {{"FAQ": {{"question": "garansi produk", "answer": "garansi 1 tahun"}}}}}}}}



CONTOH YANG BENAR:
User: "Buat FAQ emergency contact, jawabnya hubungi 119"
→ {{"intent": "faq_create", "entities": {{"entities": {{"FAQ": {{"question": "emergency contact", "answer": "hubungi 119"}}}}}}}}

User: "Edit FAQ pengiriman, ganti JNE/TIKI ekspedisi jadi Go-Send saja"
→ {{"intent": "faq_update", "entities": {{"entities": {{"FAQ": {{"old_item": "JNE/TIKI ekspedisi", "new_item": "Go-Send saja"}}}}}}}}

INI ADALAH SCHEMA INTENT YANG HARUS DIIKUTI:
{schema}

INI ADALAH KALIMAT PENGGUNA:
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
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Kamu adalah asisten NLP untuk memahami maksud dan ekstraksi entitas."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
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

        # ✅ Fallback: struktur JSON kosong yang tetap valid
        fallback_output = {
            "intent": "error",
            "entities": {key: {} for key in schema.get("entities", {})}
        }
        return fallback_output
