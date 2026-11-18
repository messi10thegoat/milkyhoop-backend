"""rencana.berlaku{} (pratinjau Buat Barang + form bawaan) membawa `label` manusiawi per butir + peta `label` semua butir (FE hapus cadangan LABEL_BERLAKU); label dari label_rencana (satu sumber)."""
from app.services import bawaan_berlaku as BB
from app.services import kategori_akun as KAK
from app.services import label_rencana as LR

USAHA = {"akun_penjualan": {"id": "a1", "kode": "4-1", "nama": "Penjualan"}, "akun_persediaan": {"id": "a2", "kode": "1-1", "nama": "Persediaan"},
         "akun_hpp": {"id": "a3", "kode": "5-1", "nama": "HPP"}, "pajak_beli": {"id": "t1", "kode": "PPN", "nama": "PPN 11%"}}
ENAM = {"akun_penjualan": "Akun penjualan", "akun_pembelian": "Akun pembelian", "akun_persediaan": "Akun persediaan", "akun_hpp": "Akun HPP", "pajak_jual": "Pajak penjualan", "pajak_beli": "Pajak pembelian"}


def hasil(dari=None):
    h = BB.berlaku({"item_type": "goods", "track_inventory": True, "purchase_tax_id": "t1"}, USAHA, {}, {"t1": {"id": "t1", "kode": "PPN", "nama": "PPN 11%"}})
    return BB.beri_teks(h, dari)


def test_label_tiap_butir_terisi_dan_peta_semua_butir():
    h = hasil()
    assert h["label"] == ENAM                                   # termasuk butir null (pajak_jual, akun_pembelian bila tak terpetakan)
    for b, lab in ENAM.items():
        if h.get(b):
            assert h[b]["label"] == lab, b
    assert h["akun_penjualan"]["label"] == "Akun penjualan" and h["pajak_beli"]["label"] == "Pajak pembelian"


def test_label_bersumber_label_rencana_dan_sama_dengan_kategori():
    assert BB.LABEL_BUTIR["akun_hpp"] == LR.LABEL_AKUN["hpp"] == "Akun HPP"
    assert set(LR.LABEL_AKUN) == set(KAK.JENIS)
    jual = {"id": "11111111-1111-1111-1111-111111111111", "account_code": "4-1", "name": "P", "account_type": "REVENUE", "is_active": True, "is_header": False}
    kat = {"id": "c", "name": "K", **{f"akun_{j}": None for j in KAK.JENIS}}
    p = KAK.rencana_akun(kat, {"hpp": None, "penjualan": jual["id"]}, {jual["id"]: jual})["rencana"]["perubahan"][0]
    assert p["label"] == LR.LABEL_AKUN["penjualan"]


def test_kunci_lama_tetap_dan_butir_null_tanpa_label_objek():
    h = hasil({"kategori": "Minuman", "jenis": ["persediaan"]})
    assert h["akun_persediaan"]["sumber"] == "kategori" and h["akun_persediaan"]["text"] == "1-1 Persediaan" and h["akun_persediaan"]["sumber_text"] == "Dari kategori 'Minuman'"
    assert h["pajak_jual"] is None and "teks" in h


def test_detail_barang_bawaan_berlaku_lewat_beri_teks():
    import inspect
    from app.routers import items as IT
    src = inspect.getsource(IT.get_item)
    assert "_BB.beri_teks(await _BB.untuk_barang(conn, tenant_id, dict(row)))" in src and '"bawaan_berlaku": bawaan_berlaku,' in src
