"""ppv-revaluasi-wac (6 Nov 2026, putusan LANGSUNG pemilik): selisih harga tagihan vs penerimaan atas stok yang MASIH ada -> Persediaan
(WAC), porsi yang sudah keluar -> 5-10400; GL == kartu stok persis; void kembali persis; cache gudang tak pernah negatif."""
import inspect
import os
from decimal import Decimal as D
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import revaluasi_ppv as RV  # noqa: E402
from app.services import tagihan_aksi as TA  # noqa: E402
from app.services import grn_rencana as GR  # noqa: E402
from app.services import bills_service as BS  # noqa: E402


# ---------------------------------------------------------------- murni
@pytest.mark.parametrize("v,q,basis,harap", [
    (D("924000"), D("240"), D("240"), D("924000")),   # nyawit Bimoli 2L: stok utuh -> seluruhnya
    (D("924000"), D("120"), D("240"), D("462000")),   # separuh sudah keluar -> separuh
    (D("924000"), D("0"), D("240"), D("0")),          # habis -> nol (semua ke PPV)
    (D("924000"), D("500"), D("240"), D("924000")),   # stok lain ikut ada -> tetap maks V
    (D("-100"), D("3"), D("6"), D("-50")),            # tagihan lebih murah, simetris
    (D("0"), D("10"), D("10"), D("0")),
])
def test_bagi(v, q, basis, harap):
    assert RV.bagi(v, q, basis) == harap


def test_lipat_tagih_dulu_biaya_masuk_dan_sisa_sen():
    assert RV.lipat(D("8400000"), D("924000"), D("240")) == (D("38850.00"), D("9324000.00"), D("0.00"))
    biaya, pers, sisa = RV.lipat(D("100.00"), D("0.01"), D("3"))  # 100,01/3 -> 33,34 x 3 = 100,02 -> sisa -0,01 ke PPV
    assert (biaya, pers, sisa) == (D("33.34"), D("100.02"), D("-0.01")) and pers + sisa == D("100.01")


# ---------------------------------------------------------------- kartu stok palsu (nilai = Σ qty x unit_cost; cache gudang CHECK >= 0)
class _Kartu:
    def __init__(self, baris, wac=None, gudang="G-A"):
        self.rows, self.wac, self.gudang, self.cache = list(baris), wac, gudang, {}
        for r in self.rows:
            self.cache[r["warehouse_id"]] = self.cache.get(r["warehouse_id"], D(0)) + r["quantity_in"] - r["quantity_out"]

    def is_in_transaction(self):
        return True

    def _agr(self, pid):
        q = sum((r["quantity_in"] - r["quantity_out"] for r in self.rows if r["product_id"] == pid), D(0))
        n = sum((r["quantity_in"] * r["unit_cost"] - r["quantity_out"] * r["unit_cost"] for r in self.rows if r["product_id"] == pid), D(0))
        return q, n

    async def fetchrow(self, sql, *a):
        if "FROM goods_receipt_items" in sql:
            return {"warehouse_id": self.gudang, "item_code": "BMO2", "nama_produk": "Bimoli 2L"}
        q, n = self._agr(a[1])
        return {"q": q, "nilai": n}

    async def fetchval(self, sql, *a):
        if "get_weighted_average_cost" in sql:
            return self.wac if self.wac is not None else ([r["average_cost"] for r in self.rows if r["product_id"] == a[1]] or [0])[-1]
        return self._agr(a[1])[0]

    async def fetch(self, sql, *a):
        return [r for r in reversed(self.rows) if r["source_type"] == a[1] and r["source_id"] == a[2]]

    async def execute(self, sql, *a):
        r = dict(zip(["id", "tenant_id", "product_id", "product_code", "product_name", "movement_type", "movement_date", "source_type",
                      "source_id", "source_number", "quantity_in", "quantity_out", "quantity_balance", "unit_cost", "total_cost", "average_cost",
                      "warehouse_id", "journal_id", "created_by", "notes"], a))
        self.cache[r["warehouse_id"]] = self.cache.get(r["warehouse_id"], D(0)) + r["quantity_in"] - r["quantity_out"]
        assert self.cache[r["warehouse_id"]] >= 0, f"chk_ws_quantity: gudang {r['warehouse_id']} negatif"
        self.rows.append(r)


P = uuid4()
CTX = {"tenant_id": "t", "user_id": None}
GI = str(uuid4())


def _masuk(q, c, g="G-A", src="GOODS_RECEIPT"):
    return {"product_id": P, "quantity_in": D(q), "quantity_out": D(0), "unit_cost": D(c), "average_cost": D(c), "warehouse_id": g,
            "source_type": src, "source_id": uuid4()}


@pytest.mark.asyncio
async def test_nyawit_bimoli_stok_utuh_wac_naik_tepat_dan_gl_sama_dengan_kartu():
    k = _Kartu([_masuk(240, 35000)])
    rv = await RV.rencana(k, "t", P, D("924000"), D("240"), [GI])
    assert (rv["wac_lama"], rv["wac_baru"], rv["persediaan"], rv["rata"]) == (D("35000"), D("38850.00"), D("924000.00"), D("38850.00"))
    sebelum = k._agr(P)[1]
    await RV.tulis(k, CTX, rv, "bill", "BILL-1", "jid", None)
    q, n = k._agr(P)
    assert q == 240 and n - sebelum == rv["persediaan"]               # Δ kartu stok == baris Persediaan jurnal (health check 15)
    assert [r["movement_type"] for r in k.rows[-2:]] == ["REVALUASI_MASUK", "REVALUASI_KELUAR"]   # MASUK dulu
    assert k.rows[-1]["average_cost"] == D("38850.00")                # get_weighted_average_cost membaca baris terakhir


@pytest.mark.asyncio
async def test_pembulatan_jurnal_sama_dengan_delta_kartu_bukan_selisih_mentah():
    k = _Kartu([_masuk(3, 10)])
    rv = await RV.rencana(k, "t", P, D("100"), D("3"), [GI])  # 100/3 -> WAC 43,33 -> Δ 99,99 (0,01 ke PPV)
    sebelum = k._agr(P)[1]
    await RV.tulis(k, CTX, rv, "bill", "BILL-2", "jid", None)
    assert rv["persediaan"] == D("99.99") == k._agr(P)[1] - sebelum


@pytest.mark.asyncio
async def test_sebagian_sudah_keluar_porsi_stok_saja():
    k = _Kartu([_masuk(240, 35000), {**_masuk(0, 35000), "quantity_out": D(120)}])
    rv = await RV.rencana(k, "t", P, D("924000"), D("240"), [GI])
    assert rv["q"] == 120 and rv["persediaan"] == D("462000.00")      # sisanya 462.000 ke PPV (pemanggil: selisih - persediaan)


@pytest.mark.asyncio
async def test_stok_habis_atau_tanpa_gudang_tanpa_revaluasi():
    assert await RV.rencana(_Kartu([_masuk(240, 35000), {**_masuk(0, 35000), "quantity_out": D(240)}]), "t", P, D("9"), D("240"), [GI]) is None
    assert await RV.rencana(_Kartu([_masuk(240, 35000)]), "t", P, D("924000"), D("240"), []) is None


@pytest.mark.asyncio
async def test_tagihan_lebih_murah_wac_tak_pernah_negatif():
    k = _Kartu([_masuk(2, 10)])
    rv = await RV.rencana(k, "t", P, D("-1000"), D("2"), [GI])
    assert rv["wac_baru"] == D("0") and rv["persediaan"] == D("-20.00")   # sisa -980 ke PPV


@pytest.mark.asyncio
async def test_void_bolak_balik_nilai_dan_wac_kembali_persis_walau_stok_pindah_gudang():
    k = _Kartu([_masuk(240, 35000)])
    rv = await RV.rencana(k, "t", P, D("924000"), D("240"), [GI])
    awal = k._agr(P)
    await RV.tulis(k, CTX, rv, "bill", "BILL-1", "jid", None)
    k.rows.append({**_masuk(0, 38850, g="G-A", src="RELOKASI_KELUAR"), "quantity_out": D(240)})  # pindah gudang A -> B
    k.rows.append({**_masuk(240, 38850, g="G-B", src="RELOKASI_MASUK")})
    k.cache["G-A"], k.cache["G-B"] = D(0), D(240)
    assert await RV.balik(k, CTX, "bill", "jid-balik", None) == 2     # tak melanggar CHECK gudang A (cermin KELUAR = MASUK dulu)
    assert k._agr(P) == awal and k.rows[-1]["average_cost"] == D("35000.00")
    assert [r["source_type"] for r in k.rows[-2:]] == ["PPV_REVALUASI_VOID"] * 2


# ---------------------------------------------------------------- sambungan (pratinjau == posting: hitung_debit + rencana_grn bersama)
def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def test_hitung_debit_porsi_revaluasi_ke_persediaan_sisanya_ppv():
    s = _src(TA.hitung_debit)
    assert '_acc(rv["akun"], rv["persediaan"], True)' in s
    assert '_acc(akun_grn["ppv"], b["selisih"] - (rv["persediaan"] if rv else NOL), False)' in s


def test_rencana_grn_hanya_saat_flag_dan_mengunci_stok_saat_tulis():
    s = _src(TA.rencana_grn)
    assert "await RV.aktif(conn, tid)" in s and 'f"STOK:{tid}:{pid}"' in s and "if kunci:" in s
    assert s.index("if kunci:") < s.index("RV.rencana(")


def test_posting_dan_void_tagihan_menulis_dan_membalik_pasangan():
    post, void = _src(BS.BillsService.post_bill), _src(BS.BillsService.void_bill)
    assert "_rv.tulis(" in post and post.index("_gt.tulis_tautan(") < post.index("_rv.tulis(")
    assert "_rv.balik(" in void and "reversal_journal_id" in void.split("_rv.balik(")[1].split(")")[0]


def test_grn_tagih_dulu_melipat_selisih_ke_harga_masuk_saat_flag():
    s = _src(GR.rencana_grn)
    assert "RV.lipat(" in s and "revaluasi = await RV.aktif(conn, tenant_id)" in s
    assert 'p.get("biaya_dasar") or b["harga_satuan"] / f' in " ".join(inspect.getsource(GR.tulis_grn).split())
