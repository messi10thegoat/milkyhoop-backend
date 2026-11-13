"""BUG-197 (unit "penyesuaian-standar-so"): galat berkode, void/preview tanpa alasan -> blok berkalimat, ubah/hapus draf masuk riwayat, ?search= nomor+catatan+nama/SKU barang.
Paritas nyata (kaos+nyawit) = uji salinan."""
import asyncio
import inspect
import uuid
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.routers.stock_adjustments as SA
from app.schemas import stock_adjustments as S
from app.services import audit_dokumen as AD
from app.services import so_riwayat as SR
from app.services import stok_sesuai as SS
from app.services.galat_berkode import GalatBerkode

T = "kaos-biru-konveksi"
SID = uuid.uuid4()


def req():
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": "00000000-0000-0000-0000-000000000001"}
    return r


def jalan(aw):
    return asyncio.run(aw)


# ---------------- (4) search
def test_syarat_cari_kosong_dan_per_kata():
    assert SS.syarat_cari(None, 2) == (None, []) and SS.syarat_cari("   ", 2) == (None, [])
    c, p = SS.syarat_cari("kaos 001", 5)
    assert p == ["%kaos%", "%001%"] and "$5" in c and "$6" in c and c.count(" AND ") >= 1
    for kolom in ("adjustment_number", "notes", "i.product_name", "i.product_code", "stock_adjustment_items", "pr.item_code"):
        assert kolom in c                                                        # nomor, catatan, nama barang, kode baris, item_code (SKU Barang) via baris
    assert "pr.tenant_id = stock_adjustments.tenant_id" in c                    # join master barang bertenant


def test_syarat_cari_satu_kata_tanpa_kurung_ganda_dan_alias():
    c, p = SS.syarat_cari("abc", 3, a="x")
    assert p == ["%abc%"] and "x.adjustment_number" in c and "x.notes" in c and "i.stock_adjustment_id = x.id" in c


def test_daftar_dan_summary_memakai_predikat_yang_sama():
    assert "SS.syarat_cari(search, param_idx)" in inspect.getsource(SA.list_stock_adjustments)
    assert "SS.syarat_cari(search, 3)" in inspect.getsource(SA.get_stock_adjustments_summary)


# ---------------- (3) riwayat
def test_event_ubah_hapus_draf_masuk_riwayat():
    for aksi, nama in (("updated", "STOCK_ADJUSTMENT_UPDATED"), ("deleted", "STOCK_ADJUSTMENT_DELETED")):
        e = AD.EVENT[("stock_adjustment", aksi)]
        assert e.nama == nama and e.riwayat is True and AD.nama("stock_adjustment", aksi) == nama and nama not in AD.TANPA_RIWAYAT
    assert '{"stock_adjustment": [sa["id"]]}, {"stock_adjustment": True}, limit)' in inspect.getsource(SS.riwayat)      # riwayat membaca audit_logs
    assert SR.ENTITAS_AUDIT["stock_adjustments"] == "stock_adjustment" and SR.ENTITAS_AUDIT["stock_adjustment"] == "stock_adjustment"


def test_ringkas_ubah():
    assert SS.ringkas_ubah("SA-1", ["adjustment_date", "items", "items"], 100000, 120000) == "Penyesuaian stok SA-1 diubah: tanggal, barang · total Rp 100.000 → Rp 120.000"
    assert SS.ringkas_ubah("SA-1", ["notes"], 5000, 5000) == "Penyesuaian stok SA-1 diubah: catatan"
    assert SS.ringkas_ubah("SA-1", ["aneh"], None, None) == "Penyesuaian stok SA-1 diubah: aneh"       # medan tak dikenal tampil apa adanya


def test_patch_delete_dan_void_draf_mencatat_di_transaksi_yang_sama():
    up, de, vo = (inspect.getsource(f) for f in (SA.update_stock_adjustment, SA.delete_stock_adjustment, SA.void_stock_adjustment))
    assert '_ad_sa.nama("stock_adjustment", "updated")' in up and "conn.transaction()" in up
    assert "_catat_hapus_draf(conn, ctx, sa" in de and "conn.transaction()" in de and de.index("_catat_hapus_draf") < de.index("DELETE FROM stock_adjustments")
    assert "_catat_hapus_draf(conn, ctx, sa" in vo and vo.index("_catat_hapus_draf") < vo.index("DELETE FROM stock_adjustments")
    assert "catat_riwayat" not in inspect.getsource(SA.preview_update_stock_adjustment) + inspect.getsource(SA.preview_delete_stock_adjustment)    # pratinjau tak mencatat


# ---------------- (2) void preview tanpa alasan
@pytest.fixture
def void_pratinjau(monkeypatch):
    kat = SimpleNamespace(alasan=None)

    async def gp():
        return None

    async def prat(pool, tid, rencana, payload):
        await rencana(None)
        return {"success": True, "data": {"ok": True, "blocks": [], "jurnal": [{"memo": "pembalik"}], "payload": payload}}

    async def batal(conn, tid, sid, alasan, bl, kunci=False):
        kat.alasan = alasan
        return {}
    monkeypatch.setattr(SA, "get_pool", gp)
    monkeypatch.setattr(SA.SS, "pratinjau", prat)
    monkeypatch.setattr(SA.SS, "rencana_batal", batal)
    return kat


@pytest.mark.parametrize("body", [None, S.VoidStockAdjustmentPreviewRequest(), S.VoidStockAdjustmentPreviewRequest(reason="   ")])
def test_void_preview_tanpa_alasan_blok_berkalimat_jurnal_tetap_terlihat(void_pratinjau, body):
    d = jalan(SA.preview_void_stock_adjustment(req(), SID, body))["data"]
    assert d["ok"] is False and d["blocks"] == [{"code": "PENYESUAIAN_ALASAN_WAJIB", "message": "Isi alasan pembatalan."}] and d["jurnal"] == [{"memo": "pembalik"}]
    assert void_pratinjau.alasan == "(alasan belum diisi)" and d["payload"] == {"reason": None}


def test_void_preview_dengan_alasan_dan_tulis_tetap_wajib_alasan(void_pratinjau):
    d = jalan(SA.preview_void_stock_adjustment(req(), SID, S.VoidStockAdjustmentPreviewRequest(reason=" salah catat ")))["data"]
    assert d["ok"] is True and d["blocks"] == [] and void_pratinjau.alasan == "salah catat"
    with pytest.raises(Exception):
        S.VoidStockAdjustmentRequest(reason="")                                  # /void tulis tetap wajib alasan (min_length=1)


# ---------------- (1) galat berkode
class Pool:
    def acquire(self):
        class Ac:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *e):
                return False

            async def fetchrow(s, *a):
                return None
        return Ac()


def test_404_dan_id_rusak_berkode_indonesia(monkeypatch):
    async def gp():
        return Pool()

    async def tak_ada(*a, **k):
        return None
    monkeypatch.setattr(SA, "get_pool", gp)
    monkeypatch.setattr(SA.SS, "riwayat", tak_ada)
    monkeypatch.setattr(SA.SS, "dokumen", tak_ada)
    for fn, args in ((SA.get_stock_adjustment, (req(), SID)), (SA.get_stock_adjustment_history, (req(), SID, 200)), (SA.get_stock_adjustment_documents, (req(), SID))):
        with pytest.raises(GalatBerkode) as e:
            jalan(fn(*args))
        assert (e.value.status_code, e.value.code, e.value.detail) == (404, "PENYESUAIAN_TIDAK_DITEMUKAN", "Penyesuaian stok tidak ditemukan.")
    with pytest.raises(GalatBerkode) as e:
        jalan(SA.get_stock_adjustment_journal_entries(req(), "bukan-uuid"))
    assert (e.value.status_code, e.value.code) == (400, "ID_PENYESUAIAN_TIDAK_VALID")


def test_tak_ada_pesan_inggris_404_tersisa():
    src = inspect.getsource(SA)
    assert "Stock adjustment not found" not in src and "Invalid adjustment_id format" not in src
