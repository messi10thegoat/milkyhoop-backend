#!/bin/bash
# Build validation script for api_gateway

echo "=== API GATEWAY BUILD VALIDATION ==="

echo "1. Python syntax validation:"
find . -name "*.py" -exec python3 -m py_compile {} \; || exit 1

echo "2. Prisma import validation:"
python3 -c "from backend.api_gateway.libs.milkyhoop_prisma import Prisma; print('Prisma import: OK')" || exit 1

echo "3. Critical imports validation:"
python3 -c "
import sys
sys.path.append('/app')
from backend.api_gateway.app import main
print('Main app import: OK')
" || exit 1

echo "✅ All validations passed"
