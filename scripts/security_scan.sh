#!/bin/bash
# ==============================================
# MilkyHoop Security Scanner
# Scans for CVEs and security issues
# ==============================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "MilkyHoop Security Scanner"
echo "=========================================="
echo ""

ERRORS=0
WARNINGS=0

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Install security tools if not present
install_tools() {
    echo -e "${YELLOW}Installing security tools...${NC}"
    pip3 install -q pip-audit safety bandit 2>/dev/null || true
}

# Scan Python dependencies for CVEs
scan_python_deps() {
    echo -e "\n${YELLOW}[1/5] Scanning Python dependencies for CVEs...${NC}"

    # Find all requirements files
    for req in $(find . -name "requirements*.txt" -type f 2>/dev/null | head -10); do
        echo "  Scanning: $req"
        if command_exists pip-audit; then
            pip-audit -r "$req" 2>/dev/null || WARNINGS=$((WARNINGS+1))
        fi
    done

    # Also check with safety
    if command_exists safety; then
        echo "  Running safety check..."
        safety check --full-report 2>/dev/null || WARNINGS=$((WARNINGS+1))
    fi
}

# Scan Python code for security issues
scan_python_code() {
    echo -e "\n${YELLOW}[2/5] Scanning Python code for security issues (bandit)...${NC}"

    if command_exists bandit; then
        bandit -r ./backend -f txt -ll 2>/dev/null | head -50 || true
    else
        echo "  Bandit not installed, skipping..."
    fi
}

# Scan Node.js dependencies
scan_node_deps() {
    echo -e "\n${YELLOW}[3/5] Scanning Node.js dependencies...${NC}"

    for pkg in $(find . -name "package.json" -not -path "*/node_modules/*" -type f 2>/dev/null | head -5); do
        dir=$(dirname "$pkg")
        echo "  Scanning: $pkg"
        (cd "$dir" && npm audit --json 2>/dev/null | head -30) || WARNINGS=$((WARNINGS+1))
    done
}

# Check for secrets in code
scan_secrets() {
    echo -e "\n${YELLOW}[4/5] Scanning for hardcoded secrets...${NC}"

    # Common secret patterns
    PATTERNS=(
        "password\s*=\s*['\"][^'\"]+['\"]"
        "api_key\s*=\s*['\"][^'\"]+['\"]"
        "secret\s*=\s*['\"][^'\"]+['\"]"
        "token\s*=\s*['\"][^'\"]+['\"]"
        "AWS_SECRET"
        "PRIVATE_KEY"
    )

    for pattern in "${PATTERNS[@]}"; do
        matches=$(grep -rn --include="*.py" --include="*.js" --include="*.ts" "$pattern" . 2>/dev/null | grep -v "node_modules" | grep -v ".env" | head -5)
        if [ -n "$matches" ]; then
            echo -e "  ${RED}Potential secret found:${NC}"
            echo "$matches"
            WARNINGS=$((WARNINGS+1))
        fi
    done

    echo "  Secret scan complete"
}

# Check Docker security
scan_docker() {
    echo -e "\n${YELLOW}[5/5] Checking Docker security...${NC}"

    # Check for privileged containers
    if grep -q "privileged: true" docker-compose.yml 2>/dev/null; then
        echo -e "  ${RED}WARNING: Privileged containers found!${NC}"
        ERRORS=$((ERRORS+1))
    fi

    # Check for host network mode
    if grep -q "network_mode: host" docker-compose.yml 2>/dev/null; then
        echo -e "  ${RED}WARNING: Host network mode found!${NC}"
        WARNINGS=$((WARNINGS+1))
    fi

    # Check for exposed ports
    exposed=$(grep -E "^\s+-\s+\"?[0-9]+:" docker-compose.yml 2>/dev/null | wc -l)
    echo "  Exposed ports: $exposed"

    echo "  Docker scan complete"
}

# Summary
print_summary() {
    echo ""
    echo "=========================================="
    echo "Security Scan Summary"
    echo "=========================================="

    if [ $ERRORS -gt 0 ]; then
        echo -e "${RED}Errors: $ERRORS${NC}"
    else
        echo -e "${GREEN}Errors: 0${NC}"
    fi

    if [ $WARNINGS -gt 0 ]; then
        echo -e "${YELLOW}Warnings: $WARNINGS${NC}"
    else
        echo -e "${GREEN}Warnings: 0${NC}"
    fi

    if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
        echo -e "\n${GREEN}All security checks passed!${NC}"
    else
        echo -e "\n${YELLOW}Please review the findings above.${NC}"
    fi
}

# Main
cd /root/milkyhoop-dev

install_tools
scan_python_deps
scan_python_code
scan_node_deps
scan_secrets
scan_docker
print_summary

exit $ERRORS
