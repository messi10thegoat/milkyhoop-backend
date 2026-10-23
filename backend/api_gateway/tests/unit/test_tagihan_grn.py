"""Tagihan <-> GRN 3-way match (sisi BACKEND, 23 Okt 2026; kontrak grn_tagih BACKEND2 KONTRAK-grn-20261023 §3; putusan pemilik P1-P6 22 Okt).
Baris ber-GRN: Dr GRNI (nilai terima) + selisih harga ke PPV (kredit bila negatif), TIDAK Dr Persediaan, TIDAK inbound stok; flag mati =
rencana kosong = jurnal/stok byte-identik; Law 4 header == Σ baris; void membalik tautan di transaksi yang sama."""
import asyncio
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4

from app.services import tagihan_aksi as TA

APP = Path(__file__).resolve().parents[2] / "app"
BS = (APP / "services/bills_service.py").read_text()


def _r(i, net):
    return {"id": i, "net": D(net)}


def test_neto_baris_diskon_kepala_proporsional_dan_tepat():
    a, b, c = uuid4(), uuid4(), uuid4()
    out = TA.neto_baris([_r(a, "1000"), _r(b, "2000"), _r(c, "0")], D("2700"))  # diskon kepala 300
    assert out[a] == D("900.00") and out[b] == D("1800.00") and out[c] == D("0")
    assert sum(out.values()) == D("2700")
    out = TA.neto_baris([_r(a, "333.33"), _r(b, "333.33"), _r(c, "333.34")], D("900"))  # sisa sen ke baris terakhir bernilai
    assert sum(out.values()) == D("900")
    assert TA.neto_baris([_r(a, "500")], D("500")) == {a: D("500")}  # tanpa diskon kepala = bi.total


class _K:
    def __init__(self, rows, roles):
        self.rows, self.roles = rows, roles

    async def fetch(self, sql, *a):
        return self.rows

    async def fetchval(self, sql, tid, role):
        return self.roles.get(role)


def test_hitung_debit_grn_grni_ppv_tanpa_persediaan(monkeypatch):
    inv, grni, ppv, beban = uuid4(), uuid4(), uuid4(), uuid4()
    g1, g2, x = uuid4(), uuid4(), uuid4()

    async def akun_inv(conn, tid, pid):
        return {"inventory_account_id": inv}
    import app.services.inventory_helpers as IH
    monkeypatch.setattr(IH, "resolve_inventory_accounts", akun_inv)
    rows = [{"id": g1, "product_id": uuid4(), "net": D("1000"), "item_type": "goods", "track_inventory": True, "purchase_account_id": None},
            {"id": g2, "product_id": uuid4(), "net": D("500"), "item_type": "goods", "track_inventory": True, "purchase_account_id": None},
            {"id": x, "product_id": uuid4(), "net": D("300"), "item_type": "goods", "track_inventory": True, "purchase_account_id": None}]
    grn = {"rencana": {g1: {"nilai_grni": D("950"), "selisih": D("50")}, g2: {"nilai_grni": D("520"), "selisih": D("-20")}},
           "akun": {"grni": grni, "ppv": ppv}}
    hasil = asyncio.run(TA.hitung_debit(_K(rows, {}), "t", uuid4(), D("1800"), inv, grn))
    d = {a: amt for a, amt, _ in hasil}
    assert d[grni] == D("1470") and d[ppv] == D("30") and d[inv] == D("300")  # hanya baris tanpa GRN ke Persediaan
    assert sum(d.values()) == D("1800")
    # flag mati (grn None) = perilaku lama: semua baris ke Persediaan
    lama = asyncio.run(TA.hitung_debit(_K(rows, {}), "t", uuid4(), D("1800"), inv, None))
    assert lama == [(inv, D("1800"), True)]


def test_ppv_negatif_dan_sisa_kepala_ke_ppv_bila_semua_grn(monkeypatch):
    grni, ppv, inv = uuid4(), uuid4(), uuid4()
    g = uuid4()
    rows = [{"id": g, "product_id": uuid4(), "net": D("1000"), "item_type": "goods", "track_inventory": True, "purchase_account_id": None}]
    grn = {"rencana": {g: {"nilai_grni": D("1100"), "selisih": D("-100.01")}}, "akun": {"grni": grni, "ppv": ppv}}
    hasil = {a: amt for a, amt, _ in asyncio.run(TA.hitung_debit(_K(rows, {}), "t", uuid4(), D("1000"), inv, grn))}
    assert hasil[grni] == D("1100") and hasil[ppv] == D("-100") and inv not in hasil  # sisa sen -> PPV, bukan GRNI/Persediaan
    assert sum(hasil.values()) == D("1000")


def test_memo_grn_dan_lama():
    grni, ppv, x = uuid4(), uuid4(), uuid4()
    g = {"rencana": {1: {}}, "akun": {"grni": grni, "ppv": ppv}}
    assert TA.memo_debit(grni, True, "V", g).startswith("Barang diterima belum ditagih")
    assert TA.memo_debit(ppv, False, "V", g).startswith("Selisih harga pembelian")
    assert TA.memo_debit(x, True, "V", None) == "Pembelian - V" and TA.memo_debit(x, False, "V", None) == "Pembelian jasa/beban - V"


def test_post_bill_kunci_blok_lewati_inbound_tautan():
    pb = BS[BS.index("    async def post_bill("):BS.index("    async def update_bill_v2(")]
    i_kunci_bill = pb.index("FOR UPDATE")
    i_grn = pb.index("await TA.rencana_grn(conn, tenant_id, bill_id, Decimal(str(subtotal)), kunci=True)")
    assert i_kunci_bill < i_grn < pb.index("get_next_journal_number")  # tagihan -> PO -> nomor
    assert 'return {"success": False, "message": _grn["blocks"][0]["message"], "data": None}' in pb
    assert "_total_header," in pb and "bill[\"vendor_name\"], line_number, _grn," in pb
    assert 'if _grn and item["id"] in _grn["rencana"]:\n                        continue' in pb
    assert pb.index("_gt.tulis_tautan(conn, list(_grn[\"rencana\"].values()))") > pb.index("UPDATE journal_entries SET status = 'POSTED'")


def test_void_balik_tautan_dan_pratinjau_sama():
    vb = BS[BS.index("    async def void_bill("):BS.index("    async def get_summary(")]
    assert "_gt.balik_tautan(conn," in vb and vb.index("_gt.balik_tautan(") < vb.index("pulihkan_tagih(")
    ta = (APP / "services/tagihan_aksi.py").read_text()
    rp = ta[ta.index("async def rencana_posting("):]
    assert "await rencana_grn(conn, tid, b[\"id\"], total - pajak)" in rp and "bloks += grn[\"blocks\"]" in rp
    assert "kunci=True" not in rp  # pratinjau tanpa kunci
