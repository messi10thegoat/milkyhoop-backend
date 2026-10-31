"""B1a (28 Okt 2026, MASTER GO): ekspor CSV + PDF massal untuk 6 modul PEMBELIAN, kembar Penjualan U1b F1/F5.

Penjaga: (1) 6 modul, kolom layar (Vendor ... Status), GRN TANPA kolom nilai (putusan MASTER); (2) SQL baca saja, tenant eksplisit, = ANY(ids);
penyusun tak memuat penulis; (3) Status = pembantu turunan YANG SAMA dengan baris daftar (fakta_po / grn medan / posisi_tagihan keadaan /
teks 'bp' / fakta_vd / medan_banyak), sisa draf kosong; (4) urutan = ids, id tenant lain dilewati; keadaan tagihan memakai tanggal bisnis;
(5) rute: Pembelian = /bulk/export + /bulk/pdf SAJA (bagikan massal tak ada: kind tak lolos CHECK document_shares), kind ⊆ JENIS_CETAK;
(6) izin = R modul == PDF tunggal; (7) handler: CSV + kolom tambahan + audit, 404/400/401; PDF memakai kind modul + nama berkas."""
import ast
import asyncio
import io
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk as RB
from app.routers import bulk_dokumen as BD
from app.routers import dokumen as D
from app.services import bulk as B
from app.services import bulk_export_beli as BB

APP = Path(B.__file__).resolve().parents[1]
T = "t1"
U = uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")
CTX = {"tenant_id": T, "user_id": U}
MODUL = {"purchase-orders": ("purchase_order", "purchase_order"), "goods-receipts": ("goods_receipt", "purchase_order"),
         "bills": ("bill", "purchase_invoice"), "bill-payments": ("payment_out", "send_payment"),
         "vendor-deposits": ("vendor_deposit", "vendor_deposit"), "vendor-credits": ("debit_note", "debit_note")}
HARI = date(2026, 10, 28)


def _jalan(c): return asyncio.run(c)


def _id(): return uuid.uuid4()


# ---------------- (1) spesifikasi kolom ----------------
def test_enam_modul_kolom_layar_dan_grn_tanpa_nilai():
    assert set(BB.SPEC_BELI) == set(MODUL)
    for m, s in BB.SPEC_BELI.items():
        assert s.modul == m and s.kolom[0] == "Vendor" and s.kolom[-1] == "Status" and len(set(s.kolom)) == len(s.kolom), m
    assert BB.KOLOM_TAMBAHAN == ("Status (kode)", "Referensi", "ID")
    uang = {"Nilai", "Total", "Jumlah", "Sisa", "Belum ditagih", "Belum dialokasikan"}
    assert not uang & set(BB.SPEC_BELI["goods-receipts"].kolom)  # GRN = dokumen gudang (putusan MASTER 28 Okt)
    assert BB.SPEC_BELI["bills"].kolom == ("Vendor", "No. faktur", "Tgl. faktur", "Jatuh tempo", "Total", "Sisa", "Status")


# ---------------- (2) baca saja ----------------
def test_sql_baca_saja_bertenant_dan_ids():
    import re
    for m, s in BB.SPEC_BELI.items():
        sql = " ".join(s.sql.split())
        assert sql.upper().startswith("SELECT"), m
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER)\b", sql, re.I), m
        assert re.search(r"\b\w+\.tenant_id = \$1 AND \w+\.id = ANY\(\$2::uuid\[\]\)", sql), m


def test_penyusun_tak_memuat_penulis_atau_modul_uang():
    src = (APP / "services/bulk_export_beli.py").read_text(encoding="utf-8")
    for tulis in ("_core(", "posting_", "void_", "refund_", "apply_", "INSERT", "UPDATE", "DELETE", "execute("):
        assert tulis not in src, tulis
    impor = " ".join((getattr(n, "module", None) or "") + " " + " ".join(a.name for a in n.names)
                     for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom)))
    for t in ("routers", "bill_payments", "vendor_deposits", "vendor_credits", "journal", "bank_sync"):
        assert t not in impor, t


# ---------------- (3)(4) penyusun baris ----------------
class _K:
    def __init__(self, rows): self.rows, self.sql = rows, []
    async def fetch(self, sql, *a): self.sql.append((sql, a)); return self.rows


@pytest.fixture
def boneka(monkeypatch):
    from app.services import nota_debit_daftar as NDD
    from app.services import po_daftar as PD
    from app.services import uang_muka_vendor_daftar as UMD

    async def hari(conn, tid): return HARI
    monkeypatch.setattr(PD, "tanggal_hari_ini", hari)

    async def fakta_po(conn, tid, ids, h):
        assert h == HARI
        return {i: {"position_text": f"POS-{i}", "sisa_ditagih": Decimal("7")} for i in ids}
    monkeypatch.setattr(PD, "fakta_po", fakta_po)

    async def akun(conn, tid): return "AKUN"

    async def fakta_vd(conn, tid, akun_, ids):
        assert akun_ == "AKUN"
        return {i: {"status_text": f"UMP-{i}", "sisa": None} for i in ids}
    monkeypatch.setattr(UMD, "akun_baca", akun)
    monkeypatch.setattr(UMD, "fakta_vd", fakta_vd)

    async def medan(conn, tid, rows):
        return {r["id"]: {"status_text": f"ND-{r['id']}", "remaining_amount": 9.0, "sisa_keadaan": "draf" if r["status"] == "draft" else "utuh"}
                for r in rows}
    monkeypatch.setattr(NDD, "medan_banyak", medan)


def _susun(modul, rows, ids):
    return _jalan(BB.susun_baris(_K(rows), T, BB.SPEC_BELI[modul], ids))


def test_urutan_ids_dan_id_tenant_lain_dilewati(boneka):
    a, b, c = _id(), _id(), _id()
    rows = [{"id": i, "vendor_name": "V", "po_number": f"PO-{n}", "po_date": HARI, "expected_date": None, "total_amount": Decimal("10"),
             "status": "sent", "ref_no": "R"} for n, i in ((1, a), (3, c))]
    baris, ada, lewat = _susun("purchase-orders", rows, [c, b, a])
    assert (ada, lewat) == (2, 1) and [x[1] for x in baris] == ["PO-3", "PO-1"] and baris[0][-1] == str(c)
    assert baris[0][5:] == [Decimal("7"), f"POS-{c}", "sent", "R", str(c)]


def test_status_po_grn_bayar_ump_nd_dari_pembantu_daftar(boneka):
    i = _id()
    g = {"id": i, "vendor_name": "V", "receipt_number": "GRN-1", "receipt_date": HARI, "po_number": "PO-1", "warehouse_name": "Utama",
         "status": "void", "qty_dasar": Decimal("5"), "qty_tertaut": Decimal("0"), "grni_terbuka": Decimal("0")}
    assert _susun("goods-receipts", [g], [i])[0][0][5] == "Dibatalkan"  # == grn_daftar.medan (status_text)
    g2 = dict(g, status="posted", qty_tertaut=Decimal("2"))
    assert _susun("goods-receipts", [g2], [i])[0][0][5] == "Sebagian ditagih"
    p = {"id": i, "vendor_name": "V", "payment_number": "PAY-1", "payment_date": HARI, "total_amount": Decimal("100"), "status": "posted",
         "reference_number": "TRF", "bruto": Decimal("100"), "dialokasi": Decimal("40")}
    r = _susun("bill-payments", [p], [i])[0][0]
    assert r[3:9] == ["Sebagian dialokasikan", Decimal("100"), 60.0, "Dibayar", "posted", "TRF"]  # layar: Draf/Dibayar/Batal (teks 'bp')
    assert _susun("bill-payments", [dict(p, status="voided")], [i])[0][0][6] == "Batal"
    v = {"id": i, "vendor_name": "V", "deposit_number": "VD-1", "deposit_date": HARI, "amount": Decimal("50"), "status": "posted", "reference": "X"}
    assert _susun("vendor-deposits", [v], [i])[0][0][4:8] == [None, f"UMP-{i}", "posted", "X"]
    n = {"id": i, "vendor_name": "V", "credit_number": "VC-1", "credit_date": HARI, "total_amount": Decimal("30"), "amount_applied": 0,
         "amount_received": 0, "status": "posted", "ref_no": None, "original_bill_id": None, "original_bill_number": "PB-1"}
    assert _susun("vendor-credits", [n], [i])[0][0][3:7] == ["PB-1", Decimal("30"), 9.0, f"ND-{i}"]
    assert _susun("vendor-credits", [dict(n, status="draft")], [i])[0][0][5] is None  # draf: sisa "—" (== layar)


def test_status_tagihan_posisi_dari_keadaan_v440_dan_tanggal_bisnis(boneka):
    i = _id()
    b = {"id": i, "vendor_name": "V", "invoice_number": "PB-1", "issue_date": HARI, "due_date": HARI, "amount": Decimal("100"), "ref_no": "INV-V",
         "keadaan": "partial", "dibayar": Decimal("40"), "hutang": Decimal("60")}
    k = _K([b])
    baris, _, _ = _jalan(BB.susun_baris(k, T, BB.SPEC_BELI["bills"], [i]))
    assert baris[0][4:9] == [Decimal("100"), Decimal("60"), "Sebagian", "partial", "INV-V"]  # posisi, BUKAN kode keadaan
    assert k.sql[0][1] == (T, [i], HARI)  # $3 = tanggal bisnis tenant (== daftar)
    for keadaan, dibayar, harap in (("overdue", 0, "Belum lunas"), ("paid", 100, "Lunas"), ("draft", 0, "Draf"), ("void", 0, "Batal")):
        assert _susun("bills", [dict(b, keadaan=keadaan, dibayar=Decimal(dibayar))], [i])[0][0][6] == harap


def test_tanpa_baris_tak_memanggil_pembantu(monkeypatch):
    from app.services import po_daftar as PD

    async def jangan(*a): raise AssertionError("pembantu dipanggil tanpa baris")
    monkeypatch.setattr(PD, "fakta_po", jangan)
    monkeypatch.setattr(PD, "tanggal_hari_ini", jangan)
    assert _susun("purchase-orders", [], [_id()]) == ([], 0, 1)


# ---------------- (5) rute ----------------
def test_rute_pembelian_hanya_ekspor_dan_pdf_tanpa_bagikan():
    for m, (kind, _) in MODUL.items():
        assert [(r.path, tuple(r.methods)) for r in RB.ROUTERS[m].routes] == [("/bulk/export", ("POST",))], m
        bagi = m in ("purchase-orders", "bills")  # L7 (31 Okt): bagikan massal PO + Faktur Pembelian (CHECK V468)
        harap = [("/bulk/pdf", ("POST",))] + ([("/bulk/share/preview", ("POST",)), ("/bulk/share", ("POST",))] if bagi else [])
        assert [(r.path, tuple(r.methods)) for r in BD.ROUTERS[m].routes] == harap, m
        assert BD.MODUL_KIND[m] == kind and kind in D.JENIS_CETAK, m
        assert kind not in D.JENIS, m  # bundel Penjualan tak berubah
        assert (kind in D.JENIS_BAGIKAN) == bagi == (m in BD.MODUL_BAGIKAN), m
    assert B.AKSI_DIIZINKAN == {"export", "confirm", "delete", "send", "cancel", "pdf", "share"}  # daftar putih TIDAK berubah


# ---------------- (6) izin ----------------
def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


@pytest.mark.parametrize("prefiks", sorted(MODUL))
def test_izin_ekspor_dan_pdf_adalah_baca_modul_sama_dengan_pdf_tunggal(prefiks):
    kind, modul = MODUL[prefiks]
    assert _izin("POST", f"/api/{prefiks}/bulk/export") == (modul, "R") == _izin("POST", f"/api/{prefiks}/bulk/pdf")
    assert _izin("POST", f"/api/{prefiks}/bulk/pdf") == _izin("GET", f"/api/documents/{kind}/X/pdf")
    if prefiks in ("purchase-orders", "bills"):  # L7: bagikan massal = E modul == share tunggal
        assert _izin("POST", f"/api/{prefiks}/bulk/share") == (modul, "E") == _izin("POST", f"/api/documents/{kind}/X/share")
    else:
        assert _izin("POST", f"/api/{prefiks}/bulk/share") != (modul, "E")  # tak ada pola bagikan massal modul ini


# ---------------- (7) handler ----------------
class _Tx:
    async def __aenter__(self): return None
    async def __aexit__(self, *e): return False


class _Kn:
    def __init__(self): self.exec = []
    def transaction(self): return _Tx()
    async def execute(self, sql, *a): self.exec.append((" ".join(sql.split()), a))


class _Pool:
    def __init__(self): self.k = _Kn()

    def acquire(self):
        pool = self

        class A:
            async def __aenter__(s): return pool.k
            async def __aexit__(s, *e): return False
        return A()


class _Req:
    def __init__(self, user=True):
        self.headers = {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(U)} if user else None})()


def _ep(routers, modul, nama):
    return next(r.endpoint for r in routers[modul].routes if r.endpoint.__name__ == nama)


@pytest.fixture
def lingkungan(monkeypatch):
    pool = _Pool()

    async def gp(): return pool

    async def hari(conn, tid): return HARI
    for mod in (RB, BD):
        monkeypatch.setattr(mod, "get_pool", gp)
        monkeypatch.setattr(mod, "tanggal_dokumen", hari)
    return pool


def test_handler_ekspor_csv_kolom_tambahan_audit_dan_galat(lingkungan, monkeypatch):
    a, b = _id(), _id()
    terima = {}

    async def susun(conn, tid, spec, ids):
        terima.update(tid=tid, modul=spec.modul, ids=ids)
        return [["V", "PB-1", HARI, None, Decimal("100"), None, "Draf", "draft", None, str(a)]], 1, 1
    monkeypatch.setattr(BB, "susun_baris", susun)
    resp = _jalan(_ep(RB.ROUTERS, "bills", "bulk_export")(_Req(), RB.BulkExportRequest(ids=[str(a), str(b)])))
    assert terima == {"tid": T, "modul": "bills", "ids": [a, b]}
    teks = resp.body.decode("utf-8-sig")
    assert teks.splitlines()[0] == "Vendor,No. faktur,Tgl. faktur,Jatuh tempo,Total,Sisa,Status,Status (kode),Referensi,ID"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="faktur-pembelian-2026-10-28.csv"'
    assert (resp.headers["X-Bulk-Total"], resp.headers["X-Bulk-Ditemukan"], resp.headers["X-Bulk-Dilewati"]) == ("2", "1", "1")
    assert any("INSERT INTO audit_logs" in s and x[0] == "BULK_EXPORT" for s, x in lingkungan.k.exec)

    async def kosong(conn, tid, spec, ids): return [], 0, len(ids)
    monkeypatch.setattr(BB, "susun_baris", kosong)
    with pytest.raises(HTTPException) as e:
        _jalan(_ep(RB.ROUTERS, "vendor-credits", "bulk_export")(_Req(), RB.BulkExportRequest(ids=[str(a)])))
    assert e.value.status_code == 404 and e.value.detail["code"] == "BULK_TAK_ADA"
    with pytest.raises(HTTPException) as e2:
        _jalan(_ep(RB.ROUTERS, "goods-receipts", "bulk_export")(_Req(), RB.BulkExportRequest(ids=[str(_id()) for _ in range(501)])))
    assert e2.value.detail["code"] == "BULK_TERLALU_BANYAK" and e2.value.detail["batas"] == 500
    with pytest.raises(HTTPException) as e3:
        _jalan(_ep(RB.ROUTERS, "purchase-orders", "bulk_export")(_Req(user=False), RB.BulkExportRequest(ids=[str(a)])))
    assert e3.value.status_code == 401


@pytest.mark.parametrize("prefiks,berkas", [("purchase-orders", "pesanan-pembelian"), ("goods-receipts", "penerimaan-barang"),
                                            ("bills", "faktur-pembelian"), ("bill-payments", "pembayaran-keluar"),
                                            ("vendor-deposits", "uang-muka-pembelian"), ("vendor-credits", "nota-debit")])
def test_handler_pdf_memakai_kind_modul_dan_nama_berkas(lingkungan, monkeypatch, prefiks, berkas):
    jenis = []

    async def render(conn, ctx, request, kind, did):
        jenis.append(kind)
        return ("R-" + did, "NO-" + did[:4])

    async def tercache(tid, r): return b"%PDF-" + r.encode()
    monkeypatch.setattr(D, "_render", render)
    monkeypatch.setattr(D, "pdf_tercache", tercache)
    monkeypatch.setattr(D, "_ctx", lambda request: CTX)
    resp = _jalan(_ep(BD.ROUTERS, prefiks, "bulk_pdf")(_Req(), BD.BulkIdsRequest(ids=[str(_id()), str(_id())])))
    assert jenis == [MODUL[prefiks][0]] * 2
    assert resp.headers["Content-Disposition"] == f'attachment; filename="{berkas}-2026-10-28.zip"'
    assert len([n for n in zipfile.ZipFile(io.BytesIO(resp.body)).namelist() if n.endswith(".pdf")]) == 2
    assert any("INSERT INTO audit_logs" in s and x[0] == "BULK_PDF" for s, x in lingkungan.k.exec)
