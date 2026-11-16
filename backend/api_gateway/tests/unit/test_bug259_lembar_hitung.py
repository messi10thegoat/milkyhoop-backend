"""BUG-259 (16 Nov 2026): Lembar Penghitungan Stok per gudang (GET /api/warehouses/{id}/count-sheet + /pdf). Stok BUKU per tanggal dari inventory_ledger,
barang dilacak aktif, bawaan hanya yang pernah bergerak di gudang itu, kolom Fisik kosong di PDF, hanya baca, izin baca WAREHOUSE."""
import asyncio
import pathlib
import re
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services import lembar_hitung_stok as LH

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
GD = {"id": "g-1", "nama": "Gudang Utama"}


def r(kode, nama, stok, satuan="pcs", kat=None, merek=None, i=1):
    return {"id": f"p-{i}", "kode": kode, "nama": nama, "kategori": kat, "merek": merek, "satuan": satuan, "stok_buku": D(str(stok))}


class Konn:
    def __init__(self, rows):
        self.rows, self.args = rows, None

    async def fetch(self, sql, *a):
        self.args = (sql, a)
        return self.rows


def jalan(rows, **kw):
    k = Konn(rows)
    return asyncio.run(LH.muat_lembar(k, "t1", GD, date(2026, 11, 16), dibuat_pada=datetime(2026, 11, 16, 22, 0, 0), **kw)), k


def test_baris_teks_minus_dan_total():
    l, _ = jalan([r("A-1", "Aqua 600", 172, kat="Minuman", merek="Aqua", i=1), r("A-2", "Teh", "-3", "kg", i=2), r("A-3", "Gula", "1500.5", "kg", i=3), r("A-4", "Habis", 0, i=4)])
    assert [b["stok_buku_text"] for b in l["baris"]] == ["172 pcs", "-3 kg", "1.500,5 kg", "0 pcs"]
    assert [b["no"] for b in l["baris"]] == [1, 2, 3, 4] and l["total_baris"] == 4 and l["jumlah_minus"] == 1
    assert l["baris"][1]["minus"] is True and l["baris"][0]["minus"] is False and l["baris"][0]["merek"] == "Aqua"
    assert l["gudang"] == {"id": "g-1", "nama": "Gudang Utama"} and l["per_tanggal"] == "2026-11-16" and l["dibuat_pada"] == "2026-11-16T22:00:00"


def test_saringan_dan_parameter_ke_sql():
    _, k = jalan([], kategori=" Minuman ", merek="  ", sertakan_semua=True)
    sql, a = k.args
    assert a == ("t1", "g-1", date(2026, 11, 16), "Minuman", None, True, LH.BATAS_BARIS + 1)
    _, k = jalan([])
    assert k.args[1][3:6] == (None, None, False)


def test_terlalu_banyak_barang_ditolak():
    rows = [r(f"K{i}", f"B{i}", 1, i=i) for i in range(LH.BATAS_BARIS + 1)]
    with pytest.raises(LH.LembarTerlaluBesar):
        jalan(rows)


def test_sql_ledger_per_tanggal_gudang_itu_barang_dilacak_aktif_berpagar_tenant_hanya_baca():
    q = LH.SQL_LEMBAR
    assert "inventory_ledger il" in q and "il.warehouse_id = $2" in q and "il.movement_date <= $3" in q
    assert "il.tenant_id = $1" in q and "p.tenant_id = $1" in q and "p.deleted_at IS NULL" in q
    assert "p.item_type = 'goods' AND p.track_inventory = true AND p.status = 'active'" in q
    assert "HAVING $6::boolean OR COUNT(il.id) FILTER (WHERE il.movement_date <= $3) > 0" in q   # bawaan: pernah bergerak sampai tanggal itu
    assert "warehouse_stock" not in q and not re.search(r"\b(INSERT|UPDATE|DELETE)\b", q, re.I)   # bukan cache; hanya baca
    assert "ORDER BY p.item_code NULLS LAST, p.nama_produk, p.id" in q and "LIMIT $7" in q


def _html(lembar):
    env = Environment(loader=FileSystemLoader(str(APP / "templates" / "pdf")), autoescape=True)
    env.filters.update({"date_id": lambda v: str(v), "logo_rapat": lambda v: v, "currency": lambda v: str(v)})
    return env.get_template("lembar_hitung_stok.html").render(lembar=lembar, company={"name": "Toko Maju", "address": None, "phone": None, "npwp": None, "logo_base64": None},
                                                               generated_at=datetime(2026, 11, 16))


def test_pdf_html_kolom_fisik_kosong_kepala_dan_tanda_tangan():
    l, _ = jalan([r("A-1", "Aqua 600", 172, i=1), r("A-2", "Teh", "-3", "kg", i=2)], kategori="Minuman")
    h = _html(l)
    assert "Lembar Penghitungan Stok" in h and "Gudang Utama" in h and "2026-11-16" in h and "Minuman" in h
    assert "<th class=\"lh-fisik\">Fisik</th>" in h and h.count("class=\"lh-fisik\">&nbsp;</td>") == 2    # kolom Fisik KOSONG per baris
    assert "172 pcs" in h and "-3 kg" in h and "stok minus: hitung ulang" in h and "Dihitung oleh" in h and "Diperiksa oleh" in h
    assert "Tidak ada barang untuk dihitung" not in h
    kosong, _ = jalan([])
    assert "Tidak ada barang untuk dihitung di gudang ini" in _html(kosong)


def test_rute_json_pdf_izin_dan_penjaga_gudang_sistem():
    src = (APP / "routers" / "warehouses.py").read_text()
    assert '@router.get("/{warehouse_id}/count-sheet")' in src and '@router.get("/{warehouse_id}/count-sheet/pdf")' in src
    assert "AND NOT is_system" in src.split("async def _muat_lembar_hitung")[1].split("@router.get")[0]   # gudang Transit = 404
    assert "LH.muat_lembar(" in src and "render_lembar_hitung_stok(" in src and "StreamingResponse(" in src
    izin = (APP / "middleware" / "permission_middleware.py").read_text()
    assert '(r"^/api/warehouses", ["GET"], "warehouse", "R")' in izin   # prefiks: count-sheet ikut izin baca gudang
    assert "def render_lembar_hitung_stok" in (APP / "services" / "pdf_service.py").read_text()
