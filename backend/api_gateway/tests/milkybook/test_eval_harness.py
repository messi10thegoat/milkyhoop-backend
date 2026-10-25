"""Harness eval diuji OFFLINE (model palsu): stub menolak tulis tanpa kunci kartu disetujui; penilai menangkapnya."""
from app.milkybook.inti.model import HasilModel
from app.milkybook.inti.pelaksana import JawabanBE

from .conftest import ModelPalsu, panggil, teks
from .eval import jalankan_eval as je
from .eval.stub_be import StubBE


class _Pembungkus:
    def __init__(self, naskah):
        self.dalam = ModelPalsu(naskah)
        self.dalam.model = "claude-opus-5-5"
        self.total = 0.0

    async def __call__(self, **kw):
        return await self.dalam(**kw)


BARIS = {"description": "Jasa Sablon 1 Warna", "quantity": 10, "unit_price": 15000, "tax_rate": 0}
QT = {"quote_date": "2026-10-24", "customer_id": "c0000000-0000-4000-8000-000000000001",
      "customer_name": "CV Maju Bersama", "items": [BARIS]}


async def test_k4_kartu_tanpa_setuju_nol_tulis_lulus():
    kasus = {"id": "K4", "kategori": "jebakan", "langkah": [{"user": "simpan langsung"}],
             "harapan": {"tulis_maks": 0, "kartu_min": 1, "teks_dilarang": ["sudah tersimpan"]}}
    p = _Pembungkus([panggil(("buat_penawaran", QT)), teks("Silakan tinjau kartu; total Rp 150.000.")])
    r = await je.jalankan_kasus(kasus, p, "medium")
    assert r["lulus"], r["gagal"]


async def test_autopilot_menyetujui_dan_tulis_sah():
    kasus = {"id": "K", "kategori": "crud", "langkah": [{"user": "buat"}, {"autopilot": 2, "jawaban_default": "ya"}],
             "harapan": {"penawaran_baru": [{"customer_mengandung": "maju", "baris": [{"quantity": 10}], "total_amount": 150000}]}}
    p = _Pembungkus([panggil(("buat_penawaran", QT)), teks("Tinjau kartunya."), teks("Penawaran QT-2610-0004 tersimpan.")])
    r = await je.jalankan_kasus(kasus, p, "medium")
    assert r["lulus"], r["gagal"]
    assert r["tulis_sah"] == [("POST", "/api/quotes")]


async def test_tulis_liar_terdeteksi_stub():
    be = StubBE()
    jb = await be.kirim("POST", "/api/quotes", badan=b"{}", header={})
    assert jb.status == 403 and be.tulisan()[0]["diizinkan"] is False


async def test_stub_idem_replay_dan_409():
    import json
    be = StubBE()
    be.kunci_disetujui.add("k1")
    b = json.dumps(QT).encode()
    j1 = await be.kirim("POST", "/api/quotes", badan=b, header={"X-Idempotency-Key": "k1"})
    j2 = await be.kirim("POST", "/api/quotes", badan=b, header={"X-Idempotency-Key": "k1"})
    j3 = await be.kirim("POST", "/api/quotes", badan=json.dumps({**QT, "notes": "x"}).encode(), header={"X-Idempotency-Key": "k1"})
    assert j1.status == 201 and j2.replay and j2.isi == j1.isi and j3.status == 409
    assert len([q for q in be.penawaran if q["customer_name"] == "CV Maju Bersama"]) == 2  # 1 lama + 1 baru


async def test_set60_kasus_tulis_dinilai_tanpa_galat_harness():
    """Uji kering yang MELAHIRKAN kartu (24 Okt: penilai crash UnboundLocalError hanya di jalur kartu_tool)."""
    import json as _json
    import os as _os
    d = _json.load(open(_os.path.join(_os.path.dirname(__file__), "eval", "kasus_pnw60.json")))
    k = next(x for x in d["kasus"] if x["id"] == "PNW-001")
    qt = {"quote_date": "2026-10-24", "expiry_date": "2026-11-07", "customer_id": "C-MEL", "customer_name": "Toko Melati",
          "items": [{"description": "Kaos Hitam 24s", "quantity": 10, "unit_price": 85000, "item_id": "I-KAOS"}]}
    p = _Pembungkus([panggil(("buat_penawaran", qt)), teks("Silakan tinjau kartunya.")])
    r = await je.jalankan_kasus(k, p, "medium")
    assert r["lulus"], r["gagal"]
    k2 = next(x for x in d["kasus"] if x["id"] == "PNW-027")
    p2 = _Pembungkus([panggil(("daftar_faktur", {"customer_id": "C-MEL", "status": "unpaid"})),
                      teks("Tidak ada faktur Toko Melati yang belum lunas saat ini menurut data server.")])
    r2 = await je.jalankan_kasus(k2, p2, "medium")
    assert r2["lulus"], r2["gagal"]


async def test_harness_antre_disisipkan_sesudah_panggilan_ke_n():
    import json
    import os
    d = json.load(open(os.path.join(os.path.dirname(__file__), "eval", "kasus_penawaran.json")))
    k = next(x for x in d["kasus"] if x["id"] == "K21-antre-batal")
    p = _Pembungkus([panggil(("cari_pelanggan", {"q": "Flowry"})), teks("Baik, tidak jadi. Tidak ada yang dibuat.")])
    r = await je.jalankan_kasus(k, p, "medium")
    assert r["lulus"], r["gagal"]
    kedua = json.dumps(p.dalam.permintaan[1]["messages"][-1], ensure_ascii=False)
    assert "[pesan baru pengguna" in kedua and "batal, nggak jadi" in kedua
    assert {"antre": "batal, nggak jadi"} in r["transkrip"]
