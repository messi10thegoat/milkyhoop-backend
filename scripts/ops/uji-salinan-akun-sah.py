"""Akun sah SATU penentu uji DB di SALINAN (bukan prod): handler ASLI GET /items/categories/akun-pilihan + GET /items/accounts/<jenis> + penulis rencana_akun atas CoA klon, tenant kaos + nyawit. Hanya BACA.
A tiap jenis: daftar tak memuat header/nonaktif; himpunan id == hitungan SQL independen (aktif, bukan header, tipe sesuai jenis)
B tiap akun daftar lolos rencana_akun (penulis) dan tiap akun aktif-bukan-header yang TIDAK di daftar ditolak penulis (uji silang)
C GET /items/accounts/<jenis> (form Barang) == akun-pilihan (form Kategori); hpp hanya COGS"""
import asyncio
import sys

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import items as IT  # noqa: E402
from app.services import kategori_akun as KAK  # noqa: E402

TENANT = ("pt-kita-nyawit-bersama", "kaos-biru-konveksi")
ENDP = {"penjualan": IT.list_sales_accounts, "persediaan": IT.list_inventory_accounts, "hpp": IT.list_cogs_accounts, "pembelian": IT.list_purchase_accounts}
HASIL = []


def cek(nama, ok, info=""):
    HASIL.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + nama + (f" -- {info}" if info else ""), flush=True)


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "uji-salinan", "username": "uji-salinan", "role": "OWNER"}
    return r


async def main():
    conn = await IT.get_db_connection()
    for tid in TENANT:
        coa = {str(r["id"]): dict(r) for r in await conn.fetch(
            "SELECT id, account_code, name, account_type, is_active, is_header FROM chart_of_accounts WHERE tenant_id = $1", tid)}
        cek(f"{tid} CoA terbaca", len(coa) > 0, f"{len(coa)} akun")
        kat = {"id": "c1", "name": "K", **{f"akun_{j}": None for j in KAK.JENIS}}
        for j in KAK.JENIS:
            r = await IT.akun_pilihan_kategori(req(tid), jenis=j)
            ids = [a["id"] for a in r["accounts"]]
            tak_boleh = [i for i in ids if coa[i]["is_header"] or not coa[i]["is_active"]]
            cek(f"A {tid} {j}: tanpa header/nonaktif", not tak_boleh, f"{len(ids)} akun")
            sql_ids = {str(x["id"]) for x in await conn.fetch(
                "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND is_active = true AND NOT COALESCE(is_header, false) AND (" + (
                    "account_type IN ('REVENUE','OTHER_INCOME')" if j == "penjualan" else
                    "account_type = 'COGS'" if j == "hpp" else
                    "(account_type = 'ASSET' AND (name ILIKE '%persediaan%' OR name ILIKE '%inventory%'))" if j == "persediaan" else
                    "account_type IN ('EXPENSE','COGS') OR (account_type = 'ASSET' AND (name ILIKE '%persediaan%' OR name ILIKE '%inventory%'))") + ")", tid)}
            cek(f"A {tid} {j}: himpunan == SQL independen", set(ids) == sql_ids, f"{len(ids)} vs {len(sql_ids)}")
            salah = []
            for i, a in coa.items():
                ok = KAK.rencana_akun(kat, {j: i}, coa)["ok"]
                if ok != (i in ids):
                    salah.append(a["account_code"])
            cek(f"B {tid} {j}: daftar ≡ penulis untuk SEMUA {len(coa)} akun", not salah, ",".join(salah[:5]))
            f = await ENDP[j](req(tid))
            cek(f"C {tid} {j}: /items/accounts == akun-pilihan", [a.id for a in f.accounts] == ids, f"{len(f.accounts)} vs {len(ids)}")
            if j == "hpp":
                cek(f"C {tid} hpp hanya COGS", all(coa[i]["account_type"] == "COGS" for i in ids))
    await conn.close()
    ok = all(HASIL)
    print(f"HASIL: {sum(HASIL)}/{len(HASIL)} PASS", flush=True)
    sys.exit(0 if ok else 1)


asyncio.run(main())
