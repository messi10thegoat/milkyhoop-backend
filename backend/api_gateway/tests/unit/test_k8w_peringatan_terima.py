"""K8-W (17 Nov 2026; Accurate PT BIS via TESTER): Terima Pemindahan Barang yang MENYISAKAN barang di Transit = PERINGATAN (bukan blok) "Selisih N belum diterima; dapat diterima di
penerimaan berikutnya." -- pratinjau (preview/receive) DAN respons tulis (/receive). Aditif, tanpa migrasi; Terima tuntas = tanpa peringatan."""
import pathlib
import uuid
from decimal import Decimal as D

from app.schemas import stock_transfers as SCH
from app.services import pemindahan_dua_langkah as D2

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def ln(nama, unit, sisa):
    return {"item_id": uuid.UUID(int=hash(nama) % (1 << 100)), "name": nama, "unit": unit, "tertunda_sesudah": D(str(sisa))}


def test_peringatan_hanya_untuk_barang_yang_masih_tersisa_format_dan_json_aman():
    a, b, c = ln("Aqua 600", "pcs", "4"), ln("Beras", "kg", "1500.5"), ln("Teh", "pcs", "0")
    out = D2.peringatan_terima([a, b, c])
    assert [w["nama"] for w in out] == ["Aqua 600", "Beras"]      # yang tuntas (0) tak diperingatkan
    assert out[0]["message"] == "Selisih 4 pcs Aqua 600 belum diterima; dapat diterima di penerimaan berikutnya."
    assert out[1]["message"] == "Selisih 1.500,5 kg Beras belum diterima; dapat diterima di penerimaan berikutnya."
    assert (out[0]["code"], out[0]["selisih"], out[0]["unit"], out[1]["selisih"]) == ("PB_TERIMA_KURANG", 4.0, "pcs", 1500.5)
    assert isinstance(out[0]["item_id"], str) and isinstance(out[0]["selisih"], float)          # aman untuk JSON (tak ada UUID/Decimal mentah)


def test_terima_tuntas_tanpa_peringatan_dan_satuan_kosong():
    assert D2.peringatan_terima([ln("A", "pcs", 0), ln("B", None, "0.00")]) == []
    assert D2.peringatan_terima([ln("Tanpa satuan", None, "2")])[0]["message"] == "Selisih 2 Tanpa satuan belum diterima; dapat diterima di penerimaan berikutnya."
    assert D2.peringatan_terima([]) == []


def test_rencana_terima_dan_terima_membawa_peringatan_router_dan_skema():
    src = (APP / "services" / "pemindahan_dua_langkah.py").read_text()
    assert '"tuntas": tuntas, "peringatan": peringatan_terima(lines)' in src and '"catatan_fase": catatan, "peringatan": rencana["peringatan"]' in src
    rt = (APP / "routers" / "stock_transfers.py").read_text()
    assert '"bawaan_semua_tertunda", "tujuan", "peringatan")' in rt            # _pv meneruskan ke pratinjau Terima
    assert 'hasil = await pd2.terima(conn, ctx, transfer_id, _baris_terima(body))' in rt and 'peringatan=hasil.get("peringatan") or []' in rt
    assert "peringatan" in SCH.ReceiveTransferResponse.model_fields and SCH.ReceiveTransferResponse.model_fields["peringatan"].default == []


def test_peringatan_bukan_blok():
    src = (APP / "services" / "pemindahan_dua_langkah.py").read_text().split("def peringatan_terima")[1].split("async def rencana_terima")[0]
    assert "_blok(" not in src and "blocks" not in src      # peringatan tak menggagalkan Terima
