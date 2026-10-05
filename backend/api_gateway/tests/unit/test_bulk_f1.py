"""U1b F1 (5 Okt 2026, MASTER GO): infra aksi massal + POST /api/{modul}/bulk/export (CSV) untuk 8 modul.

Penjaga: (1) validasi id (kosong/tak valid/duplikat/batas); (2) CSV: BOM, kutip RFC 4180, anti-injeksi rumus (teks), angka negatif utuh;
(3) audit BULK_* bertenant; (4) jalankan_per_item: tiap item transaksi SENDIRI, satu ditolak/galat tak menghentikan yang lain,
replay idempotensi tak menjalankan fn, kode aksi BULK_{AKSI}; (5) DAFTAR PUTIH: rute bulk hanya export, impor/pemanggilan modul
uang tak ada di infra; (6) tiap SQL ekspor read-only, bertenant, = ANY(ids); (7) izin = R modul untuk 8 jalur."""
import ast
import asyncio
import inspect
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk as RB
from app.services import bulk as B
from app.services import bulk_export as BE

APP = Path(B.__file__).resolve().parents[1]
T = "t1"
U = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
CTX = {"tenant_id": T, "user_id": U}


LABEL_KODE = {"v": "Kode order"}


def _id(): return uuid.uuid4()


def _jalan(c): return asyncio.run(c)


# ---------------- (1) validasi id ----------------
def test_validasi_kosong_tak_valid_duplikat_dan_batas():
    for kosong in ([], None):
        with pytest.raises(HTTPException) as e:
            B.validasi_ids(kosong, "csv")
        assert e.value.status_code == 400 and e.value.detail["code"] == "BULK_KOSONG"
    with pytest.raises(HTTPException) as e2:
        B.validasi_ids([str(_id()), "bukan-uuid"], "csv")
    assert e2.value.status_code == 400 and e2.value.detail["code"] == "BULK_ID_TAK_VALID"
    a, b = _id(), _id()
    assert B.validasi_ids([str(a), str(b), str(a)], "csv") == [a, b]  # duplikat dibuang, urutan dipertahankan
    assert B.BATAS == {"tulis": 50, "csv": 500, "pdf": 25}
    assert len(B.validasi_ids([str(_id()) for _ in range(500)], "csv")) == 500
    with pytest.raises(HTTPException) as e3:
        B.validasi_ids([str(_id()) for _ in range(501)], "csv")
    assert e3.value.status_code == 400 and e3.value.detail["code"] == "BULK_TERLALU_BANYAK" and e3.value.detail["batas"] == 500
    assert "Maksimal 500" in e3.value.detail["message"]
    with pytest.raises(HTTPException):
        B.validasi_ids([str(_id()) for _ in range(51)], "tulis")
    # duplikat tak dihitung terhadap batas
    x = str(_id())
    assert len(B.validasi_ids([x] * 600, "csv")) == 1


# ---------------- (2) CSV ----------------
def test_csv_bom_crlf_kutip_dan_tipe():
    isi = B.buat_csv(["Pelanggan", "Nilai", "Tgl"], [['PT "Maju", Jaya', Decimal("1E+3"), date(2026, 10, 5)],
                                                   ["baris\nbaru", Decimal("-250.50"), None]])
    assert isi.startswith(b"\xef\xbb\xbf")
    teks = isi.decode("utf-8-sig")
    assert teks.split("\r\n")[0] == "Pelanggan,Nilai,Tgl"
    assert '"PT ""Maju"", Jaya",1000,2026-10-05' in teks
    assert '"baris\nbaru",-250.50,' in teks  # angka negatif TANPA awalan '


@pytest.mark.parametrize("awal", ["=", "+", "-", "@", "\t"])
def test_csv_teks_berawalan_rumus_dinetralkan(awal):
    teks = B.buat_csv(["x"], [[f"{awal}HYPERLINK(1)"]]).decode("utf-8-sig")
    assert teks.split("\r\n")[1] == f"'{awal}HYPERLINK(1)" or teks.split("\r\n")[1] == f'"\'{awal}HYPERLINK(1)"'


def test_csv_kontrol_angka_dan_teks_biasa_tak_diubah():
    teks = B.buat_csv(["a"], [[-5], [Decimal("-0.5")], ["Toko Merdeka"], [3.5]]).decode("utf-8-sig")
    assert teks.split("\r\n")[1:5] == ["-5", "-0.5", "Toko Merdeka", "3.5"]


# ---------------- (3) audit ----------------
class _K:
    def __init__(self): self.exec, self.trx = [], 0

    async def execute(self, sql, *a): self.exec.append((" ".join(sql.split()), a))


def test_audit_bulk_bertenant_dan_ringkas():
    k = _K()
    ids = [_id() for _ in range(60)]
    _jalan(B.catat_audit(k, CTX, "export", "quotes", ids, {"format": "csv"}))
    sql, a = k.exec[0]
    assert "INSERT INTO audit_logs" in sql and a[0] == "BULK_EXPORT" and a[1] == T and a[3] == str(U)
    import json
    meta = json.loads(a[2])
    assert meta["modul"] == "quotes" and meta["jumlah"] == 60 and len(meta["ids"]) == 50 and meta["format"] == "csv"


# ---------------- (4) per item ----------------
class _Tx:
    def __init__(self, k): self.k = k
    async def __aenter__(self): self.k.trx += 1; return None
    async def __aexit__(self, *e): return False


class _Konn(_K):
    def transaction(self): return _Tx(self)


class _Pool:
    def __init__(self): self.k = _Konn(); self.diminta = 0

    def acquire(self):
        pool = self

        class A:
            async def __aenter__(s): pool.diminta += 1; return pool.k
            async def __aexit__(s, *e): return False
        return A()


@pytest.fixture
def idem(monkeypatch):
    from app.services import idem_buat as IB
    catatan = {"mulai": [], "simpan": [], "lama": {}}

    async def mulai(conn, ctx, kunci, prefix, doc, isi, response=None):
        catatan["mulai"].append((kunci, prefix, doc, isi))
        return "kp", "sd", catatan["lama"].get(doc)

    async def simpan(conn, ctx, kp, sd, src, resp, rid):
        catatan["simpan"].append((src, rid))
        return resp
    monkeypatch.setattr(IB, "mulai_aksi", mulai)
    monkeypatch.setattr(IB, "simpan", simpan)
    return catatan


def test_per_item_isolasi_transaksi_sendiri_dan_hasil_per_item(idem):
    ids = [_id() for _ in range(4)]
    dipanggil = []

    async def fn(conn, ctx, i):
        dipanggil.append(i)
        if i == ids[1]:
            raise HTTPException(status_code=400, detail={"code": "SO_BUKAN_DRAF", "message": "Hanya draf."})
        if i == ids[2]:
            raise RuntimeError("rahasia internal")
        return {"id": str(i)}
    p = _Pool()
    out = _jalan(B.jalankan_per_item(p, CTX, "confirm", "sales-orders", ids, fn, kunci_batch="K1", payload={"x": 1}))
    assert dipanggil == ids  # semua item diproses meski ada yang gagal
    st = [i["status"] for i in out["items"]]
    assert st == ["ok", "rejected", "error", "ok"] and out["ok_count"] == 2 and out["rejected_count"] == 1 and out["error_count"] == 1
    assert out["all_ok"] is False and out["total"] == 4 and [i["id"] for i in out["items"]] == [str(i) for i in ids]
    r = out["items"][1]
    assert r["http"] == 400 and r["code"] == "SO_BUKAN_DRAF" and r["message"] == "Hanya draf."
    e = out["items"][2]
    assert e["http"] == 500 and e["code"] == "BULK_ITEM_GALAT" and "rahasia" not in str(e)  # galat internal tak bocor
    assert p.k.trx == 4  # tiap item transaksi SENDIRI
    assert all(m[1] == "BULK_CONFIRM" and m[0] == "K1" for m in idem["mulai"])  # prefix + kunci batch
    assert [s[1] for s in idem["simpan"]] == [ids[0], ids[3]]  # hanya yang sukses disimpan
    audit = [a for s, a in p.k.exec if "INSERT INTO audit_logs" in s]
    assert len(audit) == 1 and audit[0][0] == "BULK_CONFIRM"  # satu baris audit ringkas di akhir


def test_per_item_replay_tak_menjalankan_fn(idem):
    ids = [_id(), _id()]
    idem["lama"][ids[0]] = {"success": True, "asli": 1}
    dipanggil = []

    async def fn(conn, ctx, i):
        dipanggil.append(i)
        return {"baru": 1}
    out = _jalan(B.jalankan_per_item(_Pool(), CTX, "confirm", "sales-orders", ids, fn, kunci_batch="K1"))
    assert dipanggil == [ids[1]]
    assert out["items"][0]["replay"] is True and out["items"][0]["result"] == {"success": True, "asli": 1}
    assert out["items"][1]["replay"] is False and out["all_ok"] is True


def test_per_item_409_kunci_terpakai_jadi_ditolak(idem, monkeypatch):
    from app.services import idem_buat as IB

    async def mulai(*a, **k):
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "kunci dipakai"})
    monkeypatch.setattr(IB, "mulai_aksi", mulai)

    async def fn(*a):
        raise AssertionError("tak boleh jalan")
    out = _jalan(B.jalankan_per_item(_Pool(), CTX, "confirm", "sales-orders", [_id()], fn, kunci_batch="K"))
    assert out["items"][0]["status"] == "rejected" and out["items"][0]["code"] == "IDEMPOTENCY_KEY_REUSED"


# ---------------- (5) daftar putih ----------------
def test_daftar_putih_hanya_export_dan_rute_bulk_hanya_export():
    assert B.AKSI_DIIZINKAN == {"export", "confirm", "delete", "send"}  # F1 export; F2 SO confirm/delete; F3 Penawaran send (daftar putih eksplisit)
    assert set(RB.ROUTERS) == set(BE.SPEC) and len(RB.ROUTERS) == 8
    for modul, router in RB.ROUTERS.items():
        jalur = [(r.path, tuple(sorted(r.methods))) for r in router.routes]
        assert jalur == [("/bulk/export", ("POST",))], (modul, jalur)


def test_infra_bulk_tak_mengimpor_atau_memanggil_modul_uang():
    terlarang_impor = ("receive_payments", "customer_deposits", "credit_notes", "sales_invoices", "journal", "posting",
                       "bank_sync", "so_pelunasan", "bill_payments")
    for berkas in ("routers/bulk.py", "routers/bulk_so.py", "routers/bulk_quote.py", "services/bulk.py"):
        pohon = ast.parse((APP / berkas).read_text(encoding="utf-8"))
        impor = [n for n in ast.walk(pohon) if isinstance(n, (ast.Import, ast.ImportFrom))]
        nama = " ".join((getattr(n, "module", None) or "") + " " + " ".join(a.name for a in n.names) for n in impor)
        for t in terlarang_impor:
            assert t not in nama, f"{berkas} mengimpor modul uang {t}"
    # bulk_export hanya BACA: impor modul-modul hanya untuk pembantu label status; tak ada panggilan penulis
    src = (APP / "services/bulk_export.py").read_text(encoding="utf-8")
    for tulis in ("_core(", "posting_", "void_", "refund_", "apply_", "INSERT", "UPDATE", "DELETE"):
        assert tulis not in src, f"bulk_export memuat {tulis}"
    # kontrol: pendeteksi bisa menemukan impor terlarang
    uji = ast.parse("from ..routers import receive_payments")
    assert "receive_payments" in " ".join(a.name for a in uji.body[0].names)


def test_sql_ekspor_baca_saja_bertenant_dan_ids():
    for modul, s in BE.SPEC.items():
        sql = " ".join(s.sql.split())
        assert sql.lstrip().upper().startswith("SELECT"), modul
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER)\b", sql, re.I), modul
        assert "t.tenant_id = $1" in sql and "ANY($2::uuid[])" in sql, modul
        assert len(s.kolom) == 6 and s.kolom[0] == "Pelanggan" and s.kolom[-1] == "Status", modul
        if s.jenis_kode:
            from app.services.kode_order import SUMBER_SO
            assert s.jenis_kode in SUMBER_SO, modul


# ---------------- (7) izin ----------------
@pytest.mark.parametrize("prefiks,modul", [("sales-orders", "sales_order"), ("quotes", "quote"), ("proformas", "proforma"),
                                           ("sales-invoices", "sales_invoice"), ("customer-deposits", "customer_deposit"),
                                           ("receive-payments", "receive_payment"), ("deliveries", "sales_invoice"),
                                           ("credit-notes", "credit_note")])
def test_izin_ekspor_massal_adalah_baca_modul(prefiks, modul):
    assert PM.PermissionMiddleware(lambda *a: None, False)._find_permission(f"/api/{prefiks}/bulk/export", "POST") == (modul, "R")


# ---------------- susun_baris + handler ----------------
class _KS:
    def __init__(self, rows): self.rows, self.sql, self.exec, self.trx = rows, [], [], 0

    def transaction(self): return _Tx(self)
    async def execute(self, sql, *a): self.exec.append((" ".join(sql.split()), a))
    async def fetch(self, sql, *a): self.sql.append((sql, a)); return self.rows
    async def fetchval(self, sql, *a): return "Asia/Jakarta"


@pytest.fixture
def zona_kode(monkeypatch):
    async def zona(conn, tid):
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Jakarta")

    async def tempel(conn, tid, jenis, dok, kunci="id"):
        for d in dok:
            d.update({"order_code": "KODE-1", "order_title": "Judul"})
        return dok
    monkeypatch.setattr(BE, "zona_tenant", zona)
    import app.services.kode_order as KO
    monkeypatch.setattr(KO, "tempel_kode", tempel)


def test_susun_baris_urutan_dilewati_zona_dan_label(zona_kode):
    a, b, c = _id(), _id(), _id()
    rows = [{"id": b, "pelanggan": "B", "nomor": "CN-2", "tgl": date(2026, 10, 2), "kol4": datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc),
             "dicatat": None, "nilai": Decimal("1000"), "status": "applied", "amount_applied": 0, "amount_refunded": 1000},
            {"id": a, "pelanggan": "A", "nomor": "CN-1", "tgl": date(2026, 10, 1), "kol4": datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc),
             "dicatat": None, "nilai": Decimal("500"), "status": "posted", "amount_applied": 0, "amount_refunded": 0}]
    k = _KS(rows)
    baris, ada, lewat = _jalan(BE.susun_baris(k, T, BE.SPEC["credit-notes"], [a, b, c]))
    assert (ada, lewat) == (2, 1) and [r[1] for r in baris] == ["CN-1", "CN-2"]  # urutan = urutan pilihan; c tak ada -> dilewati
    assert k.sql[0][1] == (T, [a, b, c])  # tenant eksplisit + ids
    assert baris[1][3] == date(2026, 10, 5)  # 18:00 UTC = 01:00 WIB hari berikutnya
    assert baris[0][5] == "Terbit" and baris[1][5] == "Dikembalikan" and baris[1][6] == "applied"  # label layar + kode
    assert baris[0][7] == "KODE-1" and baris[0][8] == "Judul" and baris[0][9] == str(a)


class _Req:
    def __init__(self, user): self.state = type("S", (), {"user": user})()


def _pool_untuk(monkeypatch, k):
    class P:
        def acquire(s):
            class A:
                async def __aenter__(a): return k
                async def __aexit__(a, *e): return False
            return A()

    async def gp(): return P()
    monkeypatch.setattr(RB, "get_pool", gp)

    async def hari(conn, tid): return date(2026, 10, 5)
    monkeypatch.setattr(RB, "tanggal_dokumen", hari)

    async def setelan(conn, tid): return {"label": LABEL_KODE["v"]}
    monkeypatch.setattr(RB, "muat_setelan", setelan)


def _handler(modul): return RB.ROUTERS[modul].routes[0].endpoint


def test_handler_mengembalikan_csv_header_dan_audit(monkeypatch, zona_kode):
    a = _id()
    rows = [{"id": a, "pelanggan": "X", "nomor": "QUO-1", "tgl": date(2026, 10, 1), "kol4": date(2026, 10, 30), "dicatat": None,
             "nilai": Decimal("100"), "status": "sent"}]
    k = _KS(rows)
    _pool_untuk(monkeypatch, k)
    resp = _jalan(_handler("quotes")(_Req({"tenant_id": T, "user_id": str(U)}), RB.BulkExportRequest(ids=[str(a), str(_id())])))
    assert resp.media_type.startswith("text/csv") and resp.headers["X-Bulk-Total"] == "2" and resp.headers["X-Bulk-Ditemukan"] == "1" \
        and resp.headers["X-Bulk-Dilewati"] == "1"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="penawaran-2026-10-05.csv"' and resp.headers["Cache-Control"] == "no-store"
    teks = resp.body.decode("utf-8-sig")
    assert teks.split("\r\n")[0] == "Pelanggan,No. penawaran,Tgl. penawaran,Berlaku sampai,Total,Status,Status (kode),Kode order,Judul pesanan,ID"
    assert "X,QUO-1,2026-10-01,2026-10-30,100,Terkirim,sent,KODE-1,Judul," in teks
    assert any("INSERT INTO audit_logs" in s and a_[0] == "BULK_EXPORT" for s, a_ in k.exec)


def test_handler_tak_ada_404_terlalu_banyak_400_tanpa_login_401(monkeypatch, zona_kode):
    k = _KS([])
    _pool_untuk(monkeypatch, k)
    with pytest.raises(HTTPException) as e:
        _jalan(_handler("deliveries")(_Req({"tenant_id": T, "user_id": str(U)}), RB.BulkExportRequest(ids=[str(_id())])))
    assert e.value.status_code == 404 and e.value.detail["code"] == "BULK_TAK_ADA" and not k.exec  # tanpa audit bila kosong
    with pytest.raises(HTTPException) as e2:
        _jalan(_handler("deliveries")(_Req({"tenant_id": T, "user_id": str(U)}), RB.BulkExportRequest(ids=[str(_id()) for _ in range(501)])))
    assert e2.value.status_code == 400 and e2.value.detail["code"] == "BULK_TERLALU_BANYAK"
    with pytest.raises(HTTPException) as e3:
        _jalan(_handler("deliveries")(_Req(None), RB.BulkExportRequest(ids=[str(_id())])))
    assert e3.value.status_code == 401


def test_judul_kolom_kode_order_memakai_label_setelan_tenant(monkeypatch, zona_kode):
    """U7: label kode order = setelan tenant (grapgrap 'No. SPK'); judul kolom CSV tak boleh teks tetap 'Kode order'."""
    LABEL_KODE["v"] = "No. SPK"
    try:
        a = _id()
        k = _KS([{"id": a, "pelanggan": "X", "nomor": "SO-1", "tgl": date(2026, 10, 1), "kol4": None, "dicatat": None,
                  "nilai": Decimal("5"), "status": "confirmed", "kode_order": "123", "judul_order": "Judul"}])
        _pool_untuk(monkeypatch, k)
        resp = _jalan(_handler("sales-orders")(_Req({"tenant_id": T, "user_id": str(U)}), RB.BulkExportRequest(ids=[str(a)])))
        kepala = resp.body.decode("utf-8-sig").split("\r\n")[0].split(",")
        assert kepala[7] == "No. SPK" and "Kode order" not in kepala and kepala[-1] == "ID"
    finally:
        LABEL_KODE["v"] = "Kode order"
