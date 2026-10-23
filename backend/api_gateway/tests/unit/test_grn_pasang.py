"""Aturan (D) sisi penerimaan (grn_pasang.pasangkan): FIFO ke tagihan lebih dulu, legacy, jurnal seimbang, sen tertutup KEDUA arah."""
from decimal import Decimal as D

from app.services import grn_pasang as GP


def _t(i, qty, nilai, legacy=False):
    return {"bill_item_id": i, "qty_terbuka": D(qty), "nilai_terbuka": D(nilai), "legacy": legacy}


def _seimbang(r):
    j = r["jurnal"]
    debit = j["persediaan"] + max(j["selisih"], D("0"))
    kredit = j["grni_kredit"] + max(-j["selisih"], D("0"))
    return debit == kredit


def test_tanpa_tagihan_tertunda_sama_dengan_grn_biasa():
    r = GP.pasangkan(D("10"), D("1000"), [])
    assert (r["qty_stok"], r["nilai_stok"], r["qty_bebas"], r["pasangan"], r["legacy"]) == (D("10"), D("10000.00"), D("10"), [], [])
    assert r["jurnal"] == {"persediaan": D("10000.00"), "grni_kredit": D("10000.00"), "selisih": D("0")}


def test_tagih_dulu_6_lalu_terima_10_pasangan_dan_selisih():
    r = GP.pasangkan(D("10"), D("1000"), [_t("b1", "6", "6600.00")])
    p = r["pasangan"][0]
    assert (p["qty"], p["nilai_terima"], p["nilai_tagih"], p["selisih"]) == (D("6"), D("6000.00"), D("6600.00"), D("600.00"))
    assert (r["qty_bebas"], r["nilai_bebas"]) == (D("4"), D("4000.00"))
    assert r["jurnal"] == {"persediaan": D("10000.00"), "grni_kredit": D("10600.00"), "selisih": D("600.00")} and _seimbang(r)


def test_harga_tagihan_lebih_rendah_selisih_kredit():
    r = GP.pasangkan(D("5"), D("1000"), [_t("b1", "5", "4500.00")])
    assert r["jurnal"] == {"persediaan": D("5000.00"), "grni_kredit": D("4500.00"), "selisih": D("-500.00")} and _seimbang(r)


def test_fifo_dua_tagihan_dan_terima_sebagian():
    r = GP.pasangkan(D("5"), D("1000"), [_t("b1", "3", "3300.00"), _t("b2", "4", "4400.00")])
    assert [(x["bill_item_id"], x["qty"]) for x in r["pasangan"]] == [("b1", D("3")), ("b2", D("2"))]
    assert r["pasangan"][1]["nilai_tagih"] == D("2200.00") and r["qty_bebas"] == 0 and _seimbang(r)


def test_legacy_baris_terpisah_tanpa_stok_jurnal():
    r = GP.pasangkan(D("10"), D("1000"), [_t("lama", "4", "4000.00", legacy=True), _t("b2", "3", "3300.00")])
    assert r["legacy"] == [{"bill_item_id": "lama", "qty": D("4")}]
    assert r["qty_stok"] == D("6") and r["nilai_stok"] == D("6000.00") and [x["bill_item_id"] for x in r["pasangan"]] == ["b2"]
    assert r["jurnal"]["persediaan"] == D("6000.00") and _seimbang(r)


def test_sen_tertutup_kedua_arah_rasio_3_banding_1():
    """MASTER (b): rasio tak bulat. Tagihan 3 unit B=1000.00 (333.33..); terima 1 lalu 2. Harga terima 1000/3."""
    h = D("1000") / D("3")
    r1 = GP.pasangkan(D("1"), h, [_t("b", "3", "1000.00")])
    p1 = r1["pasangan"][0]
    assert (p1["nilai_tagih"], p1["nilai_terima"]) == (D("333.33"), D("333.33")) and _seimbang(r1)
    # terima kedua 2 unit: tagihan sisa qty 2, nilai_terbuka 1000.00 - 333.33 = 666.67 -> pasangan penghabis memakai sisa PERSIS
    r2 = GP.pasangkan(D("2"), h, [_t("b", "2", "666.67")])
    p2 = r2["pasangan"][0]
    assert p2["nilai_tagih"] == D("666.67") and p1["nilai_tagih"] + p2["nilai_tagih"] == D("1000.00")
    assert p2["nilai_terima"] == r2["nilai_stok"] == D("666.67") and _seimbang(r2)
    # R total semua penerimaan == r2(qty x harga) per penerimaan (sama dgn GRN tanpa pasangan) -> GRNI pasangan = B - B = 0
    assert r1["nilai_stok"] + r2["nilai_stok"] == D("1000.00")


def test_jurnal_selalu_seimbang_acak_kecil():
    import itertools
    for qt, qa, qb in itertools.product(("1", "2", "7"), ("1", "3"), ("2", "5")):
        r = GP.pasangkan(D(qt), D("333.3333"), [_t("a", qa, str(GP.r2(D(qa) * D("350")))), _t("b", qb, str(GP.r2(D(qb) * D("320"))))])
        assert _seimbang(r), (qt, qa, qb, r["jurnal"])
        assert r["nilai_bebas"] + sum(p["nilai_terima"] for p in r["pasangan"]) == r["nilai_stok"]
