"""BUG-337: GET /api/items/{id} += harga_daftar[] (baca-saja, aditif): per DH aktif yang punya baris barang itu; semua baris; urut stabil; berlaku_hari_ini = _berlaku."""
import asyncio
from datetime import date

from app.services import harga_daftar as HD

H = date(2026, 11, 22)


class Konek:
    def __init__(self, rows):
        self.rows, self.sql, self.args, self.n = rows, None, None, 0

    async def fetch(self, sql, *args):
        self.sql, self.args, self.n = sql, args, self.n + 1
        return self.rows


def b(pl, nama, kode, bawaan, unit, q, harga, mulai=None, sampai=None, pl_start=None, pl_end=None, aktif=True, minimum=None, diskon=None):
    return {"pl_id": pl, "pl_name": nama, "pl_code": kode, "is_default": bawaan, "is_active": aktif, "pl_start": pl_start, "pl_end": pl_end,
            "unit": unit, "price": harga, "min_quantity": q, "start_date": mulai, "end_date": sampai, "min_price": minimum, "discount_percent": diskon}


def jalan(rows):
    k = Konek(rows)
    return k, asyncio.run(HD.harga_daftar_barang(k, "t-1", "it-1", "Pcs", H))


def test_kosong_default_list():
    k, h = jalan([])
    assert h == [] and k.n == 1


def test_dua_daftar_baris_tingkat_dan_berjangka_semua_dikirim():
    k, h = jalan([
        b("a", "Bawaan", "DH0", True, None, 1, 10000),
        b("a", "Bawaan", "DH0", True, None, 10, 9000),
        b("a", "Bawaan", "DH0", True, "Dus", 1, 100000, mulai=date(2026, 12, 1)),
        b("b", "Grosir", "DH1", False, None, 1, 8000, sampai=date(2026, 11, 1)),
    ])
    assert k.n == 1 and [d["price_list_id"] for d in h] == ["a", "b"]
    a = h[0]
    assert (a["nama"], a["kode"], a["is_default"], a["aktif"], a["status"]) == ("Bawaan", "DH0", True, True, "aktif")
    assert [(r["unit_text"], r["min_quantity"], r["harga"], r["berlaku_hari_ini"]) for r in a["baris"]] == [
        ("Pcs", 1.0, 10000, True), ("Pcs", 10.0, 9000, True), ("Dus", 1.0, 100000, False)]
    assert a["baris"][2]["berlaku_mulai"] == "2026-12-01" and a["baris"][2]["berlaku_sampai"] is None and a["baris"][0]["unit"] is None
    assert h[1]["baris"][0]["berlaku_hari_ini"] is False and h[1]["baris"][0]["berlaku_sampai"] == "2026-11-01"


def test_status_header_daftar_kedaluwarsa():
    _, h = jalan([b("c", "Lama", "DH2", False, None, 1, 5, pl_end=date(2026, 1, 1))])
    assert h[0]["status"] == "kedaluwarsa" and h[0]["aktif"] is True


def test_sql_satu_kueri_tenant_item_aktif_urut_stabil():
    k, _ = jalan([])
    s = " ".join(k.sql.split())
    assert k.args == ("t-1", "it-1") and "p.tenant_id = $1" in s and "pli.item_id = $2" in s and "p.is_active" in s and "COALESCE(pli.is_active, true)" in s
    assert "ORDER BY p.is_default DESC, p.priority, p.name, p.id, pli.unit NULLS FIRST, pli.min_quantity, pli.start_date NULLS FIRST, pli.id" in s


def test_bug343_minimum_dan_diskon_per_baris_angka_dan_teks_none_tak_diatur():
    from decimal import Decimal
    _, h = jalan([
        b("a", "Bawaan", "DH0", True, None, 1, 10000, minimum=Decimal("8000.00"), diskon=Decimal("12.5")),
        b("a", "Bawaan", "DH0", True, None, 10, 9000),
        b("a", "Bawaan", "DH0", True, "Dus", 1, 100000, minimum=Decimal("0"), diskon=Decimal("0")),
    ])
    r = h[0]["baris"]
    assert (r[0]["min_price"], r[0]["min_price_text"], r[0]["diskon_persen"], r[0]["diskon_text"]) == (Decimal("8000.00"), "Rp 8.000", 12.5, "12.5%")
    assert (r[1]["min_price"], r[1]["min_price_text"], r[1]["diskon_persen"], r[1]["diskon_text"]) == (None, None, None, None)
    assert (r[2]["min_price"], r[2]["diskon_persen"], r[2]["diskon_text"]) == (Decimal("0"), 0.0, "0%")


def test_bug343_sql_membaca_min_price_dan_discount_percent():
    k, _ = jalan([])
    s = " ".join(k.sql.split())
    assert "pli.min_price" in s and "pli.discount_percent" in s
