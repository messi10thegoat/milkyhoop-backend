-- Migration: Add tenant_rules table for rule_engine service
-- Created: 2025-11-17
-- Purpose: Store deterministic rules for product mapping, tax calculation, etc.

CREATE TABLE IF NOT EXISTS "tenant_rules" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "tenant_id" TEXT NOT NULL,
    "rule_id" TEXT NOT NULL,
    "rule_type" VARCHAR(50) NOT NULL,
    "rule_yaml" TEXT NOT NULL,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "priority" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,
    
    CONSTRAINT "tenant_rules_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "tenant_rules_tenant_id_rule_id_key" UNIQUE ("tenant_id", "rule_id")
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS "tenant_rules_tenant_id_rule_type_is_active_idx" ON "tenant_rules"("tenant_id", "rule_type", "is_active");
CREATE INDEX IF NOT EXISTS "tenant_rules_tenant_id_priority_idx" ON "tenant_rules"("tenant_id", "priority");
