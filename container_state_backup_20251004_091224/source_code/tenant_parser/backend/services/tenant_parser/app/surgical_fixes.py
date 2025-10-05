"""
SURGICAL FIXES for tenant_parser_service.py
1. Stricter confidence thresholds
2. Enhanced FAQ-only LLM prompts  
3. Better topic mismatch detection
"""

def apply_confidence_threshold_fix(original_code):
    """Fix 1: Stricter confidence thresholds"""
    
    # Replace permissive thresholds with stricter ones
    fixes = [
        # Direct FAQ threshold - raised
        ('elif confidence >= 0.65:', 'elif confidence >= 0.75:'),
        
        # Synthesis threshold - raised significantly  
        ('elif confidence >= 0.30:', 'elif confidence >= 0.50:'),
        
        # Deep analysis threshold - raised
        ('elif confidence >= 0.15:', 'elif confidence >= 0.35:'),
        
        # Topic similarity threshold - stricter
        ('if topic_similarity < 0.3:', 'if topic_similarity < 0.4:'),
    ]
    
    fixed_code = original_code
    for old, new in fixes:
        if old in fixed_code:
            fixed_code = fixed_code.replace(old, new)
            print(f"✅ Applied: {old} → {new}")
    
    return fixed_code

def apply_hallucination_prevention_fix(original_code):
    """Fix 2: Anti-hallucination LLM prompts"""
    
    # Find and replace medium prompt
    old_medium_prompt = '''prompt = f"""Kamu adalah customer service assistant untuk BCA. Jawab pertanyaan customer berdasarkan informasi FAQ yang tersedia.

KONTEKS FAQ:
{context}

PERTANYAAN CUSTOMER: {query}

INSTRUKSI:
- Jawab secara natural dan conversational seperti customer service yang ramah
- Gunakan informasi dari FAQ yang relevan
- Jika perlu bandingkan produk, berikan perbandingan yang jelas
- Jika ada pertanyaan tentang biaya/admin, sebutkan angka spesifik dari FAQ
- Maksimal 3 kalimat, langsung to the point
- Gunakan bahasa Indonesia yang friendly

JAWABAN:"""'''

    new_medium_prompt = '''prompt = f"""You are a helpful assistant. Answer STRICTLY based on the provided FAQ context only.

FAQ CONTEXT:
{context}

CUSTOMER QUESTION: {query}

CRITICAL RULES:
- Use ONLY information explicitly stated in the FAQ context above
- Do NOT add any information from your general knowledge
- If the question cannot be answered from the FAQ, respond: "Informasi tersebut tidak tersedia"
- Maximum 3 sentences, natural and helpful
- Be professional and direct

RESPONSE:"""'''

    # Find and replace deep prompt  
    old_deep_prompt = '''prompt = f"""Kamu adalah senior customer service BCA yang ahli memberikan rekomendasi produk. Analisis pertanyaan customer dan berikan jawaban comprehensive.

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

REKOMENDASI:"""'''

    new_deep_prompt = '''prompt = f"""You are a helpful assistant. Provide comprehensive analysis STRICTLY based on the available FAQ context.

COMPLETE FAQ CONTEXT:
{context}

CUSTOMER QUESTION: {query}

CRITICAL RULES:
- Use ONLY information explicitly available in the FAQ context above
- Do NOT add any information from your general knowledge
- If information is missing from FAQ, clearly state "That information is not available"
- Compare options only using data from the FAQ
- Maximum 4 sentences, structured and informative
- Prioritize accuracy over completeness

ANALYSIS:"""'''

    fixed_code = original_code
    
    if old_medium_prompt in fixed_code:
        fixed_code = fixed_code.replace(old_medium_prompt, new_medium_prompt)
        print("✅ Applied: Anti-hallucination medium prompt")
    
    if old_deep_prompt in fixed_code:
        fixed_code = fixed_code.replace(old_deep_prompt, new_deep_prompt)
        print("✅ Applied: Anti-hallucination deep prompt")
    
    return fixed_code

def apply_semantic_filtering_fix(original_code):
    """Fix 3: Enhanced semantic filtering"""
    
    # Strengthen topic mismatch detection
    old_filter = '''if topic_similarity < 0.3:  # Low topic overlap
                logger.info(f"🚫 Topic mismatch detected: query topics {query_topics} vs FAQ topics {faq_topics}")
                confidence *= 0.2  # Drastically reduce confidence for topic mismatch'''
    
    new_filter = '''if topic_similarity < 0.4:  # Stricter topic overlap
                logger.info(f"🚫 SEMANTIC FILTER: Topic mismatch detected - query topics {query_topics} vs FAQ topics {faq_topics}")
                return 0.0  # Force deflection for topic mismatch'''
    
    if old_filter in original_code:
        fixed_code = original_code.replace(old_filter, new_filter)
        print("✅ Applied: Enhanced semantic filtering")
        return fixed_code
    
    return original_code

def apply_all_surgical_fixes(file_content):
    """Apply all surgical fixes"""
    print("🔧 Applying surgical fixes...")
    
    # Apply fixes in sequence
    fixed_content = apply_confidence_threshold_fix(file_content)
    fixed_content = apply_hallucination_prevention_fix(fixed_content)
    fixed_content = apply_semantic_filtering_fix(fixed_content)
    
    print("🎯 All surgical fixes applied")
    return fixed_content

if __name__ == "__main__":
    # Read original file
    with open('/app/tenant_parser_service.py', 'r') as f:
        original_content = f.read()
    
    # Apply fixes
    fixed_content = apply_all_surgical_fixes(original_content)
    
    # Write fixed file
    with open('/app/tenant_parser_service_fixed.py', 'w') as f:
        f.write(fixed_content)
    
    print("💾 Fixed file saved as tenant_parser_service_fixed.py")
