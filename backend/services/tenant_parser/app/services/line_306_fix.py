"""
Direct fix for line 306 syntax error
"""

def fix_line_306():
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'r') as f:
        lines = f.readlines()
    
    # Direct fix for line 306 (index 305)
    if len(lines) > 305:
        current_line = lines[305]
        print(f"Original line 306: {current_line.strip()}")
        
        # Fix the specific unterminated string
        if 'if faq_content.startswith("Q:") and "\\n' in current_line:
            # Complete the unterminated string
            fixed_line = '            if faq_content.startswith("Q:") and "\\nA:" in faq_content:\n'
            lines[305] = fixed_line
            print(f"Fixed line 306: {fixed_line.strip()}")
    
    # Write corrected file
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'w') as f:
        f.writelines(lines)
    
    print("✅ Line 306 syntax error fixed")

if __name__ == "__main__":
    fix_line_306()
