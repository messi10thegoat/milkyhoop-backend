"""Harness NYATA W-20261014-DEPOSIT-JENIS (V422): jenis penerimaan uang muka 'DP' | 'PELUNASAN' -- LABEL saja.
- DB: kolom customer_deposits.jenis + CHECK chk_cust_deposit_jenis ada (sesudah V422).
- kaos + grapgrap READ ONLY: jenis efektif SQL == Python untuk SEMUA baris; baris lama di proforma PELUNASAN (jenis
  NULL) tampil PELUNASAN (grapgrap 5, kaos 8 saat diukur 14 Okt) lewat daftar, detail, riwayat SO, riwayat uang muka,
  kwitansi (PDF ditulis ke /out, teksnya diperiksa pdftotext di host).
- kaos TULIS di transaksi ROLLBACK (satu koneksi, handler sungguhan): POST tanpa jenis + proforma PELUNASAN ->
  PELUNASAN; eksplisit DP di proforma PELUNASAN -> DP (eksplisit menang); tanpa proforma -> DP; eksplisit PELUNASAN
  tanpa proforma -> PELUNASAN; PATCH draf jenis -> tersimpan + riwayat "jenis pembayaran"; jurnal = 0 (draf).
  Sesudahnya nol jejak.
Dijalankan SEBELUM V422 = KONTROL MERAH (kolom belum ada)."""
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

sys.path.insert(0, "/app")
import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from backend.api_gateway.app.routers import customer_deposits as CD  # noqa: E402
from backend.api_gateway.app.schemas.customer_deposits import (  # noqa: E402
    CreateCustomerDepositRequest, UpdateCustomerDepositRequest)
from backend.api_gateway.app.services import deposit_jenis as DJ  # noqa: E402
from backend.api_gateway.app.services import so_riwayat as SR  # noqa: E402
from backend.api_gateway.app.services.pdf_service import get_pdf_service  # noqa: E402
from backend.api_gateway.app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

GG, KAOS = "grapgrap-manado", "kaos-biru-konveksi"
HARAP_PELUNASAN_LAMA = {GG: 5, KAOS: 8}
gagal = 0


class Rollback(Exception):
    pass


def cek(ok, label, info=""):
    global gagal
    print(("PASS " if ok else "FAIL ") + label + (f" | {info}" if info else ""))
    if not ok:
        gagal += 1


def req(user_id, tenant):
    return SimpleNamespace(headers={}, state=SimpleNamespace(
        user={"user_id": user_id, "tenant_id": tenant, "role": "OWNER"}))


async def pemilik(c, t):
    return str(await c.fetchval(
        "SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id "
        "WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND r.is_active ORDER BY utr.is_primary DESC LIMIT 1", t))


async def boleh(_m):
    return True


def daftar_args(**kw):
    a = dict(status="all", customer_id=None, sales_order_id=None, search=None, date_from=None, date_to=None,
             skip=0, limit=100, sort_by="created_at", sort_order="desc")
    a.update(kw)
    return a


async def tulis_pdf(c, ctx, dep_id, nama):
    k = await CD.muat_pdf_kwitansi_uang_muka(c, ctx, str(dep_id))
    pdf = get_pdf_service().generate_receipt_pdf(k["receipt_data"], k["tenant_info"])
    with open(f"/out/{nama}.pdf", "wb") as f:
        f.write(pdf)
    print(f"JSON pdf {nama} purpose_label={k['receipt_data']['purpose_label']!r} jenis={k['receipt_data'].get('jenis')}")
    return k["receipt_data"]


class PoolTunggal:
    """Semua acquire() = koneksi yang SAMA di dalam transaksi luar (transaksi dalam = savepoint)."""
    def __init__(self, c):
        self.c = c

    @asynccontextmanager
    async def _a(self):
        yield self.c

    def acquire(self):
        return self._a()


async def baca_tenant(baca, t):
    async with baca.acquire() as c:
        try:
            rows = await c.fetch(
                f"SELECT cd.id, cd.deposit_number, cd.jenis, cd.sales_order_id, pf.purpose, "
                f"{DJ.sql_jenis_efektif('cd')} AS efektif FROM customer_deposits cd "
                "LEFT JOIN proformas pf ON pf.id = cd.proforma_id AND pf.tenant_id = cd.tenant_id WHERE cd.tenant_id = $1", t)
        except Exception as e:  # noqa: BLE001
            cek(False, f"{t}: baca jenis efektif", f"{type(e).__name__}: {e}")
            return
        beda = [r["deposit_number"] for r in rows if r["efektif"] != DJ.jenis_efektif(r["jenis"], r["purpose"])]
        cek(not beda and rows, f"{t}: jenis efektif SQL == Python untuk SEMUA {len(rows)} baris", beda[:5])
        lama = [r for r in rows if r["jenis"] is None and r["purpose"] == "PELUNASAN"]
        cek(len(lama) == HARAP_PELUNASAN_LAMA[t] and all(r["efektif"] == "PELUNASAN" for r in lama),
            f"{t}: baris lama di proforma PELUNASAN tampil PELUNASAN (harap {HARAP_PELUNASAN_LAMA[t]})",
            f"{len(lama)}: {[r['deposit_number'] for r in lama]}")
        tersimpan = sum(1 for r in rows if r["jenis"] is not None)
        cek(tersimpan == 0, f"{t}: tanpa backfill -- jenis tersimpan = 0 baris", tersimpan)

        own = await pemilik(c, t)
        ctx = {"tenant_id": t, "user_id": own}
        x = next((r for r in lama if r["sales_order_id"]), None)
        dp = next((r for r in rows if r["efektif"] == "DP" and r["sales_order_id"]), None)
        if not x:
            cek(False, f"{t}: ada baris PELUNASAN lama ber-SO")
            return
        d = await CD.list_customer_deposits(req(own, t), **daftar_args(sales_order_id=str(x["sales_order_id"])))
        it = {i["deposit_number"]: i["jenis"] for i in d["items"]}
        cek(it.get(x["deposit_number"]) == "PELUNASAN", f"{t}: daftar ?sales_order_id -> {x['deposit_number']} jenis PELUNASAN", it)
        d = await CD.get_customer_deposit(req(own, t), x["id"])
        cek(d["data"]["jenis"] == "PELUNASAN", f"{t}: detail {x['deposit_number']} jenis PELUNASAN", d["data"]["jenis"])
        r = await SR.riwayat_uang_muka(c, t, x["id"], boleh)
        teks = [e["ringkas"] for e in r["events"] if e["ringkas"] and x["deposit_number"] in e["ringkas"]]
        cek(r["jenis"] == "PELUNASAN" and any(s.startswith(f"Pelunasan {x['deposit_number']}") for s in teks)
            and not any(s.startswith("Uang muka") for s in teks),
            f"{t}: riwayat uang muka {x['deposit_number']} -> 'Pelunasan ...'", teks[:3])
        r = await SR.riwayat_so(c, t, x["sales_order_id"], boleh)
        teks = [e["ringkas"] for e in r["events"] if e["ringkas"] and x["deposit_number"] in e["ringkas"]]
        cek(any(s.startswith(f"Pelunasan {x['deposit_number']}") for s in teks)
            and not any(s.startswith(f"Uang muka {x['deposit_number']}") for s in teks),
            f"{t}: riwayat SO -> 'Pelunasan {x['deposit_number']} ...'", teks[:3])
        rd = await tulis_pdf(c, ctx, x["id"], f"{t}-pelunasan")
        cek(rd["purpose_label"] == "Pelunasan Pesanan", f"{t}: kwitansi {x['deposit_number']} purpose_label", rd["purpose_label"])
        if dp:
            rd = await tulis_pdf(c, ctx, dp["id"], f"{t}-dp")
            cek(rd["purpose_label"] == "Uang Muka Pesanan", f"{t}: kwitansi DP {dp['deposit_number']} purpose_label",
                rd["purpose_label"])


async def tulis_kaos(tulis):
    async with tulis.acquire() as c:
        own = await pemilik(c, KAOS)
        ctx = {"tenant_id": KAOS, "user_id": own}
        n0 = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id = $1", KAOS)
        j0 = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", KAOS)
        pf = await c.fetchrow(
            """SELECT p.id, p.sales_order_id, so.customer_id, so.customer_name FROM proformas p
               JOIN sales_orders so ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id
               WHERE p.tenant_id = $1 AND p.purpose = 'PELUNASAN' AND p.status = 'issued' AND so.status = 'confirmed'
               ORDER BY p.created_at DESC LIMIT 1""", KAOS)
        akun = await c.fetchval(
            "SELECT account_id FROM customer_deposits WHERE tenant_id = $1 AND payment_method = 'cash' "
            "AND status <> 'void' ORDER BY created_at DESC LIMIT 1", KAOS)
        cek(pf is not None and akun is not None, "kaos: ada proforma PELUNASAN issued di SO confirmed + akun kas")
        hari = await tanggal_dokumen(c, KAOS)
        CD.get_pool = lambda: _sebagai(PoolTunggal(c))

        def badan(**kw):
            b = dict(customer_id=str(pf["customer_id"]) if pf["customer_id"] else None,
                     customer_name=pf["customer_name"] or "TES E2E", amount=1, deposit_date=hari,
                     payment_method="cash", account_id=str(akun), sales_order_id=str(pf["sales_order_id"]),
                     notes="TES E2E DEPOSIT-JENIS (ROLLBACK)")
            b.update(kw)
            return CreateCustomerDepositRequest(**b)

        kasus = [
            ("proforma PELUNASAN tanpa jenis -> PELUNASAN", badan(proforma_id=str(pf["id"])), "PELUNASAN"),
            ("proforma PELUNASAN + eksplisit DP -> DP (eksplisit menang)", badan(proforma_id=str(pf["id"]), jenis="DP"), "DP"),
            ("tanpa proforma tanpa jenis -> DP", badan(), "DP"),
            ("tanpa proforma + eksplisit PELUNASAN -> PELUNASAN", badan(jenis="PELUNASAN"), "PELUNASAN"),
        ]
        try:
            async with c.transaction():
                ids = []
                for label, b, harap in kasus:
                    try:
                        h = await CD.create_customer_deposit(req(own, KAOS), b)
                    except HTTPException as e:
                        cek(False, f"kaos POST {label}", f"{e.status_code} {e.detail}")
                        ids.append(None)
                        continue
                    did = uuid.UUID(h["data"]["id"])
                    ids.append(did)
                    st = await c.fetchval("SELECT jenis FROM customer_deposits WHERE id = $1", did)
                    det = (await CD.get_customer_deposit(req(own, KAOS), did))["data"]["jenis"]
                    cek(st == harap and det == harap, f"kaos POST {label}", f"tersimpan {st} detail {det}")
                if ids[3]:
                    await CD.update_customer_deposit(req(own, KAOS), ids[3], UpdateCustomerDepositRequest(jenis="DP"))
                    st = await c.fetchval("SELECT jenis FROM customer_deposits WHERE id = $1", ids[3])
                    ring = await c.fetchval(
                        """SELECT metadata->>'ringkas' FROM audit_logs WHERE entity_id = $1::uuid
                           AND "eventType" = 'DEPOSIT_UPDATED' ORDER BY "createdAt" DESC LIMIT 1""", str(ids[3]))
                    cek(st == "DP" and ring and "jenis pembayaran" in ring, "kaos PATCH draf jenis PELUNASAN -> DP + riwayat",
                        f"{st} | {ring}")
                if ids[0]:
                    rd = await tulis_pdf(c, ctx, ids[0], "kaos-baru-pelunasan")
                    cek(rd["purpose_label"] == "Pelunasan Pesanan", "kaos: kwitansi uang muka baru PELUNASAN",
                        rd["purpose_label"])
                    r = await SR.riwayat_uang_muka(c, KAOS, ids[0], boleh)
                    cek(any(e["ringkas"].startswith("Pelunasan DEP-") for e in r["events"] if e["ringkas"]),
                        "kaos: riwayat uang muka baru 'Pelunasan DEP-...'", [e["ringkas"] for e in r["events"]][:3])
                jn = await c.fetchval("SELECT count(*) FROM journal_entries WHERE tenant_id = $1", KAOS)
                cek(jn == j0, "kaos: draf tanpa jurnal (jenis tak menyentuh pembukuan)", f"{j0} -> {jn}")
                raise Rollback
        except Rollback:
            pass
        n1 = await c.fetchval("SELECT count(*) FROM customer_deposits WHERE tenant_id = $1", KAOS)
        cek(n1 == n0, "kaos: sesudah ROLLBACK nol jejak", f"{n0} -> {n1}")


async def _sebagai(p):
    return p


async def main():
    tulis = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    baca = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2,
                                     server_settings={"default_transaction_read_only": "on"})
    async with baca.acquire() as c:
        try:
            async with c.transaction():
                await c.execute("CREATE TEMP TABLE _x(i int)")
            cek(False, "pool baca menolak tulis")
        except asyncpg.exceptions.ReadOnlySQLTransactionError:
            cek(True, "pool baca menolak tulis (READ ONLY)")
        kol = await c.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name = 'customer_deposits' "
                               "AND column_name = 'jenis'")
        chk = await c.fetchval("SELECT count(*) FROM pg_constraint WHERE conname = 'chk_cust_deposit_jenis'")
        cek(kol == 1 and chk == 1, "V422: kolom jenis + CHECK ada", f"kolom {kol} check {chk}")

    async def gpb():
        return baca
    CD.get_pool = gpb
    for t in (GG, KAOS):
        try:
            await baca_tenant(baca, t)
        except Exception as e:  # noqa: BLE001
            cek(False, f"{t}: blok READ ONLY", f"{type(e).__name__}: {e}")
    try:
        await tulis_kaos(tulis)
    except Exception as e:  # noqa: BLE001
        cek(False, "kaos: blok TULIS ROLLBACK", f"{type(e).__name__}: {e}")
    await tulis.close()
    await baca.close()
    print(f"RINGKAS gagal={gagal}")


asyncio.run(main())
