"""BUG-228 butir 3 (putusan pemilik 22 Nov): nilai penyesuaian stok BERTANDA (aditif). total_value tetap magnitudo; arah dari tanda quantity_adjustment baris;
header = Σ baris (naik - turun); Σ daftar bertanda cakupan/filter sama dgn sum_total (beda hanya tanda)."""
import asyncio
import uuid
from decimal import Decimal as D
from types import SimpleNamespace

from app.routers import stock_adjustments as SA
from app.services import stok_sesuai as SS

T = "kaos-biru-konveksi"
A1, A2, A3, A4 = (uuid.uuid4() for _ in range(4))


# ---- helper murni
def test_teks_bertanda_plus_minus_nol():
    assert SS.teks_bertanda(144000) == "+Rp 144.000" and SS.teks_bertanda(-36000) == "−Rp 36.000" and SS.teks_bertanda(0) == "Rp 0"


def test_nilai_bertanda_dan_arah():
    assert SS.nilai_bertanda(D("-1"), D("66252")) == D("-66252") and SS.nilai_bertanda(D("2"), D("100")) == D("100")
    assert [SS.arah_nilai(a, b) for a, b in [(5, 0), (0, 5), (5, 3), (0, 0)]] == ["tambah", "kurang", "campur", "nol"]
    assert SS.medan_bertanda(144000, 0) == {"arah": "tambah", "total_value_signed": 144000, "nilai_bertanda_text": "+Rp 144.000"}
    assert SS.medan_bertanda(0, 36000)["total_value_signed"] == -36000 and SS.medan_bertanda(10, 4) == {"arah": "campur", "total_value_signed": 6, "nilai_bertanda_text": "+Rp 6"}


# ---- daftar lewat tiruan koneksi: campuran increase / decrease / damaged / recount (campur)
def _row(i, no, jenis, nilai):
    import datetime as dt
    return {"id": i, "adjustment_number": no, "adjustment_date": dt.date(2026, 11, 5), "adjustment_type": jenis, "storage_location_name": None,
            "total_value": nilai, "item_count": 1, "status": "posted", "reference_no": None, "created_at": dt.datetime(2026, 11, 5, 9, 0), "journal_id": None}


class Kon:
    def __init__(self):
        self.sql = []
        self.rows = [_row(A1, "SA-1", "increase", 144000), _row(A2, "SA-2", "decrease", 36000), _row(A3, "SA-3", "damaged", 24000), _row(A4, "SA-4", "recount", 15000)]
        # agregat per dokumen (naik, turun) -- recount campur: +10.000 / -5.000
        self.agg = [{"stock_adjustment_id": A1, "naik": D("144000"), "turun": D("0")}, {"stock_adjustment_id": A2, "naik": D("0"), "turun": D("36000")},
                    {"stock_adjustment_id": A3, "naik": D("0"), "turun": D("24000")}, {"stock_adjustment_id": A4, "naik": D("10000"), "turun": D("5000")}]

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        if "SELECT COUNT(*)" in sql:
            return 4
        if "CASE WHEN i.quantity_adjustment < 0" in sql:
            return D("89000")      # net: +144.000 -36.000 -24.000 +10.000 -5.000
        return D("219000")         # sum_total mutlak: Σ total_value dokumen (tak berubah)

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "GROUP BY stock_adjustment_id" in sql:
            return self.agg
        return self.rows


class Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class C:
            async def __aenter__(s):
                return k

            async def __aexit__(s, *a):
                return False
        return C()


def _daftar(monkeypatch, k, with_totals=True, **kw):
    async def pool():
        return Pool(k)
    monkeypatch.setattr(SA, "get_pool", pool)
    req = SimpleNamespace(headers={}, state=SimpleNamespace(user={"tenant_id": T, "user_id": str(uuid.uuid4())}))
    return asyncio.run(SA.list_stock_adjustments(req, status="all", adjustment_type=None, search=None, date_from=None, date_to=None, skip=0, limit=20,
                                                 sort_by="created_at", sort_order="desc", aksi=None, with_totals=with_totals, **kw))


def test_daftar_arah_dan_nilai_bertanda_per_dokumen(monkeypatch):
    h = _daftar(monkeypatch, Kon())
    got = [(i["adjustment_number"], i["arah"], i["total_value_signed"], i["nilai_bertanda_text"], i["total_value"]) for i in h["items"]]
    assert got == [("SA-1", "tambah", 144000, "+Rp 144.000", 144000), ("SA-2", "kurang", -36000, "−Rp 36.000", 36000),
                   ("SA-3", "kurang", -24000, "−Rp 24.000", 24000), ("SA-4", "campur", 5000, "+Rp 5.000", 15000)]


def test_sum_total_signed_net_dan_sum_total_lama_tetap_mutlak(monkeypatch):
    k = Kon()
    h = _daftar(monkeypatch, k)
    assert h["sum_total_signed"] == 89000 and h["sum_total_signed_text"] == "+Rp 89.000" and h["sum_total"] == "219000.00"
    # Σ dokumen bertanda == sum_total_signed (konsisten per dokumen)
    assert sum(i["total_value_signed"] for i in h["items"]) == 89000
    s = [x for x in k.sql if "CASE WHEN i.quantity_adjustment < 0" in x][0]
    assert "status <> 'void'" in s and "stock_adjustments.id" in s       # cakupan sama dgn sum_total (void dikecualikan), sumber = baris


def test_tanpa_with_totals_tak_ada_sum_signed_dan_satu_agregat_per_halaman(monkeypatch):
    k = Kon()
    h = _daftar(monkeypatch, k, with_totals=False)
    assert "sum_total_signed" not in h or h["sum_total_signed"] is None
    assert len([x for x in k.sql if "GROUP BY stock_adjustment_id" in x]) == 1       # tanpa N+1
