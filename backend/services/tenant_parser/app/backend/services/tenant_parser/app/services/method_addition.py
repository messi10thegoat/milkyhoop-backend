"""
Add missing extract_answer_only method to enhanced_confidence_engine.py
"""

def add_missing_method():
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'r') as f:
        content = f.read()
    
    # Check if method already exists
    if 'def extract_answer_only(' in content:
        print("✅ Method already exists")
        return
    
    # Find insertion point (before the last method)
    method_to_add = '''
    def extract_answer_only(self, faq_content: str) -> str:
        """Extract clean answer from FAQ content"""
        try:
            if faq_content.startswith("Q:") and "\\nA:" in faq_content:
                return faq_content.split("\\nA:", 1)[1].strip()
            return faq_content.strip()
        except:
            return faq_content'''
    
    # Insert before the last method or class end
    lines = content.split('\n')
    insert_idx = -1
    
    # Find last method in class
    for i in range(len(lines)-1, -1, -1):
        if lines[i].strip().startswith('def ') and '    def ' in lines[i]:
            insert_idx = i
            break
    
    if insert_idx > 0:
        # Find end of that method
        for j in range(insert_idx+1, len(lines)):
            if lines[j].strip() == '' and (j+1 >= len(lines) or not lines[j+1].startswith('    ')):
                lines.insert(j+1, method_to_add)
                break
    else:
        # Insert before class end
        lines.insert(-2, method_to_add)
    
    # Write updated content
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'w') as f:
        f.write('\n'.join(lines))
    
    print("✅ extract_answer_only method added")

if __name__ == "__main__":
    add_missing_method()
