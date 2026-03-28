-- V126: user_favorites table for sidebar pin feature
CREATE TABLE IF NOT EXISTS user_favorites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  panel_key TEXT NOT NULL,
  label TEXT NOT NULL,
  icon_key TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, tenant_id, panel_key)
);

CREATE INDEX IF NOT EXISTS idx_user_favorites_user_tenant ON user_favorites(user_id, tenant_id);

ALTER TABLE user_favorites ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'user_favorites' AND policyname = 'user_favorites_tenant_policy'
  ) THEN
    CREATE POLICY user_favorites_tenant_policy ON user_favorites
      USING (tenant_id = current_setting('app.tenant_id', true));
  END IF;
END $$;
