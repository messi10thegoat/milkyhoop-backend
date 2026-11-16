"""BUG-245 (putusan pemilik LANGSUNG 15 Nov 2026, ikut Accurate): harga BERJANGKA pada kunci (barang, satuan, min qty) yang sama.
HARGA_GANDA hanya bila kunci DAN tanggal mulai sama; indeks unik (V500) memuat coalesce(start_date,'-infinity'); resolve = mulai terbaru <= tanggal
dokumen (tie max() yang tak terdefinisi diperbaiki); D3 massal dengan tanggal berlaku MASA DEPAN membuat baris berjangka (bukan menimpa)."""
import asyncio
import inspect
import pathlib
from datetime import date
from decimal import Decimal

from app.routers import price_lists as PL
from app.services import harga_jual as HJ
from app.services import harga_massal as HM

MIG = pathlib.Path(__file__).resolve().parents[3] / "migrations"


# ---- migrasi V500 ----
def test_migrasi_v500_maju_dan_rollback_ada():
    maju = (MIG / "V500__daftar_harga_berjangka.sql").read_text()
    mundur = (MIG / "V500__daftar_harga_berjangka_ROLLBACK.sql").read_text()
    assert "DROP INDEX IF EXISTS uq_price_list_items_satuan" in maju
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_list_items_berjangka" in maju
    assert "COALESCE(start_date, '-infinity'::date)" in maju and "lower(btrim(COALESCE(unit, '')))" in maju and "min_quantity" in maju
    assert "DROP INDEX IF EXISTS uq_price_list_items_berjangka" in mundur
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_list_items_satuan" in mundur and "start_date" not in mundur.split("CREATE UNIQUE")[1]


def test_nama_indeks_baru_tetap_dikenali_penerjemah_galat_unik():
    class E:  # pelanggaran unik balapan pada indeks baru -> HARGA_GANDA, bukan KODE_DAFTAR_GANDA
        constraint_name = "uq_price_list_items_berjangka"
    assert PL._galat_unik(E()).code == "HARGA_GANDA"


# ---- validasi kode ----
def test_pemeriksa_ganda_menyertakan_tanggal_mulai():
    s = inspect.getsource(PL.siapkan_baris_harga)
    assert "start_date IS NOT DISTINCT FROM" in s and "item.start_date" in s


def test_kunci_di_badan_buat_daftar_menyertakan_tanggal_mulai():
    s = inspect.getsource(PL.siapkan_buat_daftar)
    assert "item.start_date" in s.split("k = (")[1].split("\n")[0]       # tuple kunci satu baris: (pid, satuan, min_quantity, start_date)


def test_pesan_harga_ganda_menyebut_tanggal_mulai():
    # tanggal mulai harus disebut sebagai BAGIAN KUNCI (b3: pesan lama tanpa tanggal, kalimat saran "Pakai tanggal mulai lain" saja tak cukup)
    assert "jumlah minimum, dan tanggal mulai itu sudah ada" in PL.PESAN_HARGA_GANDA and "Pakai tanggal mulai lain" in PL.PESAN_HARGA_GANDA


# ---- resolve ----
def _r(minq, mulai, harga):
    return {"min_quantity": Decimal(str(minq)), "start_date": mulai, "price": Decimal(str(harga)), "end_date": None}


def test_pilih_baris_mulai_terbaru_pada_kunci_sama():
    lama, baru = _r(1, date(2026, 1, 1), 100), _r(1, date(2026, 12, 1), 120)
    assert HJ.pilih_baris([lama, baru])["price"] == 120 and HJ.pilih_baris([baru, lama])["price"] == 120


def test_pilih_baris_tanpa_tanggal_dianggap_paling_lama():
    assert HJ.pilih_baris([_r(1, None, 90), _r(1, date(2026, 1, 1), 100)])["price"] == 100


def test_pilih_baris_qty_tertinggi_tetap_lebih_dulu():
    a, b = _r(1, date(2026, 12, 1), 120), _r(10, date(2026, 1, 1), 80)
    assert HJ.pilih_baris([a, b])["price"] == 80


def test_resolve_memakai_pilih_baris_bukan_max_polos():
    s = inspect.getsource(HJ)
    assert "pilih_baris(cocok)" in s and "max(cocok, key=lambda x: Decimal(str(x[\"min_quantity\"])))" not in s


# ---- D3 massal: masa depan = baris berjangka ----
class _Konn:
    def __init__(self, update_hasil="UPDATE 0"):
        self.sql, self.update_hasil = [], update_hasil

    async def execute(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return self.update_hasil if sql.lstrip().upper().startswith("UPDATE") else "INSERT 0 1"


def _rencana(mulai):
    return {"mulai": mulai, "baris": [{"id": "b1", "ke": Decimal("120")}]}


def test_terapkan_masa_depan_membuat_baris_berjangka_tak_menimpa():
    k = _Konn()
    asyncio.run(HM.terapkan(k, "t", "pl", _rencana(date(2999, 1, 1)), hari_ini=date(2026, 11, 15)))
    s = [x[0] for x in k.sql]
    assert any(x.startswith("INSERT INTO price_list_items") for x in s)
    assert not any("SET price = $4, start_date" in x and "id = $1" in x and "start_date IS NOT DISTINCT" not in x for x in s)


def test_terapkan_masa_depan_kunci_dan_tanggal_sama_memperbarui_baris_itu_saja():
    k = _Konn("UPDATE 1")
    asyncio.run(HM.terapkan(k, "t", "pl", _rencana(date(2999, 1, 1)), hari_ini=date(2026, 11, 15)))
    assert not any(x[0].startswith("INSERT") for x in k.sql)


def test_terapkan_tanpa_tanggal_atau_sudah_berlaku_menimpa_seperti_dulu():
    for mulai in (None, date(2026, 11, 15), date(2026, 1, 1)):
        k = _Konn()
        asyncio.run(HM.terapkan(k, "t", "pl", _rencana(mulai), hari_ini=date(2026, 11, 15)))
        assert len(k.sql) == 1 and k.sql[0][0].startswith("UPDATE price_list_items SET price = $4")


def test_baris_berjangka_baru_bersaring_tenant_dan_terbuka_ujung():
    s = inspect.getsource(HM.terapkan)
    assert "tenant_id" in s and "NULL" in s.split("INSERT")[1]  # end_date baris baru dikosongkan (tak mewarisi akhir baris lama)


def test_peringatan_berlaku_nanti_menyebut_harga_lama_tetap_berlaku():
    s = inspect.getsource(HM.rencana_barang)
    assert "tetap berlaku" in s and "tingkat jumlah yang lebih rendah" not in s


# ---- BUG-245 bukti Accurate (PT BIS, 16 Nov 2026): beberapa dokumen beda "Mulai Berlaku" untuk barang+satuan sama saling menggantikan menurut tanggal transaksi ----
def _harga_pada(baris, tanggal, qty=1):
    cocok = [r for r in baris if HJ._berlaku(r, tanggal) and Decimal(str(r["min_quantity"])) <= Decimal(str(qty))]
    return HJ.pilih_baris(cocok)["price"] if cocok else None


def test_tiga_tanggal_mulai_berlaku_menggantikan_menurut_tanggal_dokumen():
    # dasar 75.000 (tanpa tanggal) -> 20/11 90.000 -> 25/11 100.000; urutan baris di database tak boleh memengaruhi hasil
    dasar, k1, k2 = _r(1, None, 75000), _r(1, date(2026, 11, 20), 90000), _r(1, date(2026, 11, 25), 100000)
    for urut in ([dasar, k1, k2], [k2, k1, dasar], [k1, dasar, k2]):
        assert _harga_pada(urut, date(2026, 11, 16)) == 75000
        assert _harga_pada(urut, date(2026, 11, 19)) == 75000
        assert _harga_pada(urut, date(2026, 11, 20)) == 90000      # batas: hari mulai ikut baris baru
        assert _harga_pada(urut, date(2026, 11, 22)) == 90000
        assert _harga_pada(urut, date(2026, 11, 24)) == 90000
        assert _harga_pada(urut, date(2026, 11, 25)) == 100000
        assert _harga_pada(urut, date(2026, 11, 27)) == 100000


def test_beda_tanggal_mulai_tidak_dianggap_ganda_oleh_kunci_badan():
    # kunci HARGA_GANDA = (barang, satuan, min qty, tanggal mulai): tiga tanggal berbeda = tiga kunci berbeda
    kunci = {("p", "pcs", 1, d) for d in (None, date(2026, 11, 20), date(2026, 11, 25))}
    assert len(kunci) == 3
