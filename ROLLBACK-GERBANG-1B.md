# ROLLBACK — GERBANG ENTITAS FASE 1b

Branch: `feat/gerbang-quote-si-so` (worktree /root/mh-gerbang1b)
Basis: master `09e4441d` — TIDAK di-merge oleh agen. Merge+deploy = langkah manusia.

## Commit batch ini
(diperbarui tiap commit)
- `beb67a97` feat(gerbang): perluas radius gerbang entitas ke quote/SI/SO (Fase 1b)
- `dc5c5e06` docs(rollback): catat commit beb67a97
- `5deda1ec` docs(rollback): daftar commit lengkap + catatan pre-empsi orchestrator
- `184218be` fix(gerbang): pesan menyebut NAMA barang + KATA BENDA dokumen (Fase 1b-r2)
- `HEAD` docs(rollback): catat commit Fase 1b-r2

## Langkah darurat (SESUDAH deploy, kalau produksi rusak)
git -C /root/milkyhoop-dev reset --keep 09e4441d
docker restart milkyhoop-dev-api_gateway

JANGAN `compose up --force-recreate`.
JANGAN `git reset --hard` (75 entri kotor di /root/milkyhoop-dev bukan milik batch ini).

## Radius perubahan
- backend/api_gateway/app/services/unified_agent/gerbang_entitas.py
- backend/api_gateway/tests/unit/test_gerbang_entitas.py
- backend/api_gateway/app/services/unified_agent/tool_executor.py
  (SEJAK 184218be — SATU baris pemanggilan gerbang meneruskan
  `teks_user=getattr(self, "user_text", None)`. Tidak ada perubahan lain di
  berkas itu; situs pemasangan tetap action-agnostic.)

## Fase 1b-r2 (`184218be`) — yang TIDAK berubah
Mekanisme gerbang tidak disentuh: radius (4 aksi), urutan pemasangan (sesudah
`_resolve_entity_names`, sebelum `validate_payload`/INSERT), keputusan blokir,
dan bentuk amplop rangkap. Yang berubah HANYA kalimat.
Pesan `create_bill` DIJAGA BYTE-EXACT oleh
`test_bill_pesan_byte_exact_tidak_berubah_sedikit_pun` (dua kasus). Kalau
sesudah deploy pesan create_bill berbeda satu karakter pun, itu regresi.

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
