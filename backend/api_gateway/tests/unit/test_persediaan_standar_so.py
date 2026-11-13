"""Persediaan standar SO gelombang 2 (kontrak backend/docs/KONTRAK-persediaan-standar-so-20261109.md): penentu bersama penyesuaian stok
(pratinjau == tulis), putusan pemilik 9 Nov (gudang dipilih/bawaan utama + stok per gudang, harga draf + isyarat, void cek periode tanggal
dokumen), gudang (stok_minus, satu gudang utama, nonaktif dari ledger bersaring tenant). Paritas jurnal/ledger lama vs baru = uji salinan."""
import asyncio
import datetime as dt
import os
import re
import uuid
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import stock_adjustments as SA  # noqa: E402
from app.routers import warehouses as WH  # noqa: E402
from app.schemas.stock_adjustments import CreateStockAdjustmentRequest, VoidStockAdjustmentRequest  # noqa: E402
from app.services import gudang_daftar as GD  # noqa: E402
from app.services import stok_sesuai as SS  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

T = "kaos-biru-konveksi"
SA_ID, G_UTAMA, G_LAIN, P1, P2, AK_PERS, AK_ADJ, JE = (uuid.uuid4() for _ in range(8))
HARI = dt.date(2026, 11, 9)
APP = Path(SS.__file__).resolve().parents[1]


class Kon:
    """Tiruan asyncpg; jawaban per potongan SQL. Mencatat SQL + argumen; placeholder tertinggi WAJIB == jumlah argumen."""

    def __init__(self, **kw):
        self.sa = {"id": SA_ID, "tenant_id": T, "adjustment_number": "SA-2611-0001", "adjustment_date": dt.date(2026, 11, 5),
                   "adjustment_type": "decrease", "status": "draft", "warehouse_id": None, "journal_id": None, "storage_location_id": None,
                   "total_value": D("3000"), **kw.pop("sa", {})}
        self.items = kw.pop("items", [
            {"product_id": P1, "product_code": "K1", "product_name": "Kaos", "quantity_adjustment": D("-3"), "unit_cost": D("1000"),
             "total_value": D("3000"), "line_number": 1}])
        self.periode = kw.pop("periode", {"id": 1, "period_name": "Nov 2026", "status": "OPEN"})
        self.periode_per_tgl = kw.pop("periode_per_tgl", {})
        self.stok = kw.pop("stok", D("2"))
        self.wac = kw.pop("wac", D("1000"))
        self.gudang_row = kw.pop("gudang", {"id": G_UTAMA, "name": "Gudang Utama", "is_active": True})
        self.akun = kw.pop("akun", True)
        self.ledger_asal = kw.pop("ledger_asal", [{"product_id": P1, "product_name": "Kaos", "warehouse_id": G_UTAMA, "masuk": D("0"),
                                                  "keluar": D("3")}])
        self.log, self.exe = [], []

    @staticmethod
    def _cocok(sql, a):
        n = max([int(x) for x in re.findall(r"\$(\d+)", sql)] or [0])
        assert n == len(a), (n, len(a), sql[:160])

    def transaction(self, **kw):
        kon = self

        class Tx:
            async def __aenter__(self):
                kon.log.append(("TX", kw))
                return kon

            async def __aexit__(self, *e):
                return False
        return Tx()

    async def fetchrow(self, sql, *a):
        self._cocok(sql, a)
        self.log.append((sql, a))
        if "FROM stock_adjustments WHERE id = $1" in sql:
            return self.sa
        if "FROM fiscal_periods" in sql:
            return self.periode_per_tgl.get(a[1], self.periode)
        if "FROM warehouses WHERE" in sql:
            return self.gudang_row
        if "FROM products p" in sql:
            return {"id": a[0], "name": "Kaos", "unit": "pcs", "code": "K1", "purchase_price": D("900")}
        if "FROM products WHERE id = $1" in sql:
            return {"item_type": "goods", "track_inventory": True}
        return None

    async def fetchval(self, sql, *a):
        self._cocok(sql, a)
        self.log.append((sql, a))
        if "COUNT(*)" in sql:
            return 0
        if "SELECT average_cost FROM inventory_ledger" in sql:
            return self.wac
        if "warehouse_id = $3" in sql:
            return self.stok
        if "SUM(quantity_in) - SUM(quantity_out)" in sql:
            return D("50")  # stok SEMUA gudang (dulu dipakai; kini tidak)
        if "nomor_jurnal" in sql or "journal_number = $2" in sql:
            return None
        return None

    async def fetch(self, sql, *a):
        self._cocok(sql, a)
        self.log.append((sql, a))
        if "FROM stock_adjustment_items" in sql and "FROM stock_adjustments" not in sql:   # BUG-197: daftar kini memuat subkueri baris untuk ?search= (bukan pembacaan baris dokumen)
            return self.items
        if "FROM journal_lines" in sql:
            return [{"account_id": AK_ADJ, "debit": D("3000"), "credit": D("0"), "memo": "x"},
                    {"account_id": AK_PERS, "debit": D("0"), "credit": D("3000"), "memo": "y"}]
        if "FROM inventory_ledger" in sql and "GROUP BY 1, 2, 3" in sql:
            return self.ledger_asal
        return []

    async def execute(self, sql, *a):
        self._cocok(sql, a)
        self.exe.append((" ".join(sql.split()), a))
        return "OK"


@pytest.fixture(autouse=True)
def _pasang(monkeypatch):
    from app.services import role_resolver as RR
    from app.services.role_resolver import AccountRoleUnmappedError

    async def peran(conn, tid, role):
        if not getattr(conn, "akun", True):
            raise AccountRoleUnmappedError("x")
        return AK_PERS if "MERCHANDISE" in str(role) else AK_ADJ

    async def hari(*a):
        return HARI
    monkeypatch.setattr(SS, "resolve_account_id_by_role", peran)
    monkeypatch.setattr("app.utils.tanggal_tenant.tanggal_dokumen", hari)
    monkeypatch.setattr(SA, "tanggal_dokumen", hari)
    _ = RR


def jalan(aw):
    return asyncio.run(aw)


# ---------------------------------------------------------------- teks + kunci
def test_teks_dan_kunci_setara():
    assert SS.position_text("draft", "decrease") == "Draf · belum diposting"
    assert SS.position_text("posted", "recount") == "Diposting · Hitung ulang" and SS.position_text("void", "x") == "Dibatalkan"
    assert SS.aksi_semua("draft") == ["draf"] and SS.aksi_semua("posted") == []
    assert SS.sql_aksi("draf", "$2", "sa") == "sa.status = 'draft'" and "$2" not in SS.sql_aksi("draf", "$2")
    assert "sa.status <> 'void'" in SS.sql_aksi("bulan_ini", "$2", "sa") and "$2::date" in SS.sql_aksi("bulan_ini", "$2", "sa")
    assert SS.documents_baris("void", JE) == [{"type": "stock_adjustment"}, {"type": "journal"}, {"type": "journal"}]


# ---------------------------------------------------------------- putusan 2: gudang + stok per gudang
def test_gudang_bawaan_utama_lalu_tertua():
    k = Kon()
    g = jalan(SS.gudang(k, T, None, Blok(True)))
    sql = next(s for s, _ in k.log if "FROM warehouses" in s)
    assert g["id"] == G_UTAMA and "ORDER BY is_default DESC, created_at" in sql and "is_active" in sql


def test_gudang_dipilih_tak_ada_atau_nonaktif_diblok():
    k = Kon(gudang=None)
    bl = Blok(True)
    jalan(SS.gudang(k, T, str(G_LAIN), bl))
    assert bl.daftar[0]["code"] == "GUDANG_TIDAK_DITEMUKAN"
    bl = Blok(True)
    jalan(SS.gudang(Kon(gudang={"id": G_LAIN, "name": "Gudang B", "is_active": False}), T, str(G_LAIN), bl))
    assert bl.daftar[0]["code"] == "GUDANG_NONAKTIF"


def test_baris_draf_stok_sebelum_per_gudang_dan_hitung_ulang():
    k = Kon(stok=D("7"))
    item = SimpleNamespace(product_id=str(P1), quantity_adjustment=D("0"), physical_quantity=D("4"), reason_detail=None)
    b = jalan(SS.susun_baris(k, T, [item], "recount", G_UTAMA, Blok(True)))[0]
    assert (b["quantity_before"], b["quantity_adjustment"], b["quantity_after"]) == (D("7"), D("-3"), D("4"))  # bukan 50 semua gudang
    assert b["total_value"] == D("3000") and b["unit_cost"] == 1000
    assert any("warehouse_id = $3" in s and a == (T, P1, G_UTAMA) for s, a in k.log if isinstance(s, str))


def test_rencana_buat_memakai_gudang_dan_blok_barang():
    body = CreateStockAdjustmentRequest(adjustment_date=HARI, adjustment_type="increase",
                                        items=[{"product_id": str(P1), "quantity_adjustment": D("2")}])
    r = jalan(SS.rencana_buat(Kon(), T, body, Blok(True)))
    assert r["blocks"] == [] and r["gudang"]["id"] == G_UTAMA and r["baris_draf"][0]["quantity_after"] == D("4")
    bodi = CreateStockAdjustmentRequest(adjustment_date=HARI, adjustment_type="increase", items=[{"product_id": "bukan-uuid", "quantity_adjustment": 1}])
    with pytest.raises(GalatBerkode) as e:
        jalan(SS.rencana_buat(Kon(), T, bodi, Blok(False)))
    assert e.value.code == "BARANG_TIDAK_DITEMUKAN"


# ---------------------------------------------------------------- posting
def test_posting_jurnal_gerak_dan_isyarat():
    k = Kon(stok=D("2"), wac=D("1200"))  # keluar 3 dari 2 -> minus (isyarat); WAC kini 1.200 vs draf 1.000 (isyarat, putusan 1)
    r = jalan(SS.rencana_posting(k, T, SA_ID, Blok(True)))
    assert r["blocks"] == [] and r["total"] == D("3000")
    assert r["baris"] == [(AK_ADJ, D("3000"), D("0"), "Penyesuaian Persediaan - SA-2611-0001"),
                          (AK_PERS, D("0"), D("3000"), "Pengurangan Persediaan - SA-2611-0001")]
    g = r["gerak"][0]
    assert (g["keluar"], g["stok_sebelum"], g["stok_sesudah"]) == (D("3"), D("2"), D("-1")) and r["gudang"]["id"] == G_UTAMA
    assert any("akan minus" in x for x in r["isyarat"]) and any("Rp 1.000 → Rp 1.200" in x for x in r["isyarat"])
    assert not any("FOR UPDATE" in s for s, _ in k.log if isinstance(s, str))


@pytest.mark.parametrize("kw,kode,status", [
    ({"sa": {"status": "posted"}}, "PENYESUAIAN_BUKAN_DRAF", 400),
    ({"periode": None}, "PERIODE_TIDAK_ADA", 400),
    ({"periode": {"id": 1, "period_name": "Nov 2026", "status": "CLOSED"}}, "PERIODE_TERTUTUP", 400),
    ({"items": []}, "TANPA_BARIS", 400),
    ({"akun": False}, "AKUN_BELUM_DIATUR", 400),
    ({"gudang": None}, "GUDANG_TIDAK_ADA", 400),
])
def test_posting_blok_pratinjau_sama_dengan_tulis(kw, kode, status):
    r = jalan(SS.rencana_posting(Kon(**kw), T, SA_ID, Blok(True)))
    assert r["blocks"][0]["code"] == kode
    with pytest.raises(GalatBerkode) as e:
        jalan(SS.rencana_posting(Kon(**kw), T, SA_ID, Blok(False), kunci=True))
    assert (e.value.code, e.value.status_code) == (kode, status)


# ---------------------------------------------------------------- batal (putusan 3)
def test_batal_periode_tanggal_dokumen_ditutup_diblok():
    tutup = {"id": 2, "period_name": "Okt 2026", "status": "CLOSED"}
    k = Kon(sa={"status": "posted", "journal_id": JE, "adjustment_date": dt.date(2026, 10, 30)},
            periode_per_tgl={dt.date(2026, 10, 30): tutup})
    r = jalan(SS.rencana_batal(k, T, SA_ID, "salah", Blok(True)))
    assert [b["code"] for b in r["blocks"]] == ["PERIODE_TERTUTUP"]  # hari ini terbuka; tanggal dokumen tertutup -> blok (dulu lolos)


def test_batal_pembalik_cermin_dan_draf_dihapus():
    r = jalan(SS.rencana_batal(Kon(sa={"status": "posted", "journal_id": JE}, stok=D("0")), T, SA_ID, "salah", Blok(True)))
    assert r["blocks"] == [] and r["baris"][0] == (AK_ADJ, D("0"), D("3000"), "Reversal - x")
    assert (r["gerak"][0]["masuk"], r["gerak"][0]["stok_sesudah"]) == (D("3"), D("3"))
    d = jalan(SS.rencana_batal(Kon(), T, SA_ID, "x", Blok(True)))
    assert d["hapus_draf"] and d["catatan"] == "Draf akan dihapus (belum berjurnal)." and d["baris"] == []
    v = jalan(SS.rencana_batal(Kon(sa={"status": "void"}), T, SA_ID, "x", Blok(True)))
    assert v["blocks"][0]["code"] == "PENYESUAIAN_SUDAH_DIBATALKAN"


def test_aksi_tersedia_urutan_dan_alasan():
    a = jalan(SS.aksi_tersedia(Kon(), T, Kon().sa))
    assert list(a) == ["posting", "ubah", "hapus", "batalkan"] and a["posting"]["tersedia"] and a["batalkan"]["alasan"] == \
        "Draf dihapus, bukan dibatalkan."


# ---------------------------------------------------------------- tulis == penentu
class Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class Cm:
            async def __aenter__(self):
                return k

            async def __aexit__(self, *e):
                return False
        return Cm()


def req(h=None):
    return SimpleNamespace(headers=h or {}, state=SimpleNamespace(user={"tenant_id": T, "user_id": str(uuid.uuid4())}))


def _pool(monkeypatch, k):
    async def pool():
        return Pool(k)

    async def pre(*a, **kw):
        return None
    monkeypatch.setattr(SA, "get_pool", pool)
    monkeypatch.setattr(SA, "_ensure_stock_adj_role_preconditions", pre)


def test_tulis_posting_jurnal_ledger_gudang_dari_penentu(monkeypatch):
    k = Kon(stok=D("5"))
    _pool(monkeypatch, k)
    h = jalan(SA.post_stock_adjustment(req(), SA_ID))
    assert h["data"]["status"] == "posted" and h["data"]["journal_number"] == "SA-2611-0001"  # nomor dokumen (putusan MASTER)
    jl = [(a[3], a[4], a[5]) for s, a in k.exe if s.startswith("INSERT INTO journal_lines")]
    assert jl == [(AK_ADJ, D("3000"), D("0")), (AK_PERS, D("0"), D("3000"))]
    il = [a for s, a in k.exe if s.startswith("INSERT INTO inventory_ledger")]
    assert len(il) == 1 and il[0][12] == G_UTAMA and il[0][7] == D("3")  # gudang utama, qty keluar 3
    up = [a for s, a in k.exe if s.startswith("UPDATE stock_adjustments SET status = 'posted'")]
    assert up and up[0][3] == G_UTAMA and up[0][4] == T


def test_tulis_posting_idempoten_replay(monkeypatch):
    k = Kon()
    _pool(monkeypatch, k)

    async def lama(*a, **kw):
        return "kp", "sd", {"success": True, "replay": True}
    monkeypatch.setattr(SA.idem_buat, "mulai_aksi", lama)
    assert jalan(SA.post_stock_adjustment(req({"X-Idempotency-Key": "k"}), SA_ID)) == {"success": True, "replay": True}
    assert [s for s, _ in k.exe if not s.startswith("SELECT pg_advisory_xact_lock")] == []


def test_tulis_void_blok_periode_dokumen(monkeypatch):
    k = Kon(sa={"status": "posted", "journal_id": JE, "adjustment_date": dt.date(2026, 10, 30)},
            periode_per_tgl={dt.date(2026, 10, 30): {"id": 2, "period_name": "Okt 2026", "status": "LOCKED"}})
    _pool(monkeypatch, k)
    with pytest.raises(GalatBerkode) as e:
        jalan(SA.void_stock_adjustment(req(), SA_ID, VoidStockAdjustmentRequest(reason="salah")))
    assert e.value.code == "PERIODE_TERTUTUP" and not any(s.startswith("INSERT") for s, _ in k.exe)


@pytest.mark.parametrize("aksi", [None, "draf", "bulan_ini"])
def test_daftar_placeholder_cocok(monkeypatch, aksi):
    k = Kon()
    _pool(monkeypatch, k)
    h = jalan(SA.list_stock_adjustments(req(), status="all", adjustment_type=None, search="SA 26", date_from=None, date_to=None, skip=0, limit=20,
                                        sort_by="created_at", sort_order="desc", aksi=aksi, with_totals=True))
    assert h["items"] == [] and "sum_total" in h


# ---------------------------------------------------------------- gudang
def test_gudang_teks_alasan_dan_predikat():
    assert GD.position_text(True, True) == "Utama" and GD.position_text(True, False, True) == "Aktif · cabang"
    assert GD.position_text(False, True) == "Nonaktif"
    assert GD.alasan_nonaktif(True, 2).startswith("Gudang masih berisi stok (2 barang)") and GD.alasan_nonaktif(True, 0) is None
    assert GD.alasan_utama(True, True) == "Gudang ini sudah gudang utama." and GD.alasan_utama(True, False) is None
    s = GD.sql_stok_minus("w")
    assert "il.tenant_id = w.tenant_id" in s and "< 0" in s and GD.aksi_semua(True) == ["stok_minus"]


def test_jadikan_utama_penentu_sama_dan_bersaring_tenant():
    """Satu gudang utama dijaga trigger DB trg_single_default_warehouse (klaim U8 gugur di uji salinan 9 Nov); set-default memakai penentu
    yang SAMA dengan aksi_tersedia.jadikan_utama (gudang nonaktif ditolak) dan UPDATE bersaring tenant."""
    assert GD.alasan_utama(False, False) == "Gudang nonaktif tidak bisa jadi gudang utama."
    src = (APP / "routers/warehouses.py").read_text()
    assert 'GD.alasan_utama(existing["is_active"], False)' in src
    assert '"UPDATE warehouses SET is_default = true WHERE id = $1 AND tenant_id = $2"' in src and not hasattr(GD, "jadikan_utama")


def test_nonaktif_gudang_dari_ledger_bersaring_tenant():
    k = Kon()
    jalan(GD.jumlah_berstok(k, T, G_UTAMA))
    s, a = k.log[0]
    assert "FROM inventory_ledger WHERE tenant_id = $1 AND warehouse_id = $2" in s and "<> 0" in s and a == (T, G_UTAMA)
    src = (APP / "routers/warehouses.py").read_text()
    assert "FROM warehouse_stock WHERE warehouse_id = $1 AND quantity > 0" not in src and src.count("GD.alasan_nonaktif(") == 2


# ---------------------------------------------------------------- izin
@pytest.mark.parametrize("jalur,metode,harap", [
    (f"/api/stock-adjustments/{SA_ID}/edit/preview", "POST", ("item", "U")),
    (f"/api/stock-adjustments/{SA_ID}/delete/preview", "POST", ("item", "D")),
    (f"/api/stock-adjustments/{SA_ID}/post/preview", "POST", ("stock_adjust", "C")),
    (f"/api/stock-adjustments/{SA_ID}/void/preview", "POST", ("stock_adjust", "C")),
    ("/api/stock-adjustments/preview", "POST", ("stock_adjust", "C")),
    (f"/api/stock-adjustments/{SA_ID}/history", "GET", ("stock_adjust", "R")),
    ("/api/warehouses/summary", "GET", ("warehouse", "R")),
])
def test_izin_pratinjau_sama_dengan_tulis(jalur, metode, harap):
    from app.middleware.permission_middleware import PermissionMiddleware
    assert PermissionMiddleware(app=None)._find_permission(jalur, metode) == harap
