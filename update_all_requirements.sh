#!/bin/bash
# NUCLEAR OPTION: Replace all requirements with container state

echo "WARNING: This will replace ALL requirements.txt files"
read -p "Are you sure? (yes/no): " confirm

if [ "$confirm" = "yes" ]; then
    LATEST_AUDIT=$(ls -dt dependency_audit_* | head -1)
    CORRECTED_DIR="$LATEST_AUDIT/corrected_requirements"
    
    for service in cust_orchestrator ragllm_service ragcrud_service auth_service; do
        corrected_file="$CORRECTED_DIR/${service}_corrected_requirements.txt"
        target_file="backend/services/$service/requirements.txt"
        
        if [ -f "$corrected_file" ] && [ -f "$target_file" ]; then
            cp "$corrected_file" "$target_file"
            echo "Updated: $target_file"
        fi
    done
    
    echo "All requirements updated to container state"
else
    echo "Update cancelled"
fi
