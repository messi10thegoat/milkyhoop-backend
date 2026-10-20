"""Harness NYATA S2-NK nota kredit seragam (20 Okt 2026). BACA saja (transaksi READ ONLY, ROLLBACK), grapgrap + kaos, SEMUA NK.
 1. sisa JURNAL (sisa_nk_jurnal_many) == sisa_kredit_nk (penjaga lama) untuk SEMUA NK bukan-draf
 2. tiap baris daftar: aksi == aksi_nk(sisa jurnal); sisa_keadaan konsisten dgn sisa jurnal; status_text == peta FE r288 (+ bertaut posted = Terpakai)
 3. ?aksi=belum_terpakai == himpunan baris ber-aksi; summary {aksi}_count/_value == jumlah/Σ sisa himpunan itu
 4. with_totals (tanpa filter, ?aksi=, ?search=) == Σ dari semua baris hasil filter
 5. summary ?search= : total == baris daftar bukan-void dgn ?search= yang sama
 6. detail == baris daftar (aksi, sisa_keadaan, status_text, remaining_amount, remaining_state)"""
import asyncio, inspect, json, os, sys
from decimal import Decimal
sys.path.insert(0, "/app"); os.environ.setdefault("OPENAI_API_KEY", "x")
from types import SimpleNamespace
import asyncpg
from uuid import UUID
from backend.api_gateway.app.routers import credit_notes as CD
from backend.api_gateway.app.services import nota_kredit_daftar as U

# peta FE r288 (frontend recipes/statusNotaKredit.ts LABEL_STATUS_NOTA_KREDIT), kunci = status_detail;
# PENGECUALIAN putusan MASTER 20 Okt: NK bertaut berstatus posted -> "Terpakai" (NetSuite Fully Applied)
FE_R288 = {"draft": "Draf", "posted": "Terbit", "partial": "Sebagian terpakai", "applied": "Terpakai", "void": "Batal",
           "refunded": "Dikembalikan", "partially_refunded": "Sebagian dikembalikan"}
gagal = 0


def cek(ok, l, i=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + l + (f" | {i}" if i else "")); gagal += 0 if ok else 1


def kw(fn, **o):
    d = {n: getattr(p.default, "default", p.default) for n, p in inspect.signature(fn).parameters.items()
         if n != "request" and p.default is not inspect.Parameter.empty}
    d.update(o); return d


async def semua(req, **o):
    out, skip, r = [], 0, None
    while True:
        r = await CD.list_credit_notes(req, **kw(CD.list_credit_notes, skip=skip, limit=100, **o))
        r = r if isinstance(r, dict) else r.model_dump()
        out += r["items"]; skip += 100
        if skip >= r["total"]: break
    return out, r


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    tx = c.transaction(readonly=True); await tx.start()
    class Pool:
        def acquire(self_):
            class A:
                async def __aenter__(s): return c
                async def __aexit__(s, *a): return False
            return A()
    async def gp(): return Pool()
    CD.get_pool = gp
    # SET LOCAL di jalur baca: aman dalam transaksi read-only
    try:
        for t in ("grapgrap-manado", "kaos-biru-konveksi"):
            uid = await c.fetchval('SELECT id FROM "User" WHERE "tenantId"=$1 ORDER BY "createdAt" LIMIT 1', t)
            req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": t, "user_id": str(uid)}), headers={})
            # 1
            cn = await c.fetch("SELECT id, credit_note_number, status, total_amount, amount_applied, amount_refunded, original_invoice_id FROM credit_notes WHERE tenant_id=$1", t)
            per = await U.sisa_nk_jurnal_many(c, t, [r["id"] for r in cn])
            b1 = [(r["credit_note_number"], r["status"], str(per[str(r["id"])])) for r in cn if r["status"] != "draft"
                  and per[str(r["id"])] != CD.sisa_kredit_nk(r["status"], r["total_amount"], r["amount_applied"], r["amount_refunded"], r["original_invoice_id"])]
            cek(not b1, f"{t}: sisa jurnal NK == sisa_kredit_nk ({sum(1 for r in cn if r['status'] != 'draft')} NK bukan-draf)", str(b1[:3]))
            # 2
            rows, r0 = await semua(req)
            n_db = await c.fetchval("SELECT count(*) FROM credit_notes WHERE tenant_id=$1", t)
            cek(len(rows) == n_db, f"{t}: daftar memuat semua NK", f"{len(rows)} vs {n_db}")
            e_aksi, e_kd, e_tx = [], [], []
            for x in rows:
                sisa = per[x["id"]]
                if x["aksi"] != U.aksi_nk(x["status"], x["original_invoice_id"], sisa): e_aksi.append((x["credit_note_number"], x["aksi"], str(sisa)))
                if not U.keadaan_cocok_sisa(x["sisa_keadaan"], x["total_amount"], sisa): e_kd.append((x["credit_note_number"], x["sisa_keadaan"], x["total_amount"], str(sisa)))
                harap_tx = "Terpakai" if x["status"] == "posted" and x["original_invoice_id"] else FE_R288.get(x["status_detail"])
                if x["status_text"] != harap_tx: e_tx.append((x["credit_note_number"], x["status_detail"], x["status_text"]))
            cek(not e_aksi, f"{t}: aksi baris == aksi_nk(sisa jurnal)", str(e_aksi[:3]))
            cek(not e_kd, f"{t}: sisa_keadaan konsisten dengan sisa jurnal", str(e_kd[:3]))
            cek(not e_tx, f"{t}: status_text == peta FE r288 (+ bertaut posted = Terpakai)", str(e_tx[:3]))
            hit = {}
            for x in rows: hit[x["sisa_keadaan"]] = hit.get(x["sisa_keadaan"], 0) + 1
            print("  keadaan", t, json.dumps(hit, sort_keys=True))
            # 3
            harap = {x["id"] for x in rows if x["aksi"] == U.AKSI_BELUM_TERPAKAI}
            ra, _ = await semua(req, aksi=U.AKSI_BELUM_TERPAKAI)
            cek({x["id"] for x in ra} == harap, f"{t}: ?aksi=belum_terpakai == baris ber-aksi ({len(harap)})")
            sm = (await CD.get_credit_notes_summary(req, **kw(CD.get_credit_notes_summary)))
            sm = sm if isinstance(sm, dict) else sm.model_dump()
            d = sm["data"]
            nilai = sum(per[i] for i in harap)
            cek(d["belum_terpakai_count"] == len(harap) and Decimal(str(d["belum_terpakai_value"])) == nilai,
                f"{t}: summary belum_terpakai == daftar", f"{d['belum_terpakai_count']}/{d['belum_terpakai_value']} vs {len(harap)}/{nilai}")
            # 4
            kata = next((x["customer_name"].split()[0] for x in rows if x["customer_name"]), "a")
            for o in ({}, {"aksi": U.AKSI_BELUM_TERPAKAI}, {"search": kata}):
                rr, rt = await semua(req, with_totals=True, **o)
                st = sum(Decimal(str(x["total_amount"])) for x in rr if x["status"] != "void")
                ss = sum(per[x["id"]] for x in rr)
                cek(Decimal(str(rt["sum_total"])) == st and Decimal(str(rt["sum_sisa"])) == ss,
                    f"{t}: with_totals {o or 'tanpa filter'} == Σ baris", f"{rt['sum_total']}/{rt['sum_sisa']} vs {st}/{ss}")
            # 5
            rs, _ = await semua(req, search=kata)
            ss = (await CD.get_credit_notes_summary(req, **kw(CD.get_credit_notes_summary, search=kata)))
            ss = ss if isinstance(ss, dict) else ss.model_dump()
            cek(ss["data"]["total"] == sum(1 for x in rs if x["status"] != "void") and ss["data"]["void_count"] == sum(1 for x in rs if x["status"] == "void")
                and ss["data"]["belum_terpakai_count"] == sum(1 for x in rs if x["aksi"] == U.AKSI_BELUM_TERPAKAI),
                f"{t}: summary ?search={kata!r} == daftar ?search= ({len(rs)} baris)")
            # 6
            e_det = []
            for x in rows:
                det = await CD.get_credit_note(req, UUID(x["id"]))
                det = (det if isinstance(det, dict) else det.model_dump())["data"]
                for f in ("aksi", "sisa_keadaan", "status_text", "remaining_amount", "status_detail"):
                    if det.get(f) != x.get(f): e_det.append((x["credit_note_number"], f, det.get(f), x.get(f)))
            cek(not e_det, f"{t}: detail == baris daftar ({len(rows)} NK)", str(e_det[:3]))
    finally:
        await tx.rollback(); await c.close()
    print("RINGKAS gagal=%d" % gagal)

asyncio.run(main())
