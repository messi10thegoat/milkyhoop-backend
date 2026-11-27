"""Serial S3 (27 Nov 2026, flag gelap serial_v1, V514): retur jual (NK) masuk lagi, retur beli (ND) keluar 'returned_vendor',
Pemindahan keluar 'transit' -> masuk tujuan available; void Pemindahan membalik kedua kaki (taut dokumen yang sama bukan 'bergerak')."""
import asyncio
import inspect
from pathlib import Path

from app.services import serial_gerak as SG


def test_status_pindah_transit_dan_masuk_menerima_transit():
    assert SG.STATUS_KELUAR["RELOKASI_GUDANG"] == "transit" and "RELOKASI_GUDANG" in SG.SUMBER_PINDAH
    src = inspect.getsource(SG.masuk)
    assert 'not (pindah and ada[s.upper()]["status"] == "transit")' in src


def test_balik_abaikan_taut_dokumen_sama():
    src = inspect.getsource(SG.balik)
    assert "l2.source_id IS DISTINCT FROM l.source_id" in src


class Kon:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append((sql, a))


def test_simpan_draf_nol_kueri_tanpa_serial_dan_tabel_terdaftar():
    k = Kon()
    asyncio.run(SG.simpan_draf(k, "credit_note_items", "x", 1, None))
    assert k.sql == []
    asyncio.run(SG.simpan_draf(k, "stock_transfer_items", "x", 2, ["A"]))
    assert "stock_transfer_id = $1 AND line_number = $2" in k.sql[0][0] and k.sql[0][1] == ("x", 2, ["A"])
    try:
        asyncio.run(SG.simpan_draf(k, "products", "x", 1, ["A"]))
        assert False
    except KeyError:
        pass


def test_penulis_meneruskan_serial():
    from app.routers import credit_notes as CN, vendor_credits as VC
    from app.services import pemindahan_barang as PB, nota_kredit_rencana as NKR
    from app.schemas import credit_notes as SCN, vendor_credits as SVC
    assert "serials" in SCN.CreditNoteItemCreate.model_fields and "serials" in SVC.VendorCreditItemCreate.model_fields
    c, v, p = inspect.getsource(CN), inspect.getsource(VC), inspect.getsource(PB)
    assert c.count('serial_gerak.simpan_draf(conn, "credit_note_items"') == 2 and 'serials=item.get("serials")' in c
    assert v.count('serial_gerak.simpan_draf(conn, "vendor_credit_items"') == 2 and 'baca_draf(conn, "vendor_credit_items"' in v
    assert 'balik_ledger=r["id"]' in p and 'serials=ln.get("serials")' in p and "serial_gerak.blok_serial(" in p
    assert "serial_gerak.blok_serial(" in inspect.getsource(NKR)


def test_migrasi_v514_aditif():
    m = Path(__file__).resolve().parents[3] / "backend/migrations/V514__serial_retur_pemindahan.sql"
    isi = [l for l in m.read_text().splitlines() if l and not l.startswith("--")]
    assert isi == [f"ALTER TABLE {t} ADD COLUMN serials TEXT[];" for t in ("credit_note_items", "vendor_credit_items", "stock_transfer_items")]
