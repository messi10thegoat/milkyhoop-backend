"""BUG-278: Ubah harga massal (D3) memakai SATU baris sumber per kunci = baris yang BERLAKU pada tanggal berlaku baru; deterministik; pratinjau == tulis."""
import asyncio
from datetime import date
from decimal import Decimal as D

from app.services import harga_massal as HM

HARI = date(2026, 11, 16)


def b(id, harga, qty=1, mulai=None, akhir=None, unit=None):
    return {"id": id, "unit": unit, "dasar": "Pcs", "price": D(harga), "min_quantity": D(qty), "start_date": mulai, "end_date": akhir, "min_price": None}


def rencana(baris, mulai=None, nilai=10, hari=HARI):
    return HM.rencana_barang("Minyak", baris, "persen", D(nilai), mulai, hari)


def test_dua_baris_sumber_satu_kunci_berjangka_masa_depan_memakai_baris_yang_berlaku_pada_tanggal_baru():
    A, B = b("a", 10000), b("b", 11000, mulai=date(2026, 11, 20))
    for urut in ([A, B], [B, A]):                                              # urutan baca TAK memengaruhi hasil
        r = rencana(urut, mulai=date(2026, 12, 1))
        assert r["ok"] and r["rencana"]["jumlah_baris"] == 1
        x = r["rencana"]["baris"][0]
        assert x["id"] == "b" and x["dari"] == D("11000") and x["ke"] == D("12100.00")          # dari baris B (berlaku pada 1/12), bukan A


def test_tanggal_baru_sebelum_baris_berjangka_memakai_baris_dasar():
    A, B = b("a", 10000), b("b", 11000, mulai=date(2026, 12, 10))
    r = rencana([A, B], mulai=date(2026, 12, 1))
    assert [x["id"] for x in r["rencana"]["baris"]] == ["a"] and r["rencana"]["baris"][0]["ke"] == D("11000.00")        # B baru mulai 10/12: belum berlaku pada 1/12


def test_perubahan_langsung_hanya_baris_yang_berlaku_hari_ini_baris_masa_depan_tak_disentuh():
    A, B, C = b("a", 10000), b("b", 11000, mulai=date(2026, 11, 10)), b("c", 12000, mulai=date(2026, 12, 25))
    r = rencana([A, B, C])
    assert [x["id"] for x in r["rencana"]["baris"]] == ["b"] and r["rencana"]["baris"][0]["ke"] == D("12100.00")         # hari ini: B berlaku; A tergantikan, C belum


def test_baris_berakhir_tak_jadi_sumber_dan_semua_tak_berlaku_diblok_berkalimat():
    r = rencana([b("a", 10000, akhir=date(2026, 11, 1))])
    assert not r["ok"] and r["blocks"][0]["code"] == "DH_TAK_BERLAKU_PADA_TANGGAL" and "16" in r["blocks"][0]["message"] and "Minyak" in r["blocks"][0]["message"]
    r2 = rencana([b("a", 10000, mulai=date(2027, 1, 1))])
    assert not r2["ok"] and r2["blocks"][0]["code"] == "DH_TAK_BERLAKU_PADA_TANGGAL"


def test_satu_baris_per_kunci_satuan_dan_qty_berbeda_tetap_terpisah_dan_berurutan():
    rows = [b("q10", 9000, qty=10), b("dus", 50000, unit="Dus"), b("q1", 10000), b("q1b", 10500, mulai=date(2026, 11, 20))]
    r = rencana(rows, mulai=date(2026, 12, 1))["rencana"]["baris"]
    assert [x["id"] for x in r] == ["q1b", "q10", "dus"]
    assert {x["id"]: x["dari"] for x in r} == {"q1b": D("10500"), "q10": D("9000"), "dus": D("50000")}


def test_seri_tanggal_sama_deterministik_id_terbesar_dan_kunci_satuan_tanpa_beda_huruf():
    rows = [b("a1", 100, unit=" dus "), b("a2", 200, unit="DUS")]               # kunci sama (satuan dinormalisasi) -> satu sumber, seri = id terbesar
    r = HM.baris_efektif(rows, HARI)
    assert [x["id"] for x in r] == ["a2"]
    assert [x["id"] for x in HM.baris_efektif(list(reversed(rows)), HARI)] == ["a2"]


def test_tanpa_tanggal_dianggap_paling_lama_dan_baris_tanpa_end_date_kunci_dari_ukur():
    r = HM.baris_efektif([{"id": "x", "unit": None, "price": 1, "min_quantity": 1, "start_date": date(2026, 1, 1)}, {"id": "y", "unit": None, "price": 2, "min_quantity": 1}], HARI)
    assert [x["id"] for x in r] == ["x"]                                         # end_date tak ada di baris = tanpa akhir (get)


def test_penulis_tak_menumbuk_kunci_karena_rencana_satu_baris_per_kunci():
    class C:
        def __init__(self):
            self.sql = []

        async def execute(self, sql, *a):
            self.sql.append((sql, a))
            return "UPDATE 0"
    rows = [b("a", 10000), b("b", 11000, mulai=date(2026, 11, 20))]
    r = rencana(rows, mulai=date(2026, 12, 1))["rencana"]
    c = C()
    asyncio.run(HM.terapkan(c, "t", "pl", r, hari_ini=HARI))
    ins = [s for s, _ in c.sql if s.lstrip().startswith("INSERT INTO price_list_items")]
    assert len(ins) == 1 and c.sql[1][1][0] == "b" and c.sql[1][1][3] == D("12100.00")                  # SATU baris baru T2, bersumber B


def test_ukur_rencana_membaca_end_date_dan_urutan_deterministik():
    import inspect
    s = inspect.getsource(HM.ukur_rencana)
    assert "pli.end_date" in s and "pli.start_date NULLS FIRST, pli.id" in s
