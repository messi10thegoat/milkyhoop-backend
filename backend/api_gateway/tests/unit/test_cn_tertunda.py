"""Nota kredit atas pendapatan TERTUNDA (V312, 26 Sep 2026). Angka harapan = LITERAL dari spek (bukan dari
konstanta modul): porsi = S_item × Σ(allocated−recognized)/Σallocated, dibatasi sisa tertunda, disebar ke baris
sebanding sisa tertundanya, sisa pembulatan ke baris terakhir; reason return/damaged -> 0 (barang kembali =
sudah terkirim = sudah diakui). Uji ujung-ke-ujung (jurnal nyata) = scripts/ops/journey/skenario_cn_tertunda.py."""
import uuid
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.services import cn_tertunda as C

A, B = uuid.uuid4(), uuid.uuid4()
BRG1, BRG2 = uuid.uuid4(), uuid.uuid4()


def baris(i, item, alok, akui):
    return {"id": i, "item_id": item, "allocated_amount": D(alok), "recognized_amount": D(akui)}


def test_batal_penuh_sebelum_kirim_seluruhnya_dimuka():
    p = C.porsi_tertunda(D("500000"), [{"item_id": BRG1, "subtotal": D("500000")}],
                         [baris(A, BRG1, "500000", "0")], "discount")
    assert p == {A: D("500000.00")}


def test_sudah_diakui_penuh_nol_porsi_seperti_dulu():
    # grapgrap CN-2609-0001: faktur 100% diakui -> seluruhnya Retur
    assert C.porsi_tertunda(D("255000"), [{"item_id": BRG1, "subtotal": D("255000")}],
                            [baris(A, BRG1, "10965000", "10965000")], "discount") == {}


def test_sebagian_terkirim_pro_rata():
    # 40% diakui -> diskon 100.000 = 60.000 Dimuka + 40.000 Retur
    assert C.porsi_tertunda(D("100000"), [{"item_id": BRG1, "subtotal": D("100000")}],
                            [baris(A, BRG1, "500000", "200000")], "pricing_error") == {A: D("60000.00")}


@pytest.mark.parametrize("alasan", ["return", "damaged"])
def test_barang_kembali_tak_menyentuh_dimuka(alasan):
    assert C.porsi_tertunda(D("500000"), [{"item_id": BRG1, "subtotal": D("500000")}],
                            [baris(A, BRG1, "500000", "0")], alasan) == {}


def test_item_cocok_ke_barisnya_sendiri():
    # NK hanya untuk BRG2 -> baris BRG1 tak tersentuh
    p = C.porsi_tertunda(D("30000"), [{"item_id": BRG2, "subtotal": D("30000")}],
                         [baris(A, BRG1, "100000", "0"), baris(B, BRG2, "50000", "0")], "other")
    assert p == {B: D("30000.00")}


def test_tanpa_item_id_menyebar_ke_semua_baris_sebanding_sisa_tertunda():
    # Σalok 300.000, Σtertunda 200.000 (A: 100.000 tertunda, B: 100.000 tertunda) -> U = 90.000 × 2/3 = 60.000
    p = C.porsi_tertunda(D("90000"), [{"item_id": None, "subtotal": D("90000")}],
                         [baris(A, BRG1, "200000", "100000"), baris(B, BRG2, "100000", "0")], "discount")
    assert p == {A: D("30000.00"), B: D("30000.00")}
    assert sum(p.values()) == D("60000.00")


def test_pembulatan_sen_diserap_baris_terakhir_jumlah_tepat():
    p = C.porsi_tertunda(D("100000"), [{"item_id": None, "subtotal": D("100000")}],
                         [baris(A, BRG1, "100000", "0"), baris(B, BRG1, "100000", "0"), baris(uuid.uuid4(), BRG1, "100000", "0")],
                         "discount")
    assert sorted(p.values()) == [D("33333.33"), D("33333.33"), D("33333.34")]
    assert sum(p.values()) == D("100000.00")


def test_porsi_tak_melebihi_sisa_tertunda():
    # NK lebih besar dari alokasi (tak mungkin lewat pastikan_cn_muat_faktur, tapi fungsi murni tetap membatasi)
    p = C.porsi_tertunda(D("900000"), [{"item_id": BRG1, "subtotal": D("900000")}],
                         [baris(A, BRG1, "500000", "0")], "other")
    assert p == {A: D("500000.00")}


def test_dua_item_nk_berbagi_baris_tak_melebihi_sisa():
    p = C.porsi_tertunda(D("400000"), [{"item_id": BRG1, "subtotal": D("300000")},
                                       {"item_id": BRG1, "subtotal": D("100000")}],
                         [baris(A, BRG1, "500000", "250000")], "discount")
    # item1: 300.000 × 250/500 = 150.000; item2: 100.000 × 250/500 = 50.000 -> 200.000 ≤ 250.000
    assert p == {A: D("200000.00")}


class _ConnVoid:
    def __init__(self, porsi):
        self.porsi, self.tulis = porsi, []

    async def fetch(self, q, *a):
        assert "reversed_at IS NULL" in q and a[1] == "kaos"
        return self.porsi

    async def execute(self, q, *a):
        self.tulis.append((q, a))
        return "OK"


def _porsi(akui_saat_nk, akui_kini, terkirim_sesudah=False):
    return [{"id": uuid.uuid4(), "invoice_item_id": A, "amount": D("500000"), "recognized_at_cn": D(akui_saat_nk),
             "invoice_id": uuid.uuid4(), "recognized_now": D(akui_kini), "terkirim_sesudah": terkirim_sesudah}]


@pytest.mark.asyncio
async def test_void_mengembalikan_alokasi_dan_menandai_porsi():
    c = _ConnVoid(_porsi("0", "0"))
    total = await C.pulihkan_saat_void(c, "kaos", {"id": uuid.uuid4()}, reversal_journal_id=uuid.uuid4())
    assert total == D("500000.00")
    sql = [q for q, _ in c.tulis]
    assert any("allocated_amount = allocated_amount + $1" in q for q in sql)
    assert any("SET reversed_at = now()" in q for q in sql)
    assert any("INVOICE_FULFILL:" in str(a) for _, a in c.tulis)          # kunci yang sama dengan fulfill


@pytest.mark.asyncio
async def test_void_ditolak_bila_barang_terkirim_sesudah_nk():
    c = _ConnVoid(_porsi("0", "100000"))
    with pytest.raises(HTTPException) as e:
        await C.pulihkan_saat_void(c, "kaos", {"id": uuid.uuid4()}, periksa_saja=True)
    assert e.value.status_code == 409 and e.value.detail["code"] == "CN_VOID_AFTER_DELIVERY"
    assert not any("UPDATE" in q for q, _ in c.tulis)


@pytest.mark.asyncio
async def test_periksa_saja_tak_menulis():
    c = _ConnVoid(_porsi("0", "0"))
    await C.pulihkan_saat_void(c, "kaos", {"id": uuid.uuid4()}, periksa_saja=True)
    assert not any("UPDATE" in q for q, _ in c.tulis)


def test_router_menyambung_post_dan_void():
    src = open(__import__("app.routers.credit_notes", fromlist=["x"]).__file__).read()
    assert "cn_tertunda.hitung_untuk_nk(" in src and "cn_tertunda.catat_porsi(" in src
    assert src.count("cn_tertunda.pulihkan_saat_void(") == 2        # penjagaan sebelum + pemulihan sesudah pembalik
    assert "AccountRole.REVENUE_DEFERRED" in src                      # Law 27: akun Dimuka lewat peran


@pytest.mark.asyncio
async def test_void_ditolak_bila_terkirim_sesudah_nk_penuh_walau_recognized_tetap_nol():
    # temuan BACKEND 26 Sep: NK batal penuh -> allocated 0 -> kirim mengakui 0 -> recognized tak naik; tetap 409
    c = _ConnVoid(_porsi("0", "0", terkirim_sesudah=True))
    with pytest.raises(HTTPException) as e:
        await C.pulihkan_saat_void(c, "kaos", {"id": uuid.uuid4()}, periksa_saja=True)
    assert e.value.status_code == 409 and e.value.detail["code"] == "CN_VOID_AFTER_DELIVERY"
    assert not any("UPDATE" in q for q, _ in c.tulis)
