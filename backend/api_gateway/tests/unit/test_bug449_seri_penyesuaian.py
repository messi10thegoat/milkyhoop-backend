"""BUG-449: penyesuaian barang bernomor seri -- pratinjau buat/ubah/posting menilai nomor seri dgn penentu SAMA pintu kartu (serial_gerak)."""
import asyncio
from decimal import Decimal
from pathlib import Path

from app.services import stok_sesuai as SS
from app.services import serial_gerak as SG
from app.services.bayar_keluar_rencana import Blok


def jalan(c):
    return asyncio.run(c)


def _pasang(monkeypatch, wajib=True, tersedia=None):
    async def w(conn, tid, pid):
        return wajib

    async def t(conn, tid, pid, nama, gid, serials):
        return tersedia
    monkeypatch.setattr(SG, "wajib", w)
    monkeypatch.setattr(SG, "blok_tersedia", t)


def test_wajib_isi_saat_keluar(monkeypatch):
    _pasang(monkeypatch)
    bl = Blok(True)
    jalan(SS._blok_seri(None, "t", "p", "Kabel", None, Decimal("-1"), "g", bl))
    assert bl.daftar[0]["code"] == "SERIAL_WAJIB" and bl.daftar[0]["field"] == "serials" and "isi nomor serinya" in bl.daftar[0]["message"]


def test_jumlah_kembar_tersedia(monkeypatch):
    _pasang(monkeypatch)
    bl = Blok(True)
    jalan(SS._blok_seri(None, "t", "p", "Kabel", ["A1", "a1"], Decimal("-2"), "g", bl))
    assert bl.daftar[0]["code"] == "SERIAL_KEMBAR"
    bl = Blok(True)
    jalan(SS._blok_seri(None, "t", "p", "Kabel", ["A1"], Decimal("-2"), "g", bl))
    assert bl.daftar[0]["code"] == "SERIAL_JUMLAH_TAK_SAMA"
    _pasang(monkeypatch, tersedia=("SERIAL_TAK_TERSEDIA", "Nomor seri Kabel tidak dikenal: Z9."))
    bl = Blok(True)
    jalan(SS._blok_seri(None, "t", "p", "Kabel", ["Z9"], Decimal("-1"), "g", bl))
    assert bl.daftar[0]["code"] == "SERIAL_TAK_TERSEDIA"
    bl = Blok(True)   # penambahan: ketersediaan tak dinilai (nomor baru masuk)
    jalan(SS._blok_seri(None, "t", "p", "Kabel", ["Z9"], Decimal("1"), "g", bl))
    assert bl.daftar == []


def test_tulis_melempar_kode_sama(monkeypatch):
    _pasang(monkeypatch)
    try:
        jalan(SS._blok_seri(None, "t", "p", "Kabel", None, Decimal("-1"), "g", Blok(False)))
        assert False
    except Exception as e:
        assert getattr(e, "code", None) == "SERIAL_WAJIB" and e.status_code == 422


def test_terpasang_di_susun_dan_posting():
    s = (Path(__file__).resolve().parents[2] / "app/services/stok_sesuai.py").read_text()
    assert s.count("await _blok_seri(") == 2
