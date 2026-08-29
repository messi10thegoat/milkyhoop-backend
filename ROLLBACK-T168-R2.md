# Rencana rollback T168 Ronde 2 (ditulis SEBELUM menyentuh berkas)
Baseline: master = 86bdd9d8
Perubahan: LOG-ONLY, satu fungsi (_backfill_top_item_id) di
backend/api_gateway/app/services/unified_agent/tool_executor.py
Rollback:
  cd /root/milkyhoop-dev
  git revert --no-edit <sha_baru>
  git push deploy master
  docker restart milkyhoop-dev-api_gateway
Kalau container crashloop (SyntaxError):
  git checkout 86bdd9d8 -- backend/api_gateway/app/services/unified_agent/tool_executor.py
  docker restart milkyhoop-dev-api_gateway
DILARANG: git reset --hard di /root/milkyhoop-dev (frontend/ = bundle live).
