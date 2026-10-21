"""Harness nyata BACA-SAJA: kunci idempotensi vs dokumen sumber (unit idempotensi-sumber, 21 Okt 2026).

Untuk SEMUA baris idempotency_keys ber-source_type "buat dokumen" (utils.idempotency.SUMBER_BUAT):
  (1) id dokumen TERBACA oleh id_dokumen_tersimpan (bentuk result nyata) -- bila tidak, pemeriksa sumber diam-diam
      tak berlaku (mode gagal paling berbahaya) => GAGAL;
  (2) tabel/kolom di peta ada di DB (information_schema);
  (3) INFO: berapa kunci aktif (<24 j) basi menurut kebijakan (dokumen hilang; voided utk kunci default RCV:/BP:/INVOICE_PAYMENT:).
      Basi tidak digagalkan (dibuang saat dipakai); angkanya dilaporkan untuk dipantau.
Jalankan: python uji-nyata-idempotensi-sumber.py [baru] [/out/x.json]
"""
import asyncio, os, sys
sys.path.insert(0, "/app/backend/api_gateway")
os.environ.setdefault("OPENAI_API_KEY", "x")
import asyncpg
from app.utils import idempotency as I


async def main():
    c = await asyncpg.connect(os.environ["URL"], server_settings={"default_transaction_read_only": "on"})
    gagal = 0
    for st, (tabel, kol, _) in I.SUMBER_BUAT.items():
        ada = await c.fetchval("select count(*) from information_schema.columns where table_name=$1 and column_name in ('id','tenant_id')", tabel)
        kolok = True if not kol else bool(await c.fetchval("select 1 from information_schema.columns where table_name=$1 and column_name=$2", tabel, kol))
        ok = ada == 2 and kolok
        print(("PASS" if ok else "FAIL"), f"peta {st} -> {tabel}.{kol}")
        gagal += 0 if ok else 1
    baris = await c.fetch("select tenant_id, key, source_type, result, result_id, expires_at>now() aktif from idempotency_keys where source_type = any($1::text[])", list(I.SUMBER_BUAT))
    import json
    tak_terbaca = 0; basi_aktif = {}
    for r in baris:
        res = r["result"]
        if isinstance(res, str):
            res = json.loads(res)
        did = I.id_dokumen_tersimpan(res, r["result_id"])
        if did is None:
            tak_terbaca += 1
            continue
        if not r["aktif"]:
            continue
        tabel, kol, basi_st = I.SUMBER_BUAT[r["source_type"]]
        row = await c.fetchrow(f"select {kol or 'id'} as st from {tabel} where id=$1 and tenant_id=$2", did, r["tenant_id"])
        default = r["key"].startswith(("RCV:", "BP:", "INVOICE_PAYMENT:"))
        if row is None or (default and kol and row["st"] in basi_st):
            basi_aktif[r["source_type"]] = basi_aktif.get(r["source_type"], 0) + 1
    ok = tak_terbaca == 0
    print(("PASS" if ok else "FAIL"), f"id dokumen terbaca di {len(baris)} baris kunci (tak terbaca={tak_terbaca})")
    gagal += 0 if ok else 1
    print("INFO kunci aktif basi menurut kebijakan (dibuang saat dipakai):", basi_aktif or "0")
    print(f"RINGKAS IDEMPOTENSI-SUMBER gagal={gagal} [baris={len(baris)}]")
    await c.close()
    sys.exit(1 if gagal else 0)

asyncio.run(main())
