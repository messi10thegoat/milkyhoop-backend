"""L7 bagikan massal Pembelian (31 Okt 2026, MASTER GO; V468 dipesan MASTER): PO + Faktur Pembelian bisa dibagikan (tunggal + massal),
kontrak SAMA dengan Penjualan F5.

Penjaga: (1) CHECK document_shares.kind V468 == SEMUA JENIS_CETAK, rollback == JENIS (V379) + membuang tautan Pembelian; JENIS_BAGIKAN =
JENIS + (bill, purchase_order) dan semua rute share memakai JENIS_BAGIKAN; (2) penentu per item: draf -> DOKUMEN_DRAF, batal/void ->
DOKUMEN_BATAL, SQL bertenant; (3) rute massal share hanya PO + bills; (4) izin E/R modul Pembelian eksplisit (BUKAN pola penampung
sales_order); (5) bundel Pembelian: `sent` dari tautan (sent|viewed) di 5 pembentuk, tanpa tautan tetap (PO sent_at), himpunan
{none, sent, viewed}."""
import asyncio
import inspect
import re
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.middleware import permission_middleware as PM
from app.routers import bulk_dokumen as BD
from app.routers import dokumen as D
from app.services import dokumen_bagikan as DB
from app.services.bulk_export import SPEC

MIG = Path(__file__).resolve().parents[3] / "migrations"
APP = Path(__file__).resolve().parents[2] / "app"
T = "t1"
CTX = {"tenant_id": T, "user_id": uuid.UUID("0bccdb25-fdf0-4e99-9024-b9a20846f76c")}
BUKA = {"purchase-orders": ("purchase_order", "purchase_order"), "bills": ("bill", "purchase_invoice")}
TUTUP = {"goods-receipts": "goods_receipt", "bill-payments": "payment_out", "vendor-deposits": "vendor_deposit",
         "vendor-credits": "debit_note"}


def _jalan(c): return asyncio.run(c)


def _kind_di(berkas):
    teks = (MIG / berkas).read_text(encoding="utf-8")
    m = re.search(r"CHECK \(kind IN \(([^)]*)\)\)", teks)
    return set(re.findall(r"'(\w+)'", m.group(1)))


# ---------------- (1) migrasi + daftar jenis ----------------
def test_v468_check_sama_dengan_semua_jenis_cetak():
    assert _kind_di("V468__document_shares_kind_pembelian.sql") == set(D.JENIS_CETAK)
    assert set(D.JENIS_BAGIKAN) <= _kind_di("V468__document_shares_kind_pembelian.sql")


def test_v468_rollback_kembali_ke_v379_dan_buang_tautan_pembelian():
    assert _kind_di("V468__document_shares_kind_pembelian_ROLLBACK.sql") == set(D.JENIS) == _kind_di("V379__document_shares_kind_credit_note.sql")
    teks = (MIG / "V468__document_shares_kind_pembelian_ROLLBACK.sql").read_text(encoding="utf-8")
    dibuang = set(re.findall(r"'(\w+)'", teks.split("DELETE FROM document_shares WHERE kind IN (", 1)[1].split(")", 1)[0]))
    assert dibuang == set(D.JENIS_CETAK) - set(D.JENIS)


def test_jenis_bagikan_penjualan_plus_po_dan_faktur_pembelian():
    assert D.JENIS_BAGIKAN == D.JENIS + ("bill", "purchase_order")
    assert D.JENIS == ("rekap", "quotation", "proforma", "receipt", "delivery", "invoice", "credit_note")  # bundel Penjualan tak berubah
    for k in TUTUP.values():
        assert k in D.JENIS_CETAK and k not in D.JENIS_BAGIKAN


def test_semua_rute_share_memakai_jenis_bagikan():
    src = (APP / "routers" / "dokumen.py").read_text()
    for nama in ("bagikan_dokumen", "pratinjau_bagikan_dokumen", "daftar_tautan_dokumen", "cabut_tautan_dokumen"):
        badan = src.split(f"async def {nama}(", 1)[1].split("\n@router", 1)[0]
        assert "if kind not in JENIS_BAGIKAN:" in badan and "JENIS_CETAK" not in badan, nama


def test_tabel_dan_label_pembelian():
    assert D.LABEL_JENIS["bill"] == "Faktur Pembelian" and D.LABEL_JENIS["purchase_order"] == "Pesanan Pembelian"
    for k, tabel, kolom in (("bill", "bills", "invoice_number"), ("purchase_order", "purchase_orders", "po_number")):
        sql, draf = D._DOKUMEN_TABEL[k]
        assert f"FROM {tabel} " in sql and f"{kolom} AS nomor" in sql and "tenant_id = $2" in sql and draf == "draft"


# ---------------- (2) penentu per item ----------------
class _Conn:
    def __init__(self, baris): self.baris, self.sql = baris, []

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return self.baris

    async def fetchval(self, sql, *a): return None


@pytest.mark.parametrize("kind", ["bill", "purchase_order"])
def test_periksa_draf_batal_dan_terbit(kind):
    uid = uuid.uuid4()
    c = _Conn({"nomor": "X-1", "status": "draft"})
    with pytest.raises(HTTPException) as e:
        _jalan(BD._periksa_bagikan(c, CTX, kind, uid))
    assert e.value.status_code == 409 and e.value.detail["code"] == "DOKUMEN_DRAF"
    assert c.sql[0][1] == (uid, T)  # tenant eksplisit
    for st in ("void", "cancelled"):
        with pytest.raises(HTTPException) as e:
            _jalan(BD._periksa_bagikan(_Conn({"nomor": "X-1", "status": st}), CTX, kind, uid))
        assert e.value.detail["code"] == "DOKUMEN_BATAL" and D.LABEL_JENIS[kind] in e.value.detail["message"]
    for st in (("sent", "closed", "partial_received") if kind == "purchase_order" else ("posted", "paid")):
        assert _jalan(BD._periksa_bagikan(_Conn({"nomor": "X-1", "status": st}), CTX, kind, uid))["status"] == st
    with pytest.raises(HTTPException) as e:
        _jalan(BD._periksa_bagikan(_Conn(None), CTX, kind, uid))
    assert e.value.status_code == 404 and e.value.detail["code"] == "DOKUMEN_TAK_ADA"


# ---------------- (3) rute ----------------
def test_rute_bagikan_massal_hanya_po_dan_faktur_pembelian():
    assert BD.MODUL_BAGIKAN == set(SPEC) | set(BUKA)
    for m, (kind, _) in BUKA.items():
        jalur = sorted((x.path, tuple(sorted(x.methods))) for x in BD.ROUTERS[m].routes)
        assert jalur == [("/bulk/pdf", ("POST",)), ("/bulk/share", ("POST",)), ("/bulk/share/preview", ("POST",))], m
        assert BD.MODUL_KIND[m] == kind
    for m in TUTUP:
        assert [x.path for x in BD.ROUTERS[m].routes] == ["/bulk/pdf"], m


def test_inventaris_rute_tulis_memuat_rute_baru():
    inv = (APP.parent / "tests" / "unit" / "inventaris_rute_tulis.txt").read_text()
    for m in BUKA:
        for akhir in ("share", "share/preview"):
            assert f"POST /api/{m}/bulk/{akhir}\n" in inv


# ---------------- (4) izin ----------------
def _izin(metode, jalur):
    return PM.PermissionMiddleware(lambda *a: None, False)._find_permission(jalur, metode)


@pytest.mark.parametrize("prefiks", sorted(BUKA))
def test_izin_eksplisit_modul_pembelian(prefiks):
    kind, modul = BUKA[prefiks]
    assert _izin("POST", f"/api/{prefiks}/bulk/share") == (modul, "E") == _izin("POST", f"/api/{prefiks}/bulk/share/preview")
    for akhir in ("share", "share/preview", "shares/Y/revoke"):
        assert _izin("POST", f"/api/documents/{kind}/X/{akhir}") == (modul, "E"), akhir
    assert _izin("GET", f"/api/documents/{kind}/X/shares") == (modul, "R")
    assert _izin("POST", f"/api/{prefiks}/bulk/share") == _izin("POST", f"/api/documents/{kind}/X/share")


def test_pola_eksplisit_di_atas_penampung_sales_order():
    src = (APP / "middleware" / "permission_middleware.py").read_text()
    penampung = src.index('(r"^/api/documents/[^/]+/[^/]+/(share|share/preview|shares/[^/]+/revoke)$", ["POST"], "sales_order", "E")')
    for k in ("purchase_order", "bill"):
        assert src.index(f'(r"^/api/documents/{k}/[^/]+/(share|share/preview|shares/[^/]+/revoke)$"') < penampung, k


# ---------------- (5) bundel: sent dari tautan ----------------
class _ConnKirim:
    def __init__(self, baris): self.baris, self.args = baris, None

    async def fetch(self, sql, *a):
        assert "FROM document_shares WHERE tenant_id = $1" in sql
        self.args = a
        return self.baris


def _dok(kind, i, sent=None):
    return {"kind": kind, "id": i, "sent": sent or {"state": "none", "sent_at": None, "viewed_at": None}}


def test_lapis_kirim_tautan_menimpa_tanpa_tautan_tetap():
    from datetime import datetime, timezone
    t = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)
    po_kirim = {"state": "sent", "sent_at": "2026-10-30T01:00:00+00:00", "viewed_at": None}
    kel = {"a": [{"key": "po", "docs": [_dok("purchase_order", "P1", dict(po_kirim)), _dok("purchase_order", "P2")]},
                 {"key": "faktur", "docs": [_dok("bill", "B1"), _dok("bill", "B2")]},
                 {"key": "grn", "docs": [_dok("goods_receipt", "G1")]}]}
    c = _ConnKirim([{"kind": "purchase_order", "doc_id": "P2", "sent_at": t, "viewed_at": None},
                    {"kind": "bill", "doc_id": "B1", "sent_at": t, "viewed_at": t}])
    out = _jalan(DB.lapis_kirim(c, T, kel))
    docs = {d["id"]: d["sent"] for g in out["a"] for d in g["docs"]}
    assert docs["P1"] == po_kirim  # tanpa tautan: PO sent_at tetap
    assert docs["P2"] == {"state": "sent", "sent_at": t.isoformat(), "viewed_at": None}
    assert docs["B1"] == {"state": "viewed", "sent_at": t.isoformat(), "viewed_at": t.isoformat()}
    assert docs["B2"]["state"] == "none" and docs["G1"]["state"] == "none"
    assert c.args[0] == T and sorted(c.args[1]) == ["bill", "bill", "purchase_order", "purchase_order"]  # GRN belum bisa dibagikan
    for s in docs.values():
        assert s["state"] in {"none", "sent", "viewed"} and set(s) == {"state", "sent_at", "viewed_at"}
        assert all(v is None or isinstance(v, str) for v in (s["sent_at"], s["viewed_at"]))


@pytest.mark.parametrize("modul,fungsi", [("po_daftar", "dokumen_po"), ("tagihan_daftar", "kelompok_tagihan"),
                                          ("grn_daftar", "kelompok_grn_banyak"), ("uang_muka_vendor_daftar", "kelompok_untuk"),
                                          ("pembayaran_keluar_daftar", "kelompok_untuk")])
def test_lima_pembentuk_bundel_pembelian_berakhir_di_lapis_kirim(modul, fungsi):
    import importlib
    m = importlib.import_module(f"app.services.{modul}")
    src = inspect.getsource(getattr(m, fungsi))
    assert re.search(r"return await lapis_kirim\(conn, \w+, hasil\)", src.rstrip().splitlines()[-1]), (modul, fungsi)
