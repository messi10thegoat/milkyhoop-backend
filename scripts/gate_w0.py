"""Gerbang W0 (BE) — idempotency POST /api/sales-orders + flag fitur /api/permissions/me.

Jalan di kontainer SEKALI-PAKAI terhadap DB SCRATCH (lihat gate_w0.sh); handler dipanggil
in-process dengan get_pool dialihkan ke pool scratch. Sebelum apa pun: current_database()
WAJIB = DB scratch, kalau tidak berhenti (harness pernah menulis prod lewat koneksi lain).
Kontainer tidak diberi env prod sama sekali -> koneksi nyasar gagal, bukan menulis prod.

Keluar 0 = LULUS semua, 1 = ada GAGAL, 2 = galat alat.
"""

import asyncio
import inspect
import os
import sys
from datetime import date

import asyncpg

DSN = os.environ["W0_DSN"]
DB = os.environ["W0_DB"]
MODE = os.environ.get("W0_MODE", "hijau")  # "merah" = kode master: harus GAGAL
KAOS, GRAP = "kaos-biru-konveksi", "grapgrap-manado"
UA, UB = "00000000-0000-4000-8000-00000000000a", "00000000-0000-4000-8000-00000000000b"  # created_by = UUID
hasil = []


def cek(nama, ok, detail=""):
    hasil.append(ok)
    print(("LULUS " if ok else "GAGAL ") + nama + (f" — {detail}" if detail else ""))


class _Req:
    def __init__(self, tenant, user, kunci=None):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}

        class _S:
            pass

        self.state = _S()
        self.state.user = {"tenant_id": tenant, "user_id": user}


class _Resp:
    def __init__(self):
        self.headers = {}


async def main():
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    async with pool.acquire() as c:
        db = await c.fetchval("SELECT current_database()")
    if db != DB:
        print(f"GALAT ALAT: terhubung ke {db}, bukan {DB} — berhenti")
        return 2

    from fastapi import HTTPException
    from app.routers import sales_orders as so
    from app.routers import team_members as tm
    from app.schemas.sales_orders import CreateSalesOrderRequest

    async def _pool():
        return pool

    so.get_pool = _pool
    tm.get_pool = _pool

    async with pool.acquire() as c:
        cust = {t: await c.fetchval("SELECT id::text FROM customers WHERE tenant_id=$1 ORDER BY id LIMIT 1", t)
                for t in (KAOS, GRAP)}
    if not all(cust.values()):
        print(f"GALAT ALAT: pelanggan scratch kosong {cust}")
        return 2

    def badan(tenant, harga=10000, nomor=None):
        return CreateSalesOrderRequest(
            order_date=date(2026, 9, 25), customer_id=cust[tenant], customer_name="UJI W0",
            order_number=nomor,
            items=[{"description": "Kaos uji W0", "quantity": 2, "unit_price": harga}],
        )

    async def post(tenant, user, kunci, b):
        r = _Resp()
        try:
            if "response" in inspect.signature(so.create_sales_order).parameters:
                out = await so.create_sales_order(_Req(tenant, user, kunci), b, r)
            else:  # kode lama (kontrol merah): tanpa parameter response
                out = await so.create_sales_order(_Req(tenant, user, kunci), b)
            return 200, out.model_dump(mode="json"), r.headers
        except HTTPException as e:
            return e.status_code, {"detail": e.detail}, r.headers

    async def jumlah_so(tenant):
        async with pool.acquire() as c:
            return await c.fetchval(
                "SELECT count(*) FROM sales_orders WHERE tenant_id=$1 AND customer_name='UJI W0'", tenant)

    # 1. kunci sama + isi sama -> 1 SO, respons identik, header replay
    n0 = await jumlah_so(KAOS)
    s1, b1, h1 = await post(KAOS, UA, "k-satu", badan(KAOS))
    s2, b2, h2 = await post(KAOS, UA, "k-satu", badan(KAOS))
    n1 = await jumlah_so(KAOS)
    cek("kunci sama dua kali -> 1 SO", s1 == 200 and s2 == 200 and n1 - n0 == 1, f"{s1}/{s2} SO +{n1 - n0}")
    cek("respons ulang IDENTIK (id + nomor)", s1 == s2 == 200 and (b1.get("data") or {}).get("id") and b1 == b2,
        f"{b1.get('data')} vs {b2.get('data')}")
    cek("header X-Idempotent-Replay hanya di ulangan",
        h2.get("X-Idempotent-Replay") == "true" and "X-Idempotent-Replay" not in h1, f"{h1} / {h2}")

    # 2. kunci sama + isi beda -> 409, tanpa SO baru
    s3, b3, _ = await post(KAOS, UA, "k-satu", badan(KAOS, harga=99999))
    cek("kunci sama isi beda -> 409 + code IDEMPOTENCY_KEY_REUSED",
        s3 == 409 and await jumlah_so(KAOS) == n1
        and (b3.get("detail") or {}).get("code") == "IDEMPOTENCY_KEY_REUSED"
        and (b3.get("detail") or {}).get("message") == "Idempotency-Key sudah dipakai untuk pesanan lain",
        f"{s3} {b3}")

    # 3. kunci sama beda TENANT -> SO terpisah, tanpa bocor respons
    g0 = await jumlah_so(GRAP)
    s4, b4, h4 = await post(GRAP, UA, "k-satu", badan(GRAP))
    cek("kunci sama beda tenant -> SO sendiri, tanpa replay",
        s4 == 200 and await jumlah_so(GRAP) - g0 == 1 and b4.get("data", {}).get("id") != b1.get("data", {}).get("id")
        and "X-Idempotent-Replay" not in h4, f"{s4} {b4.get('data')}")

    # 4. kunci sama beda PENGGUNA (tenant sama) -> SO sendiri
    s5, b5, h5 = await post(KAOS, UB, "k-satu", badan(KAOS))
    cek("kunci sama beda pengguna -> SO sendiri",
        s5 == 200 and await jumlah_so(KAOS) - n1 == 1 and "X-Idempotent-Replay" not in h5, f"{s5}")
    n2 = await jumlah_so(KAOS)

    # 4b. Q-006: 409 membawa order_id + order_number SO MILIK ruang kunci pemanggil
    def _d(b):
        return b.get("detail") if isinstance(b.get("detail"), dict) else {}
    cek("409 memuat order_id + order_number SO asli",
        s3 == 409 and _d(b3).get("order_id") == (b1.get("data") or {}).get("id")
        and _d(b3).get("order_number") == (b1.get("data") or {}).get("order_number")
        and _d(b3).get("order_id") is not None, f"{_d(b3)}")
    s9, b9, _ = await post(GRAP, UA, "k-satu", badan(GRAP, harga=77777))
    cek("409 beda tenant -> SO tenant itu sendiri, bukan milik kaos",
        s9 == 409 and _d(b9).get("order_id") == (b4.get("data") or {}).get("id")
        and _d(b9).get("order_id") != (b1.get("data") or {}).get("id"), f"{_d(b9)}")
    s10, b10, _ = await post(KAOS, UB, "k-satu", badan(KAOS, harga=77777))
    cek("409 beda pengguna -> SO pengguna itu sendiri, bukan milik pengguna A",
        s10 == 409 and _d(b10).get("order_id") == (b5.get("data") or {}).get("id")
        and _d(b10).get("order_id") != (b1.get("data") or {}).get("id"), f"{_d(b10)}")
    cek("409 tetap tanpa SO baru", await jumlah_so(KAOS) == n2)

    # 5. gagal dulu (409 nomor manual dipakai) lalu sah dengan kunci sama -> 1 SO
    nomor_dipakai = (b1.get("data") or {}).get("order_number") or "TAK-ADA"
    s6, b6, _ = await post(KAOS, UA, "k-dua", badan(KAOS, nomor=nomor_dipakai))
    s7, b7, h7 = await post(KAOS, UA, "k-dua", badan(KAOS))
    cek("409 nomor-dipakai TANPA code idempotensi (FE bisa membedakan)",
        s6 == 409 and not isinstance(b6.get("detail"), dict), f"{b6}")
    cek("gagal lalu sah, kunci sama -> 1 SO (gagal tak dicatat)",
        s6 == 409 and s7 == 200 and await jumlah_so(KAOS) - n2 == 1 and "X-Idempotent-Replay" not in h7,
        f"{s6} lalu {s7}")
    n3 = await jumlah_so(KAOS)

    # 6. dua POST BERSAMAAN kunci sama -> 1 SO
    r = await asyncio.gather(post(KAOS, UA, "k-tiga", badan(KAOS)), post(KAOS, UA, "k-tiga", badan(KAOS)))
    cek("dua POST bersamaan kunci sama -> 1 SO",
        [x[0] for x in r] == [200, 200] and await jumlah_so(KAOS) - n3 == 1 and r[0][1] == r[1][1],
        f"{[x[0] for x in r]} SO +{await jumlah_so(KAOS) - n3}")
    n4 = await jumlah_so(KAOS)

    # 7. tanpa kunci = perilaku lama: dua SO kembar yang sah
    await post(KAOS, UA, None, badan(KAOS))
    await post(KAOS, UA, None, badan(KAOS))
    cek("tanpa kunci -> 2 SO (perilaku lama)", await jumlah_so(KAOS) - n4 == 2)

    # 8. kunci terlalu panjang -> 400
    s8, _, _ = await post(KAOS, UA, "x" * 201, badan(KAOS))
    cek("kunci > 200 karakter -> 400", s8 == 400, f"{s8}")

    # 9. flag fitur /api/permissions/me — dua jalur respons, dua tenant
    class _Eng:
        async def get_effective_permissions(self, u, t):
            return {"role_code": "OWNER", "effective_permissions": {}}

    class _EngMati:
        async def get_effective_permissions(self, u, t):
            raise RuntimeError("PolicyEngine not initialized")

    import app.services.policy_engine_client as pec

    async def _peran(conn, u, t):
        return "OWNER"

    tm.resolve_business_role = _peran
    harap = {GRAP: ["conversational_form_so", "conversational_workspace_so"], KAOS: []}
    for jalur, eng in (("PolicyEngine", _Eng()), ("cadangan", _EngMati())):
        pec.get_policy_engine = lambda e=eng: e
        for t in (GRAP, KAOS):
            out = await tm.get_my_permissions(_Req(t, UA))
            cek(f"/me {jalur} {t} features", out.get("features") == harap[t] and out.get("success") is True,
                f"{out.get('features')}")

    # 10. SO asli sudah dihapus -> 409 tetap, order_id/order_number null (tanpa tautan mati)
    _id_b = (b5.get("data") or {}).get("id")
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("DELETE FROM sales_order_items WHERE sales_order_id = $1::uuid", _id_b)
            await c.execute("DELETE FROM sales_orders WHERE id = $1::uuid", _id_b)
    s11, b11, _ = await post(KAOS, UB, "k-satu", badan(KAOS, harga=66666))
    cek("409 sesudah SO asli dihapus -> id & nomor null",
        s11 == 409 and _d(b11).get("code") == "IDEMPOTENCY_KEY_REUSED"
        and _d(b11).get("order_id") is None and _d(b11).get("order_number") is None, f"{_d(b11)}")

    await pool.close()
    return 0 if all(hasil) else 1


rc = asyncio.run(main())
print(f"RINGKAS: {sum(hasil)}/{len(hasil)} lulus, mode={MODE}")
sys.exit(rc)
