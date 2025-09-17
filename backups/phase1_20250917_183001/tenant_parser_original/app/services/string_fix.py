"""
Fix unterminated string literal in enhanced_confidence_engine.py
"""

def fix_string_literal():
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'r') as f:
        lines = f.readlines()
    
    # Fix common string literal issues around line 306
    for i, line in enumerate(lines):
        # Fix unterminated strings
        if 'if faq_content.startswith("Q:") and "\\n' in line and not line.strip().endswith('":'):
            # This is likely the broken line - fix it
            if 'A:"' not in line:
                lines[i] = line.replace('\\n', '\\nA:')
                print(f"✅ Fixed line {i+1}: unterminated string")
        
        # Fix any other quote issues
        if line.count('"') % 2 != 0 and not line.strip().startswith('#'):
            # Odd number of quotes - likely unclosed
            if not line.strip().endswith('"'):
                lines[i] = line.rstrip() + '"\n'
                print(f"✅ Fixed line {i+1}: unclosed quote")
    
    # Write fixed content
    with open('/app/backend/services/tenant_parser/app/services/enhanced_confidence_engine.py', 'w') as f:
        f.writelines(lines)
    
    print("✅ String literal syntax fixed")

if __name__ == "__main__":
    fix_string_literal()
