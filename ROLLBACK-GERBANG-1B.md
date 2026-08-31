# ROLLBACK — GERBANG ENTITAS FASE 1b

Branch: `feat/gerbang-quote-si-so` (worktree /root/mh-gerbang1b)
Basis: master `09e4441d` — TIDAK di-merge oleh agen. Merge+deploy = langkah manusia.

## Commit batch ini
(diperbarui tiap commit)
- `beb67a97` feat(gerbang): perluas radius gerbang entitas ke quote/SI/SO (Fase 1b)
- `dc5c5e06` docs(rollback): catat commit beb67a97
- `HEAD` docs(rollback): daftar commit lengkap + catatan pre-empsi orchestrator

## Langkah darurat (SESUDAH deploy, kalau produksi rusak)
git -C /root/milkyhoop-dev reset --keep 09e4441d
docker restart milkyhoop-dev-api_gateway

JANGAN `compose up --force-recreate`.
JANGAN `git reset --hard` (75 entri kotor di /root/milkyhoop-dev bukan milik batch ini).

## Radius perubahan
- backend/api_gateway/app/services/unified_agent/gerbang_entitas.py
- backend/api_gateway/tests/unit/test_gerbang_entitas.py
Tidak ada perubahan di tool_executor.py (situs pemasangan sudah action-agnostic).

## Catatan pre-empsi (WAJIB dibaca sebelum membaca hasil probe)
Di `orchestrator.py` ada cabang klarifikasi deterministik yang menyala LEBIH
DULU untuk nama pihak tak ter-resolve. Radiusnya:
  create_sales_invoice, create_quote, create_credit_note,
  create_receive_payment, create_customer_deposit   -> label "Pelanggan"
  create_bill, create_vendor_credit, ...            -> label "Vendor"
Syaratnya `customer_id` FALSY. Akibatnya:
- quote / SI dengan pelanggan tak terdaftar -> menang cabang orchestrator
  (message_type TEXT, "ketik `tambah pelanggan X`"), gerbang TIDAK menyala.
- create_sales_order TIDAK ada di daftar itu -> gerbang yang menyala.
- sentinel `create_new:<nama>` TRUTHY -> pre-empsi TIDAK menyala -> gerbang
  yang menangkap.
- item yatim TIDAK disentuh cabang itu sama sekali -> selalu gerbang.
