"""TERBIT-PRATINJAU-1 (10 Nov 2026, TESTER INV-2611-0022 nyawit): (1) preview-journal draf tersimpan yang DITOLAK penulis -> blok berkode, NOL
baris jurnal (dulu jurnal Rp 0 + "Pelanggan belum dipilih" dari badan kosong); (2) stok gudang = SATU penentu (stok_baris) untuk penulis
faktur, pratinjau terbit, dan SJ dari pesanan -- kalimat sama, kolom per pemanggil dipertahankan."""
import asyncio
import inspect
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import sales_invoices as SI
from app.services import faktur_terbit_rencana as FTR
from app.services import satuan_dasar as SD
from app.services import sj_so
from app.services import stok_baris as SB
from app.services import teks_galat as tg

T = "pt-kita-nyawit-bersama"
INV = "11111111-1111-1111-1111-111111111111"
STOK = "Stok Minyak Goreng Curah di Gudang Utama hanya 0 liter, diminta 5 jerigen (= 90 liter)."


def req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": "00000000-0000-0000-0000-0000000000a1"}))


class KolamTerlarang:  # preview_journal mengambil pool di awal; jalur perkiraan dari badan = acquire() -> wajib tak terjadi untuk draf tersimpan
    def acquire(self):
        raise AssertionError("jalur perkiraan dari badan TAK boleh dipakai untuk draf tersimpan")


def pratinjau(monkeypatch, sim, body):
    async def kolam():
        return KolamTerlarang()

    async def palsu(pool, ctx, ref):
        if isinstance(sim, Exception):
            raise sim
        return sim
    monkeypatch.setattr(SI, "_pratinjau_dari_posting", palsu)
    monkeypatch.setattr(SI, "get_pool", kolam)
    return asyncio.run(SI.preview_journal(req(), body))


def test_draf_ditolak_stok_jadi_blok_tanpa_jurnal(monkeypatch):
    with pytest.raises(AssertionError):  # kontrol: tanpa invoice_id jalur perkiraan memang dipakai
        pratinjau(monkeypatch, None, {"items": [{"item_id": "x"}]})
    out = pratinjau(monkeypatch, HTTPException(409, STOK), {"invoice_id": INV})
    assert out["ok"] is False and out["journal_lines"] == [] and out["warnings"] == []
    assert out["blocks"] == [{"code": SB.KODE_STOK_KURANG, "message": STOK, "status": 409}]
    assert "Pelanggan belum dipilih" not in repr(out)


def test_draf_ditolak_galat_berkode_kode_dipertahankan(monkeypatch):
    out = pratinjau(monkeypatch, HTTPException(400, {"code": "GUDANG_WAJIB", "message": "Pilih gudang dulu."}), {"id": INV})
    assert out["blocks"] == [{"code": "GUDANG_WAJIB", "message": "Pilih gudang dulu.", "status": 400}] and out["journal_lines"] == []


def test_galat_tak_terduga_blok_jujur(monkeypatch):
    out = pratinjau(monkeypatch, RuntimeError("boom"), {"invoice_id": INV})
    assert out["ok"] is False and out["blocks"][0]["code"] == "PRATINJAU_GAGAL" and out["journal_lines"] == []
    assert "boom" not in repr(out)


def test_bukan_draf_tanpa_badan_blok(monkeypatch):
    out = pratinjau(monkeypatch, None, {"invoice_id": INV})
    assert out["blocks"][0]["code"] == "SI_BUKAN_DRAF" and out["journal_lines"] == []


def test_draf_lolos_bentuk_lama_plus_ok(monkeypatch):
    sim = {"journal_lines": [{"account_code": "1-10400", "debit": 1890000.0, "credit": 0.0}], "warnings": []}
    out = pratinjau(monkeypatch, sim, {"invoice_id": INV})
    assert out == {"ok": True, "blocks": [], **sim}


@pytest.mark.parametrize("tersedia,qd,jumlah,unit,harap", [
    (Decimal("0"), {"qty_dasar": Decimal("90"), "faktor": Decimal("18"), "satuan_dasar": "liter"}, Decimal("5"), "jerigen", STOK),
    (Decimal("2"), {"qty_dasar": Decimal("3"), "faktor": SD.SATU, "satuan_dasar": None}, Decimal("3"), "pcs",
     "Stok Minyak Goreng Curah di Gudang Utama hanya 2 pcs, diminta 3 pcs."),
    (Decimal("2.5"), {"qty_dasar": Decimal("3"), "faktor": SD.SATU, "satuan_dasar": None}, Decimal("3"), None,
     "Stok Minyak Goreng Curah di Gudang Utama hanya 2,5, diminta 3."),
])
def test_kalimat_satu(tersedia, qd, jumlah, unit, harap):
    b = SB.blok_kurang("Minyak Goreng Curah", "Gudang Utama", tersedia, qd, jumlah, unit)
    assert b == {"code": SB.KODE_STOK_KURANG, "message": harap, "status": 409}
    assert SB.blok_kurang("X", "G", qd["qty_dasar"], qd, jumlah, unit) is None  # pas = cukup
    assert SB.blok_kurang("X", "G", tersedia, qd, jumlah, unit, kode="SJ_SO_STOK_TIDAK_CUKUP")["code"] == "SJ_SO_STOK_TIDAK_CUKUP"


class Kon:
    def __init__(self, nilai):
        self.nilai, self.sql = nilai, []

    async def fetchval(self, sql, *a):
        self.sql.append((sql, a))
        return self.nilai


@pytest.mark.parametrize("kolom", SB.KOLOM_STOK)
def test_tersedia_gudang_bertenant_kolom(kolom):
    k = Kon(0)
    assert asyncio.run(SB.tersedia_gudang(k, T, "g", "i", kolom)) == Decimal("0")
    sql, a = k.sql[0]
    assert sql.startswith("SELECT COALESCE((SELECT")  # tak ada baris = 0 (perilaku lama), di SQL
    assert f"SELECT {kolom} FROM warehouse_stock WHERE tenant_id = $1 AND warehouse_id = $2 AND item_id = $3" in sql and a == (T, "g", "i")
    assert "FOR UPDATE" not in sql
    with pytest.raises(ValueError):
        asyncio.run(SB.tersedia_gudang(k, T, "g", "i", "quantity; DROP"))


def kode(fn_atau_modul) -> str:
    """Sumber TANPA komentar baris (komentar lama boleh menyebut kalimat/tabel lama)."""
    return "\n".join(re.sub(r"\s+#.*$", "", b) for b in inspect.getsource(fn_atau_modul).splitlines() if not b.lstrip().startswith("#"))


def test_satu_penentu_di_tiga_pemanggil():
    tulis = kode(SI._execute_fulfillment)
    ro = kode(FTR.rencana_terbit_ro)
    sj = kode(sj_so)
    assert 'SB.tersedia_gudang(conn, tenant_id, warehouse_id, product_id, "quantity")' in tulis and "SB.blok_kurang(" in tulis
    assert 'SB.tersedia_gudang(conn, tid, gudang["id"], it["item_id"], "quantity")' in ro and "SB.blok_kurang(" in ro
    assert '                if bk:\n                    blok(bk["code"], bk["message"], bk["status"], f"items[{i}]")' in ro
    assert '"available_quantity", kunci=kunci' in sj and 'kode="SJ_SO_STOK_TIDAK_CUKUP"' in sj
    for src in (tulis, ro, sj):  # tak ada lagi kueri/kalimat stok salinan lokal
        assert "FROM warehouse_stock" not in src and "tidak cukup\")" not in src and "hanya {avail" not in src


def test_penanda():
    assert FTR.PENANDA_TERBIT_PRATINJAU == "terbit-pratinjau-1-w20261110"
