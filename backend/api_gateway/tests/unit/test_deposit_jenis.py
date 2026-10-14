"""W-20261014-DEPOSIT-JENIS (V422, 14 Okt 2026): jenis penerimaan uang muka 'DP' | 'PELUNASAN' -- LABEL saja.

Dikunci:
- jenis efektif Python (jenis_efektif) == SQL (sql_jenis_efektif) atas SEMUA kombinasi, dievaluasi sungguhan di sqlite
  (COALESCE + CASE + subkueri), termasuk proforma tenant LAIN ber-id sama (tak boleh dipakai).
- V422: CHECK menerima tepat JENIS; peta purpose meliputi tepat purpose proforma (DP/TERMIN/PELUNASAN).
- Create: absen -> purpose proforma -> 'DP'; eksplisit menang (putusan MASTER B); kolom jenis ikut INSERT.
- Kwitansi: "Untuk Pelunasan Pesanan" / "Untuk Uang Muka Pesanan"; ringkasan "Total Pembayaran Diterima" (putusan C).
- Riwayat uang muka + SO menyebut "Pelunasan ..." untuk jenis PELUNASAN.
- Pembukuan TIDAK membaca jenis: jalur posting/penerapan/refund/plafon tak menyebut kolom ini.
"""
import asyncio
import inspect
import itertools
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.routers import customer_deposits as CD
from app.schemas.customer_deposits import CreateCustomerDepositRequest, UpdateCustomerDepositRequest
from app.services import deposit_jenis as DJ
from app.services import so_riwayat as SR
from app.services.pdf_service import get_pdf_service

APP = Path(CD.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations"


# ---------------------------------------------------------------- jenis efektif
@pytest.mark.parametrize("jenis,purpose,harap", [
    (None, None, "DP"), (None, "DP", "DP"), (None, "TERMIN", "DP"), (None, "PELUNASAN", "PELUNASAN"),
    ("DP", "PELUNASAN", "DP"), ("PELUNASAN", "DP", "PELUNASAN"), ("PELUNASAN", None, "PELUNASAN"),
    (None, "ANEH", "DP"),
])
def test_jenis_efektif_python(jenis, purpose, harap):
    assert DJ.jenis_efektif(jenis, purpose) == harap


def _sqlite():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE customer_deposits (id TEXT, tenant_id TEXT, jenis TEXT, proforma_id TEXT)")
    db.execute("CREATE TABLE proformas (id TEXT, tenant_id TEXT, purpose TEXT)")
    return db


def test_sql_sama_dengan_python_semua_kombinasi():
    db = _sqlite()
    kasus = []
    for i, (jenis, purpose) in enumerate(itertools.product([None, "DP", "PELUNASAN"],
                                                           [None, "DP", "TERMIN", "PELUNASAN", "TANPA_PF"])):
        pf = None if purpose == "TANPA_PF" else f"pf{i}"
        if pf:
            db.execute("INSERT INTO proformas VALUES (?, 't1', ?)", (pf, purpose))
        db.execute("INSERT INTO customer_deposits VALUES (?, 't1', ?, ?)", (f"d{i}", jenis, pf))
        kasus.append((f"d{i}", DJ.jenis_efektif(jenis, None if purpose == "TANPA_PF" else purpose)))
    q = f"SELECT cd.id, {DJ.sql_jenis_efektif('cd')} FROM customer_deposits cd"
    hasil = dict(db.execute(q).fetchall())
    assert hasil == dict(kasus)
    assert set(hasil.values()) == {"DP", "PELUNASAN"}


def test_sql_tak_memakai_proforma_tenant_lain():
    db = _sqlite()
    db.execute("INSERT INTO proformas VALUES ('pfX', 't2', 'PELUNASAN')")  # id sama, tenant LAIN
    db.execute("INSERT INTO customer_deposits VALUES ('d1', 't1', NULL, 'pfX')")
    assert db.execute(f"SELECT {DJ.sql_jenis_efektif('cd')} FROM customer_deposits cd").fetchone()[0] == "DP"


def test_sql_alias_nama_tabel():
    db = _sqlite()
    db.execute("INSERT INTO proformas VALUES ('p', 't1', 'PELUNASAN')")
    db.execute("INSERT INTO customer_deposits VALUES ('d', 't1', NULL, 'p')")
    q = f"SELECT {DJ.sql_jenis_efektif('customer_deposits')} FROM customer_deposits"
    assert db.execute(q).fetchone()[0] == "PELUNASAN"


# ---------------------------------------------------------------- migrasi + kontrak skema
def test_v422_check_sama_dengan_jenis():
    sql = (MIG / "V422__customer_deposit_jenis.sql").read_text()
    m = re.search(r"jenis IN \(([^)]*)\)", sql)
    assert m and set(re.findall(r"'([A-Z]+)'", m.group(1))) == set(DJ.JENIS)
    assert "ADD COLUMN IF NOT EXISTS jenis TEXT NULL" in sql
    assert "UPDATE" not in sql.upper().replace("ON UPDATE", "")  # tanpa backfill (putusan A)
    assert (MIG / "V422__customer_deposit_jenis_ROLLBACK.sql").read_text().count("DROP COLUMN IF EXISTS jenis") == 1


def test_peta_purpose_meliputi_purpose_proforma():
    assert set(DJ.PETA_PURPOSE) == {"DP", "TERMIN", "PELUNASAN"}
    assert set(DJ.PETA_PURPOSE.values()) <= set(DJ.JENIS)


def test_skema_jenis():
    dasar = dict(customer_name="X", amount=1, deposit_date="2026-10-14", payment_method="cash", account_id="a")
    assert CreateCustomerDepositRequest(**dasar).jenis is None
    assert CreateCustomerDepositRequest(**dasar, jenis="PELUNASAN").jenis == "PELUNASAN"
    with pytest.raises(Exception):
        CreateCustomerDepositRequest(**dasar, jenis="TERMIN")
    assert UpdateCustomerDepositRequest(jenis="DP").model_dump(exclude_unset=True) == {"jenis": "DP"}


# ---------------------------------------------------------------- create
def _rata(f):
    return " ".join(inspect.getsource(f).split())


def test_create_simpan_jenis_eksplisit_menang_lalu_purpose_lalu_dp():
    src = _rata(CD.create_customer_deposit)
    assert "jenis_simpan = body.jenis" in src
    assert "if jenis_simpan is None:" in src
    assert "SELECT purpose FROM proformas WHERE id = $1 AND tenant_id = $2" in src
    assert "jenis_simpan = jenis_dari_purpose(purpose_pf)" in src
    m = re.search(r"INSERT INTO customer_deposits \((.*?)\) VALUES \((.*?)\) RETURNING", src)
    kolom = [k.strip() for k in m.group(1).split(",")]
    nilai = [v.strip() for v in m.group(2).split(",")]
    assert kolom[-1] == "jenis" and len(kolom) == len(nilai) and nilai[-1] == "$17"


def test_respons_daftar_detail_membawa_jenis_efektif():
    for f in (CD.list_customer_deposits, CD.get_customer_deposit):
        src = _rata(f)
        assert "sql_jenis_efektif(" in src and '"jenis":' in src, f.__name__


def test_pembukuan_tak_membaca_jenis():
    """Label saja: posting, penerapan, refund, void, plafon tak menyebut jenis."""
    for nama in ("_post_deposit", "assert_deposit_within_order_total", "received_total_for_order"):
        f = getattr(CD, nama, None)
        if f is not None:
            assert "jenis" not in inspect.getsource(f), nama


# ---------------------------------------------------------------- kwitansi
def test_label_kwitansi():
    assert CD._deposit_purpose_label("so", "PELUNASAN") == "Pelunasan Pesanan"
    assert CD._deposit_purpose_label(None, "PELUNASAN") == "Pelunasan"
    assert CD._deposit_purpose_label("so", "DP") == "Uang Muka Pesanan"
    assert CD._deposit_purpose_label("so") == "Uang Muka Pesanan"      # pemanggil lama
    assert CD._deposit_purpose_label(None) == "Uang Muka"


def _render(**r):
    svc = get_pdf_service()
    dasar = dict(receipt_number="DEP-1", receipt_date="2026-10-14", payer_name="Budi", amount=1000,
                 method="Tunai", purpose_ref="SO-1", order_total=5000, dp_received_total=1000, order_remaining=4000)
    return svc.jinja_env.get_template("kwitansi.html").render(receipt={**dasar, **r}, tenant={}, generated_at="")


def _teks(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_kwitansi_pelunasan_dan_dp():
    t = _teks(_render(purpose_label=CD._deposit_purpose_label("so", "PELUNASAN")))
    assert "Untuk Pelunasan Pesanan SO-1" in t and "Total Pembayaran Diterima" in t
    assert "Bukti Penerimaan" in t and "Uang Muka" not in t
    t = _teks(_render(purpose_label=CD._deposit_purpose_label("so", "DP")))
    assert "Untuk Uang Muka Pesanan SO-1" in t and "Total Pembayaran Diterima" in t


def test_kwitansi_memuat_jenis_efektif_dari_sql():
    src = _rata(CD.muat_pdf_kwitansi_uang_muka)
    assert 'sql_jenis_efektif("cd")' in src
    assert '_deposit_purpose_label(dep["sales_order_id"], dep["jenis_efektif"])' in src


# ---------------------------------------------------------------- riwayat
class _Konn:
    def __init__(self, dep):
        self.dep = dep

    async def fetchrow(self, sql, *a):
        assert "AS jenis" in sql
        return self.dep

    async def fetch(self, sql, *a):
        if "FROM audit_logs" in sql:
            return list(getattr(self, "audit", []))
        if "customer_deposit_applications" in sql:
            return [{"id": "a1", "invoice_number": "INV-1", "amount_applied": 500,
                     "created_at": datetime(2026, 10, 14, 3, tzinfo=timezone.utc), "created_by": None,
                     "reversed_at": None}]
        return []


def _riwayat_dp(monkeypatch, jenis, audit=()):
    async def _zona(conn, tid):
        return timezone.utc
    monkeypatch.setattr(SR, "zona_tenant", _zona)
    jam = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    dep = {"id": "d1", "deposit_number": "DEP-1", "amount": 1000, "created_at": jam, "created_by": None,
           "posted_at": jam, "posted_by": None, "voided_at": None, "voided_by": None, "voided_reason": None,
           "sales_order_id": None, "proforma_id": None, "order_number": None, "proforma_number": None, "jenis": jenis}

    async def boleh(m):
        return True
    k = _Konn(dep)
    k.audit = audit
    return asyncio.run(SR.riwayat_uang_muka(k, "t1", "d1", boleh))


def test_riwayat_uang_muka_pelunasan(monkeypatch):
    r = _riwayat_dp(monkeypatch, "PELUNASAN")
    teks = [e["ringkas"] for e in r["events"]]
    assert r["jenis"] == "PELUNASAN"
    assert any(t.startswith("Pelunasan DEP-1") and "diterima" in t for t in teks), teks
    assert any(t.startswith("Pelunasan DEP-1") and "diterapkan" in t for t in teks), teks
    assert not any("Uang muka" in t for t in teks), teks


def test_riwayat_uang_muka_dp(monkeypatch):
    teks = [e["ringkas"] for e in _riwayat_dp(monkeypatch, "DP")["events"]]
    assert any(t.startswith("Uang muka DEP-1") for t in teks) and not any("Pelunasan" in t for t in teks)


def _audit(event, ringkas=None):
    meta = {"ringkas": ringkas} if ringkas else {}
    return {"id": "x", "createdAt": datetime(2026, 10, 14, 5, tzinfo=timezone.utc), "eventType": event,
            "userId": None, "entity_type": "customer_deposit", "entity_id": "d1", "entity_number": "DEP-1",
            "metadata": meta, "source": None}


AUDIT_DP = (_audit("DEPOSIT_VOIDED", "Uang muka DEP-1 Rp 1.000 dibatalkan: salah"),
            _audit("DEPOSIT_MOVED", "Uang muka DEP-1 dipindah dari proforma PRO-1 ke proforma PRO-2"),
            _audit("DEPOSIT_APPLICATION_REVERSED"), _audit("DEPOSIT_REFUNDED"))


def test_riwayat_audit_tersimpan_ikut_jenis_pelunasan(monkeypatch):
    """Ringkas audit TERSIMPAN (void/pindah, ditulis 'Uang muka ..') dibaca dengan label jenis efektif (harness nyata
    kaos DEP-2610-0005, 14 Okt: void + pindah tampil 'Uang muka' walau PELUNASAN)."""
    teks = [e["ringkas"] for e in _riwayat_dp(monkeypatch, "PELUNASAN", AUDIT_DP)["events"]]
    assert "Pelunasan DEP-1 Rp 1.000 dibatalkan: salah" in teks, teks
    assert "Pelunasan DEP-1 dipindah dari proforma PRO-1 ke proforma PRO-2" in teks, teks
    assert "Penerapan pelunasan dilepas" in teks and "Pelunasan dikembalikan" in teks, teks
    assert not any("uang muka" in t.lower() for t in teks), teks


def test_riwayat_audit_tersimpan_dp_tak_berubah(monkeypatch):
    teks = [e["ringkas"] for e in _riwayat_dp(monkeypatch, "DP", AUDIT_DP)["events"]]
    assert "Uang muka DEP-1 Rp 1.000 dibatalkan: salah" in teks and "Penerapan uang muka dilepas" in teks, teks


def test_label_ringkas():
    assert DJ.label_ringkas("Uang muka DEP-1 x", "Pelunasan") == "Pelunasan DEP-1 x"
    assert DJ.label_ringkas("Penerapan uang muka DEP-1 dilepas", "Pelunasan") == "Penerapan pelunasan DEP-1 dilepas"
    assert DJ.label_ringkas("Uang muka DEP-1 x", "Uang muka") == "Uang muka DEP-1 x"
    assert DJ.label_ringkas("Uang muka DEP-1 x", None) == "Uang muka DEP-1 x"
    assert DJ.label_ringkas("Faktur INV-1 dibuat", "Pelunasan") == "Faktur INV-1 dibuat"


def test_riwayat_so_memakai_jenis():
    src = _rata(SR.riwayat_so)
    assert 'sql_jenis_efektif("customer_deposits")' in src
    assert 'f"{lb} {n} {_rp(d[\'amount\'])} diterima (diposting)"' in src
    assert "Uang muka {n}" not in src


def test_label_medan_jenis():
    assert SR.label_medan(["jenis"]) == "jenis pembayaran"
