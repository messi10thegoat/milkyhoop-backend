"""BUG-255 (17 Nov 2026; putusan MASTER): pasang DAFTAR HARGA ke banyak pelanggan -- jalur sendiri pratinjau == tulis (rencana() murni), tulis atomik (satu UPDATE, yang sudah sama dilewati),
aktivitas per pelanggan + satu audit, X-Idempotency-Key, izin customer U, hanya customers.price_list_id (tanpa harga/jurnal/dokumen)."""
import asyncio
import pathlib
import re
import uuid

import pytest
from fastapi import HTTPException

from app.services import dh_pelanggan_massal as DH

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
D1, D2 = str(uuid.uuid4()), str(uuid.uuid4())


def p(i, nama, daftar=None, aktif=True, nama_daftar=None):
    return {"id": uuid.UUID(int=i), "kode": f"C{i}", "nama": nama, "is_active": aktif, "price_list_id": daftar and uuid.UUID(daftar), "daftar_nama": nama_daftar}


def tgt(i=D1, nama="Grosir", aktif=True):
    return {"id": uuid.UUID(i), "name": nama, "code": "G", "is_active": aktif}


def test_rencana_ringkas_kelompok_lama_baris_dan_payload():
    rows = [p(1, "Andi", None), p(2, "Budi", D2, nama_daftar="Eceran"), p(3, "Cici", D1, nama_daftar="Grosir"), p(4, "Dedi", None, aktif=False)]
    r = DH.rencana(tgt(), True, rows, 4, False)
    assert r["ok"] and r["ringkas"] == {"dipilih": 4, "berubah": 3, "sama": 1, "tak_ditemukan": 0, "nonaktif": 1}
    assert r["target"] == {"id": D1, "nama": "Grosir", "status_text": "Aktif"}
    assert {g["nama"]: g["jumlah"] for g in r["kelompok_lama"]} == {"Daftar bawaan": 2, "Eceran": 1, "Grosir": 1} and r["kelompok_lama"][0]["jumlah"] == 2
    a, b, c, d = r["baris"]
    assert (a["dari"], a["ke"], a["berubah"]) == ({"id": None, "nama": "Daftar bawaan"}, {"id": D1, "nama": "Grosir"}, True) and c["berubah"] is False and d["nonaktif"] is True
    assert r["payload"] == {"price_list_id": D1, "customer_ids": [str(uuid.UUID(int=i)) for i in (1, 2, 3, 4)]} and any("1 pelanggan nonaktif" in x for x in r["peringatan"])


def test_rencana_kembali_ke_daftar_bawaan_target_null():
    r = DH.rencana(None, False, [p(1, "Andi", D1, nama_daftar="Grosir"), p(2, "Budi", None)], 2, False)
    assert r["ok"] and r["target"] == {"id": None, "nama": "Daftar bawaan", "status_text": "Aktif"} and r["ringkas"]["berubah"] == 1 and r["ringkas"]["sama"] == 1
    assert r["baris"][0]["ke"] == {"id": None, "nama": "Daftar bawaan"}


def test_rencana_blok_target_tanpa_pelanggan_dan_batas():
    assert [b["code"] for b in DH.rencana(None, True, [p(1, "A")], 1, False)["blocks"]] == ["DAFTAR_TIDAK_ADA"]
    assert [b["code"] for b in DH.rencana(tgt(aktif=False), True, [p(1, "A")], 1, False)["blocks"]] == ["DAFTAR_NONAKTIF"]
    assert [b["code"] for b in DH.rencana(tgt(), True, [], 0, False)["blocks"]] == ["TANPA_PELANGGAN"]
    r = DH.rencana(tgt(), True, [p(1, "A")], DH.BATAS_ID + 1, False)
    assert [b["code"] for b in r["blocks"]] == ["TERLALU_BANYAK"] and r["ok"] is False
    assert DH.rencana(tgt(), True, [p(1, "A")], DH.BATAS_ID + 1, True)["ok"] is True        # saringan boleh sampai 2000
    assert DH.rencana(tgt(), True, [p(1, "A")], DH.BATAS_SARINGAN + 1, True)["ok"] is False


def test_rencana_tak_ditemukan_dan_semua_sama_peringatan_tanpa_blok():
    r = DH.rencana(tgt(), True, [p(1, "A", D1, nama_daftar="Grosir")], 3, False)
    assert r["ringkas"]["tak_ditemukan"] == 2 and r["ok"] and any("sudah memakai 'Grosir'" in x for x in r["peringatan"]) and r["ringkas"]["berubah"] == 0
    assert DH.rencana(tgt(), True, [], 2, False)["ok"] is False                       # tak ada baris terukur = tak ok


def test_rencana_contoh_dibatasi_tapi_payload_memuat_semua():
    rows = [p(i, f"P{i}") for i in range(1, DH.CONTOH + 21)]
    r = DH.rencana(tgt(), True, rows, len(rows), False)
    assert len(r["baris"]) == DH.CONTOH and len(r["payload"]["customer_ids"]) == len(rows) and len(r["_semua"]) == len(rows)


class Konn:
    def __init__(self, dalam_tx=True, diubah=None):
        self.sql, self.dalam_tx, self.diubah = [], dalam_tx, diubah

    def is_in_transaction(self):
        return self.dalam_tx

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        assert sql == DH.SQL_UBAH
        return [{"id": uuid.UUID(i)} for i in (self.diubah if self.diubah is not None else a[1])]

    async def execute(self, sql, *a):
        self.sql.append((sql, a))
        return "OK"


def jalan(aw):
    return asyncio.run(aw)


def test_tulis_satu_update_aktivitas_per_yang_berubah_dan_satu_audit():
    rows = [p(1, "Andi", None), p(2, "Budi", D2, nama_daftar="Eceran"), p(3, "Cici", D1, nama_daftar="Grosir")]
    r = DH.rencana(tgt(), True, rows, 3, False)
    c = Konn()
    h = jalan(DH.tulis(c, {"tenant_id": "t1", "user_id": "u1"}, r, "Anton"))
    upd = [s for s in c.sql if s[0] == DH.SQL_UBAH]
    assert len(upd) == 1 and upd[0][1][0] == "t1" and upd[0][1][1] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))] and upd[0][1][2] == D1      # Cici (sudah sama) tak ikut
    akt = [a for s, a in c.sql if "INSERT INTO customer_activities" in s]
    assert [x[0] for x in akt] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]
    assert akt[0][2] == "Daftar harga diubah menjadi 'Grosir' (massal)" and (akt[0][3], akt[0][4]) == ("Daftar bawaan", "Grosir") and (akt[1][3], akt[1][4]) == ("Eceran", "Grosir")
    aud = [a for s, a in c.sql if "INSERT INTO audit_logs" in s]
    assert len(aud) == 1 and '"jumlah": 2' in aud[0][1] and '"ke": "Grosir"' in aud[0][1] and "CUSTOMER_PRICE_LIST_BULK" in [s for s, _ in c.sql if "INSERT INTO audit_logs" in s][0]
    assert h["jumlah_berubah"] == 2 and h["jumlah_sama"] == 1


def test_tulis_ke_daftar_bawaan_memakai_null_dan_balapan_yang_sudah_berubah_dilewati():
    r = DH.rencana(None, False, [p(1, "Andi", D1, nama_daftar="Grosir"), p(2, "Budi", D2, nama_daftar="Eceran")], 2, False)
    c = Konn(diubah=[str(uuid.UUID(int=1))])               # id 2 berubah oleh penulis lain di antara pratinjau dan tulis
    h = jalan(DH.tulis(c, {"tenant_id": "t1", "user_id": None}, r, None))
    assert [s for s in c.sql if s[0] == DH.SQL_UBAH][0][1][2] is None and h["jumlah_berubah"] == 1
    assert len([1 for s, _ in c.sql if "INSERT INTO customer_activities" in s]) == 1


def test_tulis_wajib_transaksi_dan_nol_perubahan_tanpa_update():
    r = DH.rencana(tgt(), True, [p(1, "A", D1, nama_daftar="Grosir")], 1, False)
    with pytest.raises(RuntimeError):
        jalan(DH.tulis(Konn(dalam_tx=False), {"tenant_id": "t1"}, r, None))
    c = Konn()
    h = jalan(DH.tulis(c, {"tenant_id": "t1"}, r, None))
    assert not [s for s in c.sql if s[0] == DH.SQL_UBAH] and h["jumlah_berubah"] == 0


def test_sql_berpagar_tenant_satu_update_hanya_kolom_price_list_id():
    assert "tenant_id = $1" in DH.SQL_UBAH and "price_list_id IS DISTINCT FROM $3" in DH.SQL_UBAH and DH.SQL_UBAH.startswith("UPDATE customers SET price_list_id = $3, updated_at = NOW()")
    assert "c.tenant_id = $1" in DH.SQL_PELANGGAN and "pl.tenant_id = c.tenant_id" in DH.SQL_PELANGGAN and "tenant_id = $2" in DH.SQL_TARGET
    assert not re.search(r"\b(INSERT|DELETE)\b", DH.SQL_UBAH + DH.SQL_PELANGGAN + DH.SQL_TARGET)


def test_rute_izin_urutan_dan_idempotensi():
    src = (APP / "routers" / "customers.py").read_text()
    assert '@router.post("/bulk/price-list/preview")' in src and '@router.post("/bulk/price-list")' in src
    assert src.index('@router.post("/bulk/price-list")') < src.index('@router.post("/{customer_id}/edit/preview")')
    isi = src.split("async def pasang_dh_massal")[1].split("@router.get")[0]
    assert 'idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "CUSTOMER_DH_BULK", "batch"' in isi and "pg_advisory_xact_lock" in isi and "_urai_dh_massal(conn" in isi
    assert "DHM.tulis(conn" in isi and '"code": "BULK_DITOLAK"' in isi and isi.index("_urai_dh_massal(conn") < isi.index("DHM.tulis(conn")        # validasi ULANG sebelum tulis
    pre = src.split("async def preview_dh_massal")[1].split("@router.post")[0]
    assert "conn.transaction(readonly=True)" in pre and "rollback" in pre and "DHM.tulis" not in pre                    # pratinjau READ ONLY
    assert '(r"^/api/customers/bulk/price-list(/preview)?$", ["POST"], "customer", "U")' in (APP / "middleware" / "permission_middleware.py").read_text()


# ---- penguraian badan di router (SATU penentu pratinjau == tulis)
class KonnUrai:
    def __init__(self, target=None, pelanggan=None, ids_saringan=None):
        self.target, self.pelanggan, self.ids_saringan, self.sql = target, pelanggan or [], ids_saringan or [], []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return self.target

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        if sql == DH.SQL_PELANGGAN:
            return [r for r in self.pelanggan if str(r["id"]) in a[1]]
        return [{"id": uuid.UUID(i)} for i in self.ids_saringan]


def urai(body, **kw):
    from app.routers import customers as CU
    k = KonnUrai(**kw)
    return jalan(CU._urai_dh_massal(k, "t1", body)), k


@pytest.mark.parametrize("body, field", [({"customer_ids": []}, "price_list_id"), ({"price_list_id": None}, "customer_ids"), ({"price_list_id": None, "customer_ids": [], "saringan": {}}, "customer_ids"),
                                          ({"price_list_id": "bukan-uuid", "customer_ids": []}, "price_list_id"), ({"price_list_id": None, "customer_ids": ["x"]}, "customer_ids")])
def test_urai_badan_tak_sah_422(body, field):
    with pytest.raises(HTTPException) as e:
        urai(body)
    assert e.value.status_code == 422 and e.value.detail["field"] == field


def test_urai_customer_ids_unik_berurutan_dan_target():
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    (r, t), k = urai({"price_list_id": D1, "customer_ids": [str(b), str(a), str(b)]}, target=tgt(), pelanggan=[p(1, "A"), p(2, "B")])
    assert r["ok"] and t["name"] == "Grosir" and r["ringkas"]["dipilih"] == 2 and r["payload"]["customer_ids"] == [str(a), str(b)]
    assert r["ringkas"]["tak_ditemukan"] == 0      # id ganda dibuang: 3 diminta -> 2 unik, bukan "1 tak ditemukan"
    assert [s for s, _ in k.sql][0] == DH.SQL_TARGET and k.sql[0][1] == (D1, "t1")


def test_urai_saringan_aktif_bawaan_dan_daftar_sekarang_dan_batas():
    (r, _), k = urai({"price_list_id": None, "saringan": {"search": "toko", "price_list_id_sekarang": "bawaan"}}, pelanggan=[p(1, "A")], ids_saringan=[str(uuid.UUID(int=1))])
    q = next(s for s, _ in k.sql if s.startswith("SELECT id FROM customers"))
    assert "price_list_id IS NULL" in q and f"LIMIT {DH.BATAS_SARINGAN + 1}" in q and "is_active" in q          # bawaan: hanya pelanggan aktif
    assert r["ok"] and r["ringkas"]["dipilih"] == 1 and r["target"]["nama"] == "Daftar bawaan"
    (_, _), k2 = urai({"price_list_id": None, "saringan": {"price_list_id_sekarang": D2, "is_active": None}}, ids_saringan=[])
    q2 = next(s for s, _ in k2.sql if s.startswith("SELECT id FROM customers"))
    assert "price_list_id = $" in q2 and "price_list_id IS NULL" not in q2
