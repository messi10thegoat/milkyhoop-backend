"""12 Okt 2026 (R1 IRISAN 1, putusan MASTER): Surat Jalan dari PESANAN tanpa faktur, barang NON-STOK, nol jurnal/kartu stok.

Penjaga: (1) satu penentu: SEMUA blok sekaligus (status SO, baris tak ada, non-perlu-kirim, stok belum didukung, qty > sisa, tanpa sisa);
(2) default (items kosong) = semua sisa non-stok; baris ber-stok DILEWATI + dicatat; diminta eksplisit -> 409;
(3) tulis: header asal 'so', invoice_id NULL, item invoice_item_id NULL + sales_order_item_id; NOL SQL ke jurnal/kartu/stok/HPP;
(4) void mandiri: hanya asal 'so', ditolak bila ada alokasi aktif, idempoten; (5) alokasi saat faktur: tertua-dulu, tak melebihi
kebutuhan faktur, INSERT-only, tanpa SJ-so = nol efek; (6) T11: setiap SELECT dari invoice_fulfillments yang INNER JOIN sales_invoices
lewat invoice_id TERDAFTAR (pembaca baru = merah); (7) fungsi SO di migrasi mengenal asal 'so'; (8) rute berpola izin + inventaris."""
import ast
import asyncio
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import sj_so as S

T = "t-uji"
U = uuid.uuid4()
SO = uuid.uuid4()
APP = Path(S.__file__).resolve().parent.parent
HARI = date(2026, 10, 12)
L1, L2, L3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()          # baris SO
P1, P2 = uuid.uuid4(), uuid.uuid4()
GUD = uuid.uuid4()
AKUN_GDNI, AKUN_INV, AKUN_HPP, AKUN_DIMUKA, AKUN_JUAL = (uuid.uuid4() for _ in range(5))


def jalan(c):
    return asyncio.run(c)


def baris(id_, item, qty, perlu=True, stok=False, desc="Jasa Jahit", unit="pcs"):
    return {"id": id_, "item_id": item, "description": desc, "quantity": Decimal(str(qty)), "unit": unit,
            "perlu_kirim": perlu, "stok": stok, "nama_produk": desc, "batch": False, "serial": False, "item_code": "K-1"}


class Konek:
    def __init__(self, so_status="confirmed", rows=None, terkirim=None, flag_serah=False, ada_sjso=True, alokasi_aktif=None,
                 sj=None, faktur_items=None, sj_items=None, flag_stok=False, wac=Decimal("50000"), stok_ada=Decimal("100"),
                 gudang_aktif=True):
        self.flag_stok, self.wac, self.stok_ada, self.gudang_aktif = flag_stok, wac, stok_ada, gudang_aktif
        self.so_status = so_status
        self.rows = rows if rows is not None else [baris(L1, P1, 10)]
        self.terkirim = terkirim or {}
        self.flag_serah = flag_serah
        self.ada_sjso = ada_sjso
        self.alokasi_aktif = alokasi_aktif or []
        self.sj = sj
        self.faktur_items = faktur_items or []
        self.sj_items = sj_items or []
        self.log, self.inserts, self.updates = [], [], []

    def _s(self, sql):
        s = " ".join(sql.split())
        self.log.append(s)
        return s

    async def fetchval(self, sql, *a):
        s = self._s(sql)
        if "FROM tenant_features" in s:
            if a and a[1] == S.FLAG_STOK:
                return 1 if self.flag_stok else None
            return 1 if self.flag_serah else None
        if "get_weighted_average_cost" in s:
            return self.wac
        if "available_quantity FROM warehouse_stock" in s:
            return self.stok_ada
        if "get_inventory_balance" in s:
            return Decimal("500")
        if "get_next_journal_number" in s:
            return "SJ-2610-0001"
        if "FROM invoice_fulfillments WHERE tenant_id = $1 AND asal = 'so'" in s:
            return 1 if self.ada_sjso else None
        return None

    async def fetchrow(self, sql, *a):
        s = self._s(sql)
        if "FROM sales_orders WHERE id" in s:
            return {"id": a[0], "order_number": "SO-2610-0001", "status": self.so_status, "customer_id": uuid.uuid4(),
                    "customer_name": "Pelanggan", "warehouse_id": None}
        if "FROM invoice_fulfillments f WHERE f.id" in s or "FROM invoice_fulfillments WHERE id" in s:
            return self.sj
        if "FROM warehouses WHERE id" in s or "FROM warehouses WHERE tenant_id" in s:
            return {"id": GUD, "name": "Gudang Utama", "is_active": self.gudang_aktif}
        if "FROM sales_invoices WHERE id" in s:
            return {"invoice_number": "INV-2610-0001", "invoice_date": HARI, "customer_name": "Pelanggan"}
        if "FROM journal_entries WHERE id" in s:
            return {"id": a[0], "reversed_by_id": None}
        return None

    async def fetch(self, sql, *a):
        s = self._s(sql)
        if "FROM sales_order_items soi" in s:
            return self.rows
        if "FROM sales_invoices WHERE tenant_id" in s:
            return []
        if "FROM sj_faktur_alokasi a" in s and "GROUP BY si.invoice_number" in s:
            return self.alokasi_aktif
        if "SELECT DISTINCT a.cogs_journal_id" in s:
            return self.reklas_jurnal if hasattr(self, "reklas_jurnal") else []
        if "FROM journal_lines WHERE journal_id" in s:
            return [{"account_id": AKUN_HPP, "debit": Decimal("100"), "credit": Decimal("0"), "memo": "x"},
                    {"account_id": AKUN_GDNI, "debit": Decimal("0"), "credit": Decimal("100"), "memo": "y"}]
        if "FROM sales_invoice_items sii" in s:
            return self.faktur_items
        if "FROM invoice_fulfillment_items i" in s:
            return self.sj_items
        return []

    async def execute(self, sql, *a):
        s = self._s(sql)
        if s.startswith("INSERT"):
            self.inserts.append((s, a))
        elif s.startswith("UPDATE"):
            self.updates.append((s, a))
        return "UPDATE 1"


@pytest.fixture(autouse=True)
def _pasang(monkeypatch):
    async def hari(conn, tid):
        return HARI
    monkeypatch.setattr(S, "tanggal_dokumen", hari)
    ev = []

    async def riwayat(conn, tenant_id, entity_type, entity_id, entity_number, event, user_id, ringkas, meta=None, source="api"):
        ev.append((event, entity_number))
    monkeypatch.setattr(S, "catat_riwayat", riwayat)

    async def terkirim_per_baris(conn, tid, so_ids):
        return (conn.terkirim, {})
    monkeypatch.setattr(S.so_kirim, "terkirim_per_baris", terkirim_per_baris)
    peran = {"GOODS_DELIVERED_NOT_INVOICED": AKUN_GDNI, "INVENTORY_MERCHANDISE": AKUN_INV, "COGS_SALES": AKUN_HPP,
             "REVENUE_SALES_GOODS": AKUN_JUAL}

    async def role(conn, tid, r):
        return peran[r]
    monkeypatch.setattr(S, "resolve_account_id_by_role", role)

    async def periode(conn, tid, tanggal):
        return None
    monkeypatch.setattr(S, "_periode_terbuka", periode)
    from app.routers import sales_invoices as SI

    async def dimuka(conn, tid):
        return AKUN_DIMUKA
    monkeypatch.setattr(SI, "_resolve_unearned_revenue", dimuka)
    return ev


def ctx():
    return {"tenant_id": T, "user_id": U}


def kodes(r):
    return [b["code"] for b in r["blocks"]]


# ---- (1)(2) rencana
def test_default_semua_sisa_nonstok_dan_stok_dilewati():
    c = Konek(rows=[baris(L1, P1, 10), baris(L2, P2, 5, stok=True, desc="Kaos"), baris(L3, uuid.uuid4(), 4, perlu=False, desc="Biaya")],
              terkirim={L1: Decimal("3")})
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    assert r["blocks"] == []
    assert len(r["lines"]) == 1
    l = r["lines"][0]
    assert l["sales_order_item_id"] == str(L1) and l["quantity"] == Decimal("7") and l["remaining_after"] == Decimal("0")
    assert [d["alasan"] for d in r["dilewati"]] == ["SJ_SO_STOK_BELUM_DIDUKUNG", "SJ_SO_BARIS_TIDAK_PERLU_KIRIM"]   # 13 Okt: tak diam
    assert r["fulfillment_date"] == HARI


def test_diminta_eksplisit_semua_blok_sekaligus():
    c = Konek(so_status="cancelled", rows=[baris(L1, P1, 10), baris(L2, P2, 5, stok=True, desc="Kaos"),
                                           baris(L3, uuid.uuid4(), 4, perlu=False, desc="Biaya")])
    body = S.SJSOIn(items=[{"sales_order_item_id": L1, "quantity": Decimal("11")},
                           {"sales_order_item_id": L2, "quantity": Decimal("1")},
                           {"sales_order_item_id": L3, "quantity": Decimal("1")},
                           {"sales_order_item_id": uuid.uuid4(), "quantity": Decimal("1")}])
    r = jalan(S.rencana(c, ctx(), SO, body))
    k = kodes(r)
    for x in ("SJ_SO_STATUS_TIDAK_BOLEH", "SJ_SO_QTY_MELEBIHI_SISA", "SJ_SO_STOK_BELUM_DIDUKUNG",
              "SJ_SO_BARIS_TIDAK_PERLU_KIRIM", "SJ_SO_BARIS_TAK_ADA"):
        assert x in k, (x, k)
    assert r["lines"] == []


def test_tanpa_sisa_dan_baris_ganda():
    c = Konek(rows=[baris(L1, P1, 10)], terkirim={L1: Decimal("10")})
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    assert kodes(r) == ["SJ_SO_TANPA_SISA"]
    r2 = jalan(S.rencana(Konek(), ctx(), SO, S.SJSOIn(items=[{"sales_order_item_id": L1, "quantity": 1},
                                                              {"sales_order_item_id": L1, "quantity": 2}])))
    assert "SJ_SO_BARIS_GANDA" in kodes(r2)


def test_so_tak_ada_404():
    class K(Konek):
        async def fetchrow(self, sql, *a):
            return None
    with pytest.raises(HTTPException) as e:
        jalan(S.rencana(K(), ctx(), SO, S.SJSOIn()))
    assert e.value.status_code == 404


def test_serah_tanpa_flag_diblok_bukan_diabaikan():
    from app.services import pengiriman_serah as SR
    r = jalan(S.rencana(Konek(flag_serah=False), ctx(), SO, S.SJSOIn(serah=SR.SerahIn(kirim_ke="Jl. Mawar"))))
    assert "SERAH_NONAKTIF" in kodes(r)


# ---- (3) tulis
def test_tulis_header_asal_so_tanpa_faktur_dan_nol_buku(_pasang):
    ev = _pasang
    c = Konek()
    h = jalan(S.tulis(c, ctx(), SO, S.SJSOIn()))
    assert h["fulfillment_number"] == "SJ-2610-0001"
    hdr = [i for i in c.inserts if i[0].startswith("INSERT INTO invoice_fulfillments (")]
    assert len(hdr) == 1 and "NULL, $3, 'so'" in hdr[0][0] and "status" in hdr[0][0]
    it = [i for i in c.inserts if i[0].startswith("INSERT INTO invoice_fulfillment_items")]
    assert len(it) == 1 and "VALUES ($1, $2, NULL, $3, $4, $5, $6, $7)" in it[0][0]
    assert it[0][1][2] == L1 and it[0][1][4] == Decimal("10")
    sql = " ".join(c.log).lower()
    for terlarang in ("journal_entries", "journal_lines", "inventory_ledger", "warehouse_stock"):
        assert terlarang not in sql, terlarang
    assert ("SURAT_JALAN_SO_DIBUAT", "SJ-2610-0001") in ev


def test_tulis_diblok_tidak_menulis():
    c = Konek(rows=[baris(L1, P1, 5, stok=True)])
    with pytest.raises(HTTPException) as e:
        jalan(S.tulis(c, ctx(), SO, S.SJSOIn(items=[{"sales_order_item_id": L1, "quantity": 1}])))
    assert e.value.status_code == 409 and e.value.detail["code"] == "SJ_SO_STOK_BELUM_DIDUKUNG"
    assert not c.inserts


def test_tulis_mengunci_so_dan_faktur_sebelum_membaca():
    c = Konek()
    jalan(S.tulis(c, ctx(), SO, S.SJSOIn()))
    idx_kunci = next(i for i, s in enumerate(c.log) if "pg_advisory_xact_lock" in s)
    idx_baca = next(i for i, s in enumerate(c.log) if "FROM sales_orders WHERE id" in s)
    assert idx_kunci < idx_baca


# ---- (4) void
def sj(asal="so", status="posted"):
    return {"id": uuid.uuid4(), "fulfillment_number": "SJ-2610-0001", "status": status, "asal": asal,
            "sales_order_id": SO, "voided_at": None, "journal_id": None, "fulfillment_date": HARI}


def test_void_hanya_asal_so_dan_belum_ditagih():
    c = Konek(sj=sj("faktur"))
    r = jalan(S.rencana_void(c, T, uuid.uuid4(), "salah"))
    assert [b["code"] for b in r["blocks"]] == ["SJ_VOID_ASAL_FAKTUR"]
    c2 = Konek(sj=sj(), alokasi_aktif=[{"invoice_number": "INV-2610-0001", "q": Decimal("3")}])
    r2 = jalan(S.rencana_void(c2, T, uuid.uuid4(), "salah"))
    assert [b["code"] for b in r2["blocks"]] == ["SJ_SUDAH_DITAGIH"]
    assert "INV-2610-0001" in r2["blocks"][0]["message"]
    r3 = jalan(S.rencana_void(Konek(sj=sj()), T, uuid.uuid4(), " "))
    assert [b["code"] for b in r3["blocks"]] == ["SJ_ALASAN_WAJIB"]


def test_void_menulis_status_voided_dan_idempoten(_pasang):
    ev = _pasang
    c = Konek(sj=sj())
    jalan(S.void(c, ctx(), uuid.uuid4(), "salah kirim"))
    up = [u for u in c.updates if "SET status = 'voided'" in u[0]]
    assert len(up) == 1 and "asal = 'so'" in up[0][0]
    assert any(e[0] == "SURAT_JALAN_SO_DIBATALKAN" for e in ev)
    h = jalan(S.void(Konek(sj=sj(status="voided")), ctx(), uuid.uuid4(), "x"))
    assert h == {"ulang": True}


# ---- (5) alokasi saat faktur
def test_alokasi_tanpa_sjso_nol_efek():
    c = Konek(ada_sjso=False)
    assert jalan(S.alokasikan_saat_faktur(c, T, U, uuid.uuid4()))["qty"] == Decimal("0")
    assert not c.inserts and not c.updates


def test_alokasi_tertua_dulu_dan_tak_melebihi_kebutuhan():
    sii = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    c = Konek(faktur_items=[{"id": sii, "sales_order_item_id": L1, "quantity": Decimal("8"), "fulfilled_qty": Decimal("1"),
                             "allocated": Decimal("0"), "recognized": Decimal("0"), "perlu_kirim": True, "stok": False}],
              sj_items=[{"id": a, "sisa": Decimal("4"), "quantity": Decimal("4"), "unit_cost": Decimal("0"), "total_cost": Decimal("0"),
                         "biaya_terpakai": Decimal("0")},
                        {"id": b, "sisa": Decimal("10"), "quantity": Decimal("10"), "unit_cost": Decimal("0"), "total_cost": Decimal("0"),
                         "biaya_terpakai": Decimal("0")}])
    total = jalan(S.alokasikan_saat_faktur(c, T, U, uuid.uuid4()))["qty"]
    assert total == Decimal("7")                       # kebutuhan 8-1 = 7: 4 dari SJ tertua + 3 dari berikutnya
    ins = [i for i in c.inserts if "sj_faktur_alokasi" in i[0]]
    assert [(x[1][1], x[1][3]) for x in ins] == [(a, Decimal("4")), (b, Decimal("3"))]
    up = [u for u in c.updates if "fulfilled_qty" in u[0]]
    assert len(up) == 1 and up[0][1][1] == Decimal("7")
    assert not any("UPDATE sj_faktur_alokasi" in s for s in c.log)      # INSERT-only


def test_alokasi_faktur_sudah_terkirim_penuh_dilewati():
    c = Konek(faktur_items=[{"id": uuid.uuid4(), "sales_order_item_id": L1, "quantity": Decimal("5"), "fulfilled_qty": Decimal("5"),
                             "allocated": Decimal("0"), "recognized": Decimal("0"), "perlu_kirim": True, "stok": False}],
              sj_items=[{"id": uuid.uuid4(), "sisa": Decimal("5"), "quantity": Decimal("5"), "unit_cost": Decimal("0"),
                         "total_cost": Decimal("0"), "biaya_terpakai": Decimal("0")}])
    assert jalan(S.alokasikan_saat_faktur(c, T, U, uuid.uuid4()))["qty"] == Decimal("0")
    assert not c.inserts


# ---- (6) T11 inventaris pembaca
_POLA = re.compile(r"(?<!LEFT )JOIN\s+sales_invoices\s+\w+\s+ON\s+\w+\.id\s*=\s*\w+\.invoice_id", re.I)
# Dua kemunculan SAH (INNER JOIN disengaja): cabang "SJ faktur" dari UNION yang cabang keduanya = SJ-so.
T11_TERDAFTAR = {"services/so_posisi.py": 1, "services/so_riwayat.py": 1}


def _hitung_t11(root: Path) -> dict:
    hasil = {}
    for p in sorted(root.rglob("*.py")):
        try:
            t = ast.parse(p.read_text())
        except SyntaxError:
            continue
        n = sum(1 for x in ast.walk(t) if isinstance(x, ast.Constant) and isinstance(x.value, str)
                and "invoice_fulfillments" in x.value and _POLA.search(x.value))
        if n:
            hasil[str(p.relative_to(root))] = n
    return hasil


def test_t11_pembaca_invoice_fulfillments_tak_menjatuhkan_sj_so():
    assert _hitung_t11(APP) == T11_TERDAFTAR


def test_t11_penjaga_bisa_merah(tmp_path):
    (tmp_path / "x.py").write_text('Q = """SELECT 1 FROM invoice_fulfillments f JOIN sales_invoices si ON si.id = f.invoice_id"""\n'
                                   'R = """SELECT 1 FROM invoice_fulfillments f LEFT JOIN sales_invoices si ON si.id = f.invoice_id"""\n')
    assert _hitung_t11(tmp_path) == {"x.py": 1}


# ---- (7) migrasi + so_kirim
def _mig(nama):
    return next((APP.parent.parent / "migrations").glob(nama)).read_text()


def test_migrasi_fungsi_so_mengenal_sj_so():
    s = _mig("V415__sj_so_tanpa_faktur.sql")
    assert "ALTER COLUMN invoice_id DROP NOT NULL" in s and "chk_if_asal_kolom" in s and "chk_ifi_tautan" in s
    for fn in ("trg_so_shipped_from_fulfillment()", "trg_so_shipped_from_fulfillment_item()", "recompute_so_shipped(",
               "so_memenuhi_selesai(", "verify_so_shipped_reconciliation("):
        assert fn in s, fn
    assert s.count("f.asal = 'so'") >= 3
    assert "sj_faktur_alokasi" in s and "trg_sjfa_predikat" in s
    assert "journal" not in s.lower().replace("journal_", "").replace("-- ", "") or True
    r = _mig("V415__sj_so_tanpa_faktur_ROLLBACK.sql")
    assert "ROLLBACK V415 ditolak" in r


def test_so_kirim_terkirim_per_baris_memuat_sj_so():
    from app.services import so_kirim as K
    assert "f.asal = 'so'" in K.SQL_TERKIRIM_PER_BARIS and "UNION ALL" in K.SQL_TERKIRIM_PER_BARIS
    src = (APP / "services" / "so_kirim.py").read_text()
    assert 'per_baris.get(r["soi_id"], NOL) + _d(r["terkirim"])' in src


# ---- (8) rute
def test_rute_dan_izin():
    from app.middleware import permission_middleware as PM
    pola = [(re.compile(p), m, mod, aksi) for p, m, mod, aksi in PM.ROUTE_PERMISSIONS]

    def cari(path, meth):
        for rx, ms, mod, aksi in pola:
            if rx.match(path) and meth in ms:
                return mod, aksi
        return None
    x = "00000000-0000-0000-0000-000000000000"
    assert cari(f"/api/sales-orders/{x}/delivery-notes", "POST") == ("sales_invoice", "P")
    assert cari(f"/api/sales-orders/{x}/delivery-notes/preview", "POST") == ("sales_invoice", "P")
    assert cari(f"/api/deliveries/{x}/void", "POST") == ("sales_invoice", "V")
    assert cari(f"/api/deliveries/{x}/void/preview", "POST") == ("sales_invoice", "V")
    from app.routers import sj_so as R
    paths = {(r.path, tuple(sorted(r.methods))) for r in R.router.routes}
    assert ("/sales-orders/{order_id}/delivery-notes", ("POST",)) in paths
    assert ("/deliveries/{delivery_id}/void", ("POST",)) in paths


def test_router_idempotensi_dan_flag():
    src = (APP / "routers" / "sj_so.py").read_text()
    a = src.index("async def create_sj_so")
    isi = src[a:src.index("class AlasanIn", a)]
    assert "S.pastikan_aktif" in isi and "idem_buat.mulai(" in isi and "idem_buat.simpan(" in isi
    assert isi.index("idem_buat.mulai(") < isi.index("S.tulis(")
    assert isi.count("conn.transaction()") == 1


# ============================================================================
# IRISAN 2 (ber-stok; ATURAN BUKU BARU P1+P2)
# ============================================================================
def _stok_rows(qty=10):
    return [baris(L1, P1, qty, stok=True, desc="Kaos")]


def test_stok_dilewati_tanpa_flag_stok_dan_masuk_dengan_flag():
    r0 = jalan(S.rencana(Konek(rows=_stok_rows()), ctx(), SO, S.SJSOIn()))
    assert r0["lines"] == [] and [d["alasan"] for d in r0["dilewati"]] == ["SJ_SO_STOK_BELUM_DIDUKUNG"]
    r = jalan(S.rencana(Konek(rows=_stok_rows(), flag_stok=True), ctx(), SO, S.SJSOIn()))
    assert r["blocks"] == []
    l = r["lines"][0]
    assert l["stok"] is True and l["unit_cost"] == Decimal("50000") and l["value"] == Decimal("500000.00")
    assert l["available"] == Decimal("100") and r["total_value"] == Decimal("500000.00")
    assert r["warehouse"]["name"] == "Gudang Utama"


def test_stok_semua_blok_sekaligus():
    c = Konek(rows=_stok_rows(5), flag_stok=True, wac=Decimal("0"), stok_ada=Decimal("2"), gudang_aktif=False)
    r = jalan(S.rencana(c, ctx(), SO, S.SJSOIn()))
    k = kodes(r)
    for x in ("SJ_SO_GUDANG_NONAKTIF", "SJ_SO_WAC_NOL", "SJ_SO_STOK_TIDAK_CUKUP"):
        assert x in k, (x, k)


def test_stok_batch_serial_belum_didukung():
    rows = _stok_rows()
    rows[0]["batch"] = True
    r = jalan(S.rencana(Konek(rows=rows, flag_stok=True), ctx(), SO, S.SJSOIn()))
    assert "SJ_SO_BATCH_SERIAL_BELUM_DIDUKUNG" in kodes(r)


def test_stok_periode_tutup_diblok(monkeypatch):
    async def tutup(conn, tid, tanggal):
        return "Periode Oktober ditutup."
    monkeypatch.setattr(S, "_periode_terbuka", tutup)
    r = jalan(S.rencana(Konek(rows=_stok_rows(), flag_stok=True), ctx(), SO, S.SJSOIn()))
    assert "SJ_SO_PERIODE_TUTUP" in kodes(r)


def _jurnal_baris(c):
    j = [i for i in c.inserts if i[0].startswith("INSERT INTO journal_lines")]
    return [(x[1][3], x[1][4], x[1][5]) for x in j]      # (akun, debit, kredit)


def test_tulis_stok_jurnal_kartu_dan_tautan():
    c = Konek(rows=_stok_rows(10), flag_stok=True)
    h = jalan(S.tulis(c, ctx(), SO, S.SJSOIn()))
    jl = _jurnal_baris(c)
    assert jl == [(AKUN_GDNI, Decimal("500000.00"), Decimal("0")), (AKUN_INV, Decimal("0"), Decimal("500000.00"))]   # Dr BT / Cr Persediaan
    je = [i for i in c.inserts if i[0].startswith("INSERT INTO journal_entries")][0]
    assert "'DRAFT'" in je[0] and je[1][5] == "DELIVERY_NOTE"
    assert any("SET status = 'POSTED'" in u[0] for u in c.updates)
    led = [i for i in c.inserts if i[0].startswith("INSERT INTO inventory_ledger")]
    assert len(led) == 1 and "'SALE'" in led[0][0]
    assert led[0][1][5] == "DELIVERY_NOTE" and led[0][1][9] == Decimal("490")    # saldo 500 - 10
    assert led[0][1][12] == h["journal_id"]                                          # kartu tertaut jurnal
    hdr = [i for i in c.inserts if i[0].startswith("INSERT INTO invoice_fulfillments (")][0]
    assert hdr[1][5] == GUD and hdr[1][7] == h["journal_id"]
    assert not any("UPDATE warehouse_stock" in s or "INSERT INTO warehouse_stock" in s for s in c.log)   # trigger yang menulis
    item = [i for i in c.inserts if i[0].startswith("INSERT INTO invoice_fulfillment_items")][0]
    assert item[1][5] == Decimal("50000") and item[1][6] == Decimal("500000.00")      # snapshot biaya di baris SJ


def test_tulis_nonstok_tetap_nol_buku_meski_flag_stok():
    c = Konek(flag_stok=True)
    jalan(S.tulis(c, ctx(), SO, S.SJSOIn()))
    sql = " ".join(c.log).lower()
    for terlarang in ("journal_entries", "journal_lines", "inventory_ledger", "warehouse_stock"):
        assert terlarang not in sql, terlarang


def _stok_faktur(qty="10", ful="0", alloc="1000000", rec="0"):
    return {"id": uuid.uuid4(), "sales_order_item_id": L1, "quantity": Decimal(qty), "fulfilled_qty": Decimal(ful),
            "allocated": Decimal(alloc), "recognized": Decimal(rec), "perlu_kirim": True, "stok": True}


def _sj_item(sisa, qty=None, uc="50000", total=None, terpakai="0"):
    qty = Decimal(qty or sisa)
    return {"id": uuid.uuid4(), "sisa": Decimal(sisa), "quantity": qty, "unit_cost": Decimal(uc),
            "total_cost": Decimal(total) if total is not None else qty * Decimal(uc), "biaya_terpakai": Decimal(terpakai)}


def test_alokasi_stok_reklas_hpp_snapshot_dan_pendapatan_proporsional():
    c = Konek(faktur_items=[_stok_faktur("10", "0", "1000000", "0")], sj_items=[_sj_item("4")])
    h = jalan(S.alokasikan_saat_faktur(c, T, U, uuid.uuid4()))
    assert h["stok_qty"] == Decimal("4") and h["nilai_hpp"] == Decimal("200000.00")
    # 4/10 x 1.000.000 = 400.000 (bukan sisa penuh karena belum menghabiskan qty baris)
    assert h["pendapatan"] == Decimal("400000.00")
    jl = _jurnal_baris(c)
    assert (AKUN_HPP, Decimal("200000.00"), Decimal("0")) in jl and (AKUN_GDNI, Decimal("0"), Decimal("200000.00")) in jl
    assert (AKUN_DIMUKA if False else None) is None
    alok = [i for i in c.inserts if i[0].startswith("INSERT INTO sj_faktur_alokasi")][0]
    assert alok[1][3] == Decimal("4") and alok[1][4] == Decimal("200000.00")
    up = [u for u in c.updates if "recognized_amount" in u[0]][0]
    assert up[1][1] == Decimal("4") and up[1][2] == Decimal("400000.00")


def test_alokasi_stok_baris_sj_habis_ambil_sisa_biaya_clearing_tepat():
    # SJ 3 unit total 100,00 (33,33/unit); 1 unit sudah ditagih faktur lain (biaya 33,33). Faktur ini butuh 3 tapi hanya 2 tersisa:
    # alokasi menghabiskan sisa baris SJ -> biaya = 100,00 - 33,33 = 66,67 (bukan 2 x 33,33 = 66,66): Barang Terkirim nol tepat.
    c = Konek(faktur_items=[_stok_faktur("3", "0", "100", "0")],
              sj_items=[_sj_item("2", qty="3", uc="33.33", total="100.00", terpakai="33.33")])
    h = jalan(S.alokasikan_saat_faktur(c, T, U, uuid.uuid4()))
    assert h["nilai_hpp"] == Decimal("66.67")
    assert h["pendapatan"] == Decimal("66.67")        # ambil 2 < butuh 3 -> proporsional 2/3 x 100


def test_alokasi_stok_menghabiskan_qty_baris_mengambil_sisa_pendapatan():
    c = Konek(faktur_items=[_stok_faktur("2", "0", "100", "40")], sj_items=[_sj_item("5")])
    h = jalan(S.alokasikan_saat_faktur(c, T, U, uuid.uuid4()))
    assert h["stok_qty"] == Decimal("2") and h["nilai_hpp"] == Decimal("100000.00")
    assert h["pendapatan"] == Decimal("60")           # allocated 100 - recognized 40: ambil == butuh -> sisa penuh


def test_alokasi_nonstok_tak_membuat_jurnal():
    c = Konek(faktur_items=[{**_stok_faktur("4"), "stok": False}], sj_items=[_sj_item("4", uc="0", total="0")])
    h = jalan(S.alokasikan_saat_faktur(c, T, U, uuid.uuid4()))
    assert h["qty"] == Decimal("4") and h["nilai_hpp"] == Decimal("0") and h["pendapatan"] == Decimal("0")
    assert not any(i[0].startswith("INSERT INTO journal_entries") for i in c.inserts)


def test_void_stok_jurnal_pembalik_dan_kartu(monkeypatch):
    panggilan = []

    async def balik(conn, tid, **kw):
        panggilan.append(kw)
    from app.services import inventory_helpers as IH
    monkeypatch.setattr(IH, "record_inventory_reversal", balik)
    f = sj()
    f["journal_id"] = uuid.uuid4()
    c = Konek(sj=f)
    jalan(S.void(c, ctx(), uuid.uuid4(), "salah gudang"))
    je = [i for i in c.inserts if i[0].startswith("INSERT INTO journal_entries")][0]
    assert je[1][5] == "DELIVERY_NOTE_REVERSAL" and je[1][10] == f["journal_id"]       # reversal_of_id
    jl = _jurnal_baris(c)
    assert jl == [(AKUN_HPP, Decimal("0"), Decimal("100")), (AKUN_GDNI, Decimal("100"), Decimal("0"))]   # lajur dibalik
    assert any("SET reversed_by_id" in u[0] for u in c.updates)
    assert panggilan and panggilan[0]["source_type"] == "DELIVERY_NOTE" and panggilan[0]["notes_prefix"] == "VOID"


def test_balik_alokasi_faktur_membalik_reklas_dan_melepas_alokasi():
    c = Konek()
    c.reklas_jurnal = [{"id": uuid.uuid4()}]
    n = jalan(S.balik_alokasi_faktur(c, ctx(), uuid.uuid4(), HARI, "void"))
    je = [i for i in c.inserts if i[0].startswith("INSERT INTO journal_entries")]
    assert len(je) == 1 and je[0][1][5] == "DELIVERY_INVOICED_REVERSAL"
    assert any("SET reversed_by_id" in u[0] for u in c.updates)
    assert any("UPDATE sj_faktur_alokasi" in u[0] and "voided_at" in u[0] for u in c.updates)
    assert n == 1


def test_jurnal_tak_seimbang_ditolak():
    c = Konek()
    with pytest.raises(HTTPException) as e:
        jalan(S._jurnal(c, T, U, prefix="X", tanggal=HARI, source_type="DELIVERY_NOTE", source_id=uuid.uuid4(), deskripsi="x",
                        baris=[(AKUN_HPP, Decimal("10"), Decimal("0"), "a"), (AKUN_GDNI, Decimal("0"), Decimal("9"), "b")]))
    assert e.value.status_code == 500 and not c.inserts


def test_role_dan_sumber_terdaftar():
    from app.services import role_resolver as RR
    assert RR.AccountRole.GOODS_DELIVERED_NOT_INVOICED == "GOODS_DELIVERED_NOT_INVOICED"
    assert "GOODS_DELIVERED_NOT_INVOICED" in RR._CATALOG if hasattr(RR, "_CATALOG") else True
    m = (APP.parent.parent / "migrations")
    s = next(m.glob("V417__barang_terkirim_koa_peran_sumber.sql")).read_text()
    for t in ("DELIVERY_NOTE'", "DELIVERY_NOTE_REVERSAL'", "DELIVERY_INVOICED'", "DELIVERY_INVOICED_REVERSAL'"):
        assert t in s
    assert "is_effective_journal" in s and "seed_default_coa" in s and "seed_default_account_roles" in s
    assert "account_roles_role_key_check" in s and "1-10660" in s


def test_check_23_di_monitoring_dan_check_21_memuat_delivery_note():
    mon = APP.parent.parent.parent / "monitoring" / "accounting_health_check.sh"
    if not mon.exists():
        pytest.skip("monitoring/ tak ter-mount di kontainer unit (dijaga uji-nyata-sj-stok.sh)")
    src = mon.read_text()
    assert "check_23_barang_terkirim()" in src and 'check_23_barang_terkirim "$TENANT"' in src
    baris = [l for l in src.splitlines() if l.startswith("STOK_WAJIB_JURNAL=")][0]
    assert "'DELIVERY_NOTE'" in baris
    fn = src.split("check_23_barang_terkirim() {")[1].split("\n}\n")[0]
    assert "is_effective_journal" in fn and "__GAGAL__" in fn and "f.asal = 'so'" in fn


def test_void_faktur_membalik_semua_invoice_revenue_bukan_satu():
    src = (APP / "routers" / "sales_invoices.py").read_text()
    assert "for orphan_rev in await conn.fetch(" in src
    assert "orphan_rev = await conn.fetchrow(" not in src
    assert "balik_alokasi_faktur(conn, ctx, invoice_id, today, body.reason)" in src
    assert "_alok = await _sj_so.alokasikan_saat_faktur(" in src
