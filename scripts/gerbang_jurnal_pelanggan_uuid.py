"""GERBANG tab jurnal pelanggan PASCA-V247 (kolom customer_id uuid). Baca-saja, modul & koneksi HIDUP.

Subjek: ber-DP, tanpa-DP, ber-NOTA-KREDIT — dipilih yang PUNYA jurnal POSTED di cabangnya.
Tiap subjek: 200 tanpa log galat; himpunan entries == independen (+yatim); total == independen; cabang DP/CN hadir.
SABOTASE: sabotase lama (cabut cast) tak bisa memerah lagi di uuid. Diganti: cabang CN dibuat tak pernah cocok
-> jurnal CN wajib hilang dari entries.
"""
import asyncio
import importlib.util
import logging
import os
import sys
import uuid

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg  # noqa: E402
from starlette.requests import Request  # noqa: E402

PATH = sys.argv[1]
T = "kaos-biru-konveksi"
hasil = []


def catat(s, u, ok, k=""):
    hasil.append((s, u, bool(ok), str(k)[:300]))


class Tangkap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.pesan = []

    def emit(self, record):
        self.pesan.append(record.getMessage())


def muat(nama, path):
    spec = importlib.util.spec_from_file_location(nama, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[nama] = m
    spec.loader.exec_module(m)
    return m


INDEPENDEN = """
WITH p AS (SELECT $2::uuid AS cid),
cabang AS (
  SELECT 'INVOICE' AS jalur, si.journal_id AS jid FROM sales_invoices si, p WHERE si.customer_id = p.cid AND si.tenant_id = $1
  UNION SELECT 'COGS', si.cogs_journal_id FROM sales_invoices si, p WHERE si.customer_id = p.cid AND si.tenant_id = $1
  UNION SELECT 'RP', rp.journal_id FROM receive_payments rp, p WHERE rp.customer_id = p.cid AND rp.tenant_id = $1
  UNION SELECT 'DP', cd.journal_id FROM customer_deposits cd, p WHERE cd.customer_id = p.cid AND cd.tenant_id = $1
  UNION SELECT 'CN', cn.journal_id FROM credit_notes cn, p WHERE cn.customer_id = p.cid AND cn.tenant_id = $1
  UNION SELECT 'SIP', sip.journal_id FROM sales_invoice_payments sip JOIN sales_invoices si ON si.id = sip.invoice_id, p
        WHERE si.customer_id = p.cid AND si.tenant_id = $1
)
SELECT c.jalur, je.id, je.source_type FROM cabang c JOIN journal_entries je ON je.id = c.jid
WHERE je.tenant_id = $1 AND je.status = 'POSTED'
"""
YATIM = """
SELECT je.id FROM journal_entries je WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND je.source_type = 'PAYMENT_RECEIVED'
  AND EXISTS (SELECT 1 FROM sales_invoices si2 WHERE si2.customer_id = $2::uuid AND si2.tenant_id = je.tenant_id
              AND je.description LIKE '%' || si2.invoice_number || '%')
  AND NOT EXISTS (SELECT 1 FROM receive_payment_allocations rpa2 WHERE rpa2.payment_id = je.source_id)
"""


def req(uid):
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"",
                    "state": {"user": {"tenant_id": T, "user_id": str(uid)}}})


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    import app.services.db_pool as dbp

    class FakePool:
        def acquire(self, *a, **k):
            class C:
                async def __aenter__(s):
                    return conn
                async def __aexit__(s, *e):
                    return False
            return C()
        async def release(self, c):
            return None

    async def fake(*a, **k):
        return FakePool()
    dbp.get_db_pool = fake
    mod = muat("app.routers.customers_uji", PATH)
    tangkap = Tangkap()
    logging.getLogger(mod.logger.name).addHandler(tangkap)
    uid = await conn.fetchval("SELECT created_by FROM bank_transactions WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", T)

    ber_dp = await conn.fetchval("""SELECT cd.customer_id::text FROM customer_deposits cd JOIN journal_entries je ON je.id=cd.journal_id
        WHERE cd.tenant_id=$1 AND je.status='POSTED' AND cd.customer_id IS NOT NULL LIMIT 1""", T)
    tanpa_dp = await conn.fetchval("""SELECT c.id::text FROM customers c WHERE c.tenant_id=$1
        AND EXISTS (SELECT 1 FROM sales_invoices si WHERE si.customer_id=c.id AND si.journal_id IS NOT NULL)
        AND NOT EXISTS (SELECT 1 FROM customer_deposits d WHERE d.customer_id=c.id) LIMIT 1""", T)
    ber_cn = await conn.fetchval("""SELECT cn.customer_id::text FROM credit_notes cn JOIN journal_entries je ON je.id=cn.journal_id
        WHERE cn.tenant_id=$1 AND je.status='POSTED' AND cn.customer_id IS NOT NULL LIMIT 1""", T)
    subjek = [("ber-DP", ber_dp), ("tanpa-DP", tanpa_dp), ("ber-NOTA-KREDIT", ber_cn)]
    catat("PRASYARAT", "tiga subjek ada", all(c for _, c in subjek), subjek)

    for label, cid in subjek:
        if not cid:
            continue
        tangkap.pesan.clear()
        try:
            r = await mod.get_customer_journal_entries(req(uid), cid, None, None, None, 1, 100)
            kode = 200
        except Exception as e:  # noqa: BLE001
            r, kode = None, getattr(e, "status_code", type(e).__name__)
        galat_log = [p for p in tangkap.pesan if "Error getting customer journal entries" in p]
        ind = await conn.fetch(INDEPENDEN, T, cid)
        yatim = {x["id"] for x in await conn.fetch(YATIM, T, cid)}
        ind_ids = {x["id"] for x in ind}
        per_jalur = {}
        for x in ind:
            per_jalur.setdefault(x["jalur"], set()).add(x["id"])
        ok200 = kode == 200 and not galat_log
        catat("HIJAU", f"{label}: 200 tanpa log galat", ok200, f"{kode} {galat_log[:1]}")
        if not ok200:
            continue
        data = r["data"]
        ent_ids = {uuid.UUID(e["id"]) for e in data["entries"]}
        harap_ent = ind_ids | yatim
        catat("HIJAU", f"{label}: entries == independen (+yatim {len(yatim)}), tak kosong",
              ent_ids == harap_ent and 0 < len(harap_ent) <= 100,
              f"entries={len(ent_ids)} independen={len(ind_ids)} per_jalur={ {k: len(v) for k, v in per_jalur.items()} }")
        catat("HIJAU", f"{label}: total == independen", data["total"] == len(ind_ids), f"total={data['total']} independen={len(ind_ids)}")
        if label == "ber-DP":
            catat("HIJAU", "ber-DP: jurnal cabang DP ada di entries",
                  bool(per_jalur.get("DP")) and per_jalur["DP"] <= ent_ids, f"DP={len(per_jalur.get('DP', set()))}")
        if label == "ber-NOTA-KREDIT":
            catat("HIJAU", "ber-NOTA-KREDIT: jurnal cabang CN ada di entries",
                  bool(per_jalur.get("CN")) and per_jalur["CN"] <= ent_ids, f"CN={len(per_jalur.get('CN', set()))}")

    src = open(PATH, encoding="utf-8").read()
    A = "cn.customer_id::text = (${customer_id_param_idx}::uuid)::text"
    if src.count(A) != 2:
        catat("SABOTASE", "jangkar cabang CN ditemukan 2x", False, src.count(A))
    elif ber_cn:
        open("/tmp/customers_sabotase.py", "w", encoding="utf-8").write(
            src.replace(A, "cn.customer_id::text = upper((${customer_id_param_idx}::uuid)::text)"))
        ms = muat("app.routers.customers_sabotase", "/tmp/customers_sabotase.py")
        r = await ms.get_customer_journal_entries(req(uid), ber_cn, None, None, None, 1, 100)
        ids = {uuid.UUID(e["id"]) for e in r["data"]["entries"]}
        cn_j = {x["id"] for x in await conn.fetch(INDEPENDEN, T, ber_cn) if x["jalur"] == "CN"}
        catat("SABOTASE", "cabang CN dirusak -> jurnal CN hilang dari entries (gerbang bisa merah)",
              bool(cn_j) and not (cn_j & ids), f"CN={len(cn_j)} tersisa={len(cn_j & ids)}")
    await conn.close()
    # prasyarat 1 + ber-DP 4 + tanpa-DP 3 + ber-CN 4 + sabotase 1
    harap = 13
    for s, u, ok, k in hasil:
        print(("[H] " if ok else "[X] ") + f"{s:10} {u}  | {k}")
    g = sum(1 for h in hasil if not h[2])
    print(f"\ngagal={g} total={len(hasil)} harap={harap} -> {'LENGKAP' if len(hasil) == harap else 'TAK SAH'}")
    sys.exit(0 if g == 0 and len(hasil) == harap else 1)


asyncio.run(main())
