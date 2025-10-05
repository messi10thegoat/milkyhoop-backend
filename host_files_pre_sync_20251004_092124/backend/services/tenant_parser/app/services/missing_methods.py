"""
Add missing advanced prompt methods to enhanced_confidence_engine.py
"""

def add_missing_methods():
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'r') as f:
        content = f.read()
    
    # Missing methods to add
    missing_methods = '''
    def build_medium_prompt(self, query: str, faq_results: List) -> str:
        """Build medium context prompt for GPT-3.5 synthesis"""
        
        context_parts = []
        for i, faq in enumerate(faq_results[:2], 1):
            context_parts.append(f"FAQ {i}: {faq.content}")
        
        context = "\\n\\n".join(context_parts)
        
        prompt = f"""Kamu adalah customer service assistant yang membantu customer. Jawab pertanyaan berdasarkan informasi FAQ yang tersedia.

KONTEKS FAQ:
{context}

PERTANYAAN CUSTOMER: {query}

INSTRUKSI:
- Jawab secara natural dan conversational seperti customer service yang ramah
- Gunakan HANYA informasi dari FAQ yang relevan
- Jika perlu bandingkan produk, berikan perbandingan yang jelas
- Jika ada pertanyaan tentang biaya/admin, sebutkan angka spesifik dari FAQ
- Maksimal 3 kalimat, langsung to the point
- Gunakan bahasa Indonesia yang friendly

JAWABAN:"""

        return prompt
    
    def build_deep_prompt(self, query: str, faq_results: List) -> str:
        """Build deep context prompt for complex queries"""
        
        context_parts = []
        for i, faq in enumerate(faq_results[:3], 1):
            context_parts.append(f"FAQ {i}: {faq.content}")
        
        context = "\\n\\n".join(context_parts)
        
        prompt = f"""Kamu adalah senior customer service yang ahli memberikan rekomendasi. Analisis pertanyaan customer dan berikan jawaban comprehensive.

KONTEKS FAQ LENGKAP:
{context}

PERTANYAAN CUSTOMER: {query}

INSTRUKSI:
- Berikan analisis mendalam berdasarkan kebutuhan customer
- Jika customer menyebutkan budget/kondisi tertentu, berikan rekomendasi yang sesuai
- Bandingkan beberapa produk jika diperlukan
- Jelaskan keuntungan dan pertimbangan masing-masing opsi
- Gunakan data spesifik dari FAQ (biaya admin, minimal setoran, dll)
- Maksimal 4 kalimat, structured dan informatif
- Tone professional tapi tetap friendly

REKOMENDASI:"""

        return prompt'''
    
    # Find insertion point before last method
    lines = content.split('\\n')
    
    # Insert before create_enhanced_confidence_engine function
    for i, line in enumerate(lines):
        if 'def create_enhanced_confidence_engine():' in line:
            lines.insert(i-1, missing_methods)
            break
    
    # Write updated content
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'w') as f:
        f.write('\\n'.join(lines))
    
    print("✅ Missing prompt methods added")

if __name__ == "__main__":
    add_missing_methods()
