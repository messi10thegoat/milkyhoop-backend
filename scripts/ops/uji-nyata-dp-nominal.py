"""UJI NYATA DP nominal rupiah + kontrak P2 + pindah uang muka antar-proforma (P1), kaos 'TES E2E'. 11 Okt 2026.
SEMUA tulis di-ROLLBACK (satu koneksi ber-tx luar, pool palsu; tiap skenario savepoint). Handler ASLI. Cek AKHIR: hitungan
kaos (je, jl, audit, bank_tx, uang muka, proforma, 3 tabel urutan) == sebelum. Kontrol merah: jalankan dengan kode master."""
import asyncio
import json
import os
import sys
import uuid
from datetime import date
from decimal import Decimal

sys.path.insert(0, "/app/backend/api_gateway")
import asyncpg
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

TENANT = "kaos-biru-konveksi"
USER = "0bccdb25-fdf0-4e99-9024-b9a20846f76c"
SO_ID = uuid.UUID("5933637c-5b27-407c-bf36-1ca4d966bf81")   # SO-2609-0263 confirmed 100 jt, tanpa proforma/DP
BANK = "56842900-0ae4-4c25-8ad5-d0726a36e21b"
COA_BANK = "15105bf6-183e-4f93-abcc-5689f22fa394"
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append((nama, bool(ok)))
    print(("PASS " if ok else "FAIL ") + nama + (f" | {info}" if info else ""), flush=True)


def lihat(nama, v):
    print(f"UKUR {nama}: {json.dumps(v, default=str, sort_keys=True)}", flush=True)


async def jumlah(conn):
    q = {
        "je": "SELECT count(*) FROM journal_entries WHERE tenant_id=$1",
        "jl": "SELECT count(*) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id=$1",
        "audit": "SELECT count(*) FROM audit_logs WHERE tenant_id=$1",
        "bank_tx": "SELECT count(*) FROM bank_transactions WHERE tenant_id=$1",
        "dep": "SELECT md5(string_agg(id::text||status||amount::text||COALESCE(proforma_id::text,''), ',' ORDER BY id)) FROM customer_deposits WHERE tenant_id=$1",
        "pf": "SELECT md5(string_agg(id::text||status||amount::text, ',' ORDER BY id)) FROM proformas WHERE tenant_id=$1",
        "seq_j": "SELECT COALESCE(sum(last_number),0) FROM journal_number_sequences WHERE tenant_id=$1",
        "seq_pf": "SELECT md5(string_agg(t::text, ',' ORDER BY t::text)) FROM proforma_sequences t WHERE tenant_id=$1",
        "seq_dep": "SELECT md5(string_agg(t::text, ',' ORDER BY t::text)) FROM customer_deposit_sequences t WHERE tenant_id=$1",
    }
    return {k: await conn.fetchval(v, TENANT) for k, v in q.items()}


class _Acq:
    def __init__(self, c): self.c = c
    async def __aenter__(self): return self.c
    async def __aexit__(self, *a): return False


class PoolSatu:
    def __init__(self, c): self.c = c
    def acquire(self): return _Acq(self.c)


class _Batal(Exception):
    pass


async def sp(conn, fn):
    out = {}
    try:
        async with conn.transaction():
            out["v"] = await fn()
            raise _Batal()
    except _Batal:
        pass
    return out["v"]


def req():
    r = Request({"type": "http", "method": "POST", "headers": [(b"user-agent", b"qa-dp")], "path": "/", "query_string": b"", "state": {}})
    r.state.user = {"tenant_id": TENANT, "user_id": USER, "role": "OWNER"}
    return r


async def galat(coro):
    try:
        return await coro
    except HTTPException as e:
        return {"HTTP": e.status_code, "detail": e.detail}


async def main():
    import app.routers.proformas as PF
    import app.routers.customer_deposits as CD
    import app.services.db_pool as DBP
    from app.routers.proformas import CreateProformaRequest, CancelProformaRequest
    from app.schemas.customer_deposits import CreateCustomerDepositRequest
    from app.services import so_posisi as P
    from app.services.proforma_terbayar import terbayar_proforma

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    outer = conn.transaction()
    await outer.start()
    pool = PoolSatu(conn)

    async def gp():
        return pool
    PF.get_pool = gp
    CD.get_pool = gp
    DBP.get_db_pool = gp
    so = await conn.fetchrow("SELECT * FROM sales_orders WHERE id=$1 AND tenant_id=$2", SO_ID, TENANT)
    sebelum = await jumlah(conn)
    print("sebelum:", sebelum, flush=True)

    async def buat_pf(amount=None, percent=None):
        b = CreateProformaRequest(sales_order_id=str(SO_ID), purpose="DP", amount=amount, percent_of_order=percent,
                                  proforma_date=date(2026, 10, 11))
        r = await PF.create_proforma(req(), b)
        pid = r["id"] if isinstance(r, dict) and "id" in r else r["data"]["id"]
        await PF.issue_proforma(req(), str(pid), Response())
        return uuid.UUID(str(pid))

    async def dp(amount, pid=None):
        b = CreateCustomerDepositRequest(customer_id=str(so["customer_id"]), customer_name=so["customer_name"], amount=amount,
                                         deposit_date=date(2026, 10, 11), payment_method="transfer", account_id=COA_BANK,
                                         bank_account_id=BANK, auto_post=True, sales_order_id=str(SO_ID),
                                         proforma_id=str(pid) if pid else None, notes="TES E2E dp-kali",
                                         idempotency_key=f"tes-e2e-dp-{uuid.uuid4()}")
        r = await CD.create_customer_deposit(req(), b)
        d = r if isinstance(r, dict) else r.model_dump()
        return d.get("data", d)

    async def keadaan(pid):
        tb = (await terbayar_proforma(conn, TENANT, [SO_ID])).get(pid, {})
        fk = (await P.fakta_daftar(conn, TENANT, [dict(so)], date(2026, 10, 11)))
        det = await PF.get_proforma_detail(req(), str(pid))
        det = det.get("data", det)
        return {"paid": tb.get("paid"), "posisi": list(fk.values())[0]["position_text"], "excess": det.get("excess_amount"),
                "sisa_so": list(fk.values())[0]["outstanding_amount"],
                "detail": {k: det.get(k) for k in ("amount", "paid_amount", "outstanding_amount", "is_fully_paid", "status")}}

    async def jurnal_dp(dep_id):
        return [dict(r) for r in await conn.fetch(
            """SELECT coa.account_code, coa.name, jl.debit, jl.credit FROM customer_deposits d
               JOIN journal_lines jl ON jl.journal_id=d.journal_id JOIN chart_of_accounts coa ON coa.id=jl.account_id
               WHERE d.id=$1 AND d.tenant_id=$2 ORDER BY jl.line_number""", uuid.UUID(str(dep_id)), TENANT)]

    from app.routers import quotes as QT
    try:
        from app.routers.customer_deposits import PindahProformaRequest
    except ImportError:  # kontrol merah di kode master: S6 harus GAGAL sebagai cek, bukan crash
        PindahProformaRequest = None
    QT.get_pool = gp

    async def pindah(dep_id, pid, pratinjau=False):
        b = PindahProformaRequest(target_proforma_id=str(pid), reason="TES E2E salah nominal")
        f = CD.preview_move_customer_deposit_proforma if pratinjau else CD.move_customer_deposit_proforma
        return (await f(req(), uuid.UUID(str(dep_id)), b))["data"]

    async def hitung_buku():
        return {k: await conn.fetchval(v, TENANT) for k, v in {
            "je": "SELECT count(*) FROM journal_entries WHERE tenant_id=$1",
            "jl": "SELECT count(*) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id=$1",
            "bank_tx": "SELECT count(*) FROM bank_transactions WHERE tenant_id=$1",
            "saldo": "SELECT md5(string_agg(jl.account_id::text||':'||jl.debit::text||':'||jl.credit::text, ',' ORDER BY jl.id)) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id WHERE je.tenant_id=$1",
        }.items()}

    try:
        # S1: proforma 5 jt, DP 3 jt + 2 jt BERTAUT -> sebagian lalu lunas, jurnal Dr bank / Cr uang muka
        async def s1():
            pid = await buat_pf(amount=Decimal("5000000"))
            d1 = await dp(3000000, pid)
            k1 = await keadaan(pid)
            d2 = await dp(2000000, pid)
            return k1, await keadaan(pid), await jurnal_dp(d1["id"]), await jurnal_dp(d2["id"])
        k1, k2, j1, j2 = await sp(conn, s1)
        lihat("S1", [k1, k2])
        cek("S1a DP 3 jt: paid 3 jt, sisa 2 jt, belum lunas, posisi Menunggu DP",
            k1["detail"]["paid_amount"] == 3000000 and k1["detail"]["outstanding_amount"] == 2000000
            and not k1["detail"]["is_fully_paid"] and k1["posisi"] == "Menunggu DP")
        cek("S1b DP 2 jt: paid 5 jt, sisa 0, lunas, posisi Diproses, sisa SO 95 jt",
            k2["detail"]["paid_amount"] == 5000000 and k2["detail"]["outstanding_amount"] == 0 and k2["detail"]["is_fully_paid"]
            and k2["posisi"] == "Diproses" and k2["sisa_so"] == 95000000)
        cek("S1c jurnal tiap DP = Dr 1-10201 / Cr 2-10500 sebesar nominalnya",
            [(r["account_code"], r["debit"], r["credit"]) for r in j1] == [("1-10201", Decimal("3000000.00"), Decimal("0.00")), ("2-10500", Decimal("0.00"), Decimal("3000000.00"))]
            and [(r["account_code"], r["debit"], r["credit"]) for r in j2] == [("1-10201", Decimal("2000000.00"), Decimal("0.00")), ("2-10500", Decimal("0.00"), Decimal("2000000.00"))])

        # S3: lebih bayar -> diterima; kontrak P2: paid = amount, sisa 0, excess 1 jt
        async def s3():
            pid = await buat_pf(amount=Decimal("5000000"))
            r = await galat(dp(6000000, pid))
            return r, await keadaan(pid)
        r3, k3 = await sp(conn, s3)
        lihat("S3", k3)
        cek("S3 lebih bayar diterima (tak ditolak diam) dan kontrak P2: paid 5 jt, sisa 0, excess 1 jt, posisi Diproses",
            "HTTP" not in r3 and k3["detail"] == {"amount": 5000000.0, "paid_amount": 5000000.0, "outstanding_amount": 0.0,
                                                    "is_fully_paid": True, "status": "issued"} and k3["excess"] == 1000000.0
            and k3["posisi"] == "Diproses")

        # S4/S5: nominal proforma SELALU rupiah (diketik pecahan / persen tak bulat)
        async def s45():
            a = await buat_pf(amount=Decimal("5000377.50"))
            b = await buat_pf(percent=Decimal("12.3456789"))
            c = await buat_pf(percent=Decimal("62.31"))
            return [await conn.fetchval("SELECT amount FROM proformas WHERE id=$1", x) for x in (a, b, c)]
        n = await sp(conn, s45)
        lihat("S4/S5 amount", n)
        cek("S4 nominal diketik 5.000.377,50 -> 5.000.378 (rupiah)", n[0] == Decimal("5000378"))
        cek("S5 persen 12.3456789% x 100 jt -> 12.345.679 (HALF_UP rupiah); 62.31% -> 62.310.000", n[1] == Decimal("12345679") and n[2] == Decimal("62310000"))

        # S6: tiruan Pergatsi + PERBAIKAN P1 (terbit baru -> pindah DP -> batal lama), nol jurnal/bank
        async def s6():
            lama = await buat_pf(amount=Decimal("5000000"))
            await conn.execute("UPDATE proformas SET amount = 5000377.50, percent_of_order = 62.31 WHERE id=$1", lama)  # data lama
            d = await dp(5000000, lama)
            k0 = await keadaan(lama)
            baru = await buat_pf(amount=Decimal("5000000"))
            buku0 = await hitung_buku()
            a0 = await conn.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1", TENANT)
            pv = await pindah(d["id"], baru, pratinjau=True)
            buku_pv = await hitung_buku()
            a_pv = await conn.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1", TENANT)
            tautan_pv = await conn.fetchval("SELECT proforma_id FROM customer_deposits WHERE id=$1", uuid.UUID(str(d["id"])))
            hasil = await pindah(d["id"], baru)
            buku1 = await hitung_buku()
            a1 = await conn.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1", TENANT)
            ulang = await pindah(d["id"], baru)
            a2 = await conn.fetchval("SELECT count(*) FROM audit_logs WHERE tenant_id=$1", TENANT)
            k_lama, k_baru = await keadaan(lama), await keadaan(baru)
            batal = await galat(PF.cancel_proforma(req(), str(lama), CancelProformaRequest(reason="TES E2E salah nominal"), Response()))
            k_akhir = await keadaan(baru)
            riw = (a_pv - a0, a1 - a_pv, a2 - a1)
            return dict(k0=k0, pv=pv, hasil=hasil, ulang=ulang, k_lama=k_lama, k_baru=k_baru, batal=batal, k_akhir=k_akhir,
                        buku0=buku0, buku_pv=buku_pv, buku1=buku1, tautan_pv=tautan_pv, lama=lama, d=d["id"], riw=riw)
        try:
            r = await sp(conn, s6)
        except Exception as e:
            cek("S6 perbaikan Pergatsi lewat pindah uang muka berjalan", False, repr(e)[:200])
            r = None
        if r is not None:
          lihat("S6", {k: v for k, v in r.items() if k not in ("buku0", "buku_pv", "buku1")})
          cek("S6a tiruan Pergatsi: sisa 377,5 -> Menunggu DP", r["k0"]["detail"]["outstanding_amount"] == 377.5 and r["k0"]["posisi"] == "Menunggu DP")
          cek("S6b pratinjau: dapat dilanjutkan, journals [], bank None, tautan TAK berubah, buku identik",
              r["pv"]["can_proceed"] and r["pv"]["journals"] == [] and r["pv"]["bank"] is None and r["pv"]["preview"] is True
              and r["tautan_pv"] == r["lama"] and r["buku_pv"] == r["buku0"])
          cek("S6c pindah: je/jl/bank_tx/isi baris jurnal IDENTIK sebelum-sesudah (nol jurnal, nol mutasi bank)", r["buku1"] == r["buku0"],
              f"{r['buku0']} {r['buku1']}")
          cek("S6d sesudah pindah: proforma lama paid 0, proforma baru paid 5 jt lunas",
              r["k_lama"]["detail"]["paid_amount"] == 0 and r["k_baru"]["detail"]["paid_amount"] == 5000000 and r["k_baru"]["detail"]["is_fully_paid"])
          cek("S6e pratinjau == aksi (sesudah per proforma sama)", r["pv"]["proformas"] == r["hasil"]["proformas"] and not r["hasil"]["preview"])
          cek("S6f ulang aksi yang sama = no-op idempoten (no_change, tanpa galat)", r["ulang"]["no_change"] is True and r["ulang"]["can_proceed"])
          cek("S6h riwayat: pratinjau +0, pindah +1 baris audit, ulang no-op +0", r["riw"] == (0, 1, 0), str(r["riw"]))
          cek("S6g batal proforma lama kini DIIZINKAN (tanpa pembayaran eksplisit) -> posisi Diproses",
              "HTTP" not in (r["batal"] if isinstance(r["batal"], dict) else {}) and r["k_akhir"]["posisi"] == "Diproses", str(r["batal"])[:200])

        # S7: konversi Penawaran -> SO menandai nominal DP 'manual' (SQL yang disunting DIJALANKAN)
        async def s7():
            qid = uuid.UUID("e0666cd2-00e8-4980-9aee-a06dc96f4d48")  # QUO-2610-0012 kaos, sent, tanpa DP -> diisi DP uji
            await conn.execute("UPDATE quotes SET dp_amount = 60000, dp_percent = 60 WHERE id=$1 AND tenant_id=$2", qid, TENANT)
            hasil = await QT._konversi_penawaran(conn, {"tenant_id": TENANT, "user_id": uuid.UUID(USER)}, str(qid), None)
            return await conn.fetchrow("SELECT dp_amount, dp_percent, dp_amount_source FROM sales_orders WHERE quote_id=$1 AND tenant_id=$2", qid, TENANT)
        so7 = await sp(conn, s7)
        lihat("S7", dict(so7) if so7 else None)
        cek("S7 SO hasil konversi: dp_amount 60.000, dp_amount_source 'manual'", so7 and so7["dp_amount"] == Decimal("60000") and so7["dp_amount_source"] == "manual")
    finally:
        await outer.rollback()
    akhir = await jumlah(conn)
    cek("AKHIR nol jejak (je, jl, audit, bank_tx, dep, pf, urutan)", akhir == sebelum, "" if akhir == sebelum else f"{sebelum} {akhir}")
    await conn.close()
    g = [n for n, ok in HASIL if not ok]
    print(f"RINGKAS {len(HASIL)-len(g)}/{len(HASIL)}", "GAGAL: " + "; ".join(g) if g else "SEMUA LULUS")

asyncio.run(main())
