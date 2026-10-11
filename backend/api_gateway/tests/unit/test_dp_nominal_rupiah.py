"""DP nominal = sumber kebenaran, IDR rupiah penuh, kelebihan bayar = uang muka pesanan, pindah uang muka antar-proforma
(11 Okt 2026, putusan pemilik lewat MASTER, pola NetSuite). Latar: Pergatsi SO-2610-0015 -- DP diketik 5.000.000 di
Penawaran, persen tampilan 62.31 dipakai ulang oleh proforma -> 5.000.377,50 (float 2 desimal) -> "Menunggu DP".
Law 9/25 (Decimal HALF_UP), Law 1/16 (posisi dari jurnal, tanpa toleransi), Law 2 (pindah = nol jurnal)."""
import ast
import asyncio
import os
import re
import uuid
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as CD
from app.routers import proformas as PF
from app.services import default_dokumen as DD
from app.services import proforma_terbayar as PT

APP = Path(PF.__file__).resolve().parent.parent


def _src(rel):
    return (APP / rel).read_text()


def _fungsi(rel, nama):
    t = _src(rel)
    for n in ast.walk(ast.parse(t)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nama:
            return ast.get_source_segment(t, n)
    raise AssertionError(f"{nama} tak ada di {rel}")


# ---------------- B: persen -> nominal SEKALI, Decimal HALF_UP ke RUPIAH ----------------
@pytest.mark.parametrize("total,persen,harap", [
    ("8025000", "62.31", "5000378"),      # Pergatsi: dulu 5000377.50
    ("100000000", "62.31", "62310000"),
    ("1001", "50", "501"),                # 500,5 -> HALF_UP
    ("1005", "10", "101"),                # 100,5 -> 101 (banker's rounding = 100)
])
def test_nominal_dari_persen_rupiah_half_up(total, persen, harap):
    n = DD.nominal_dari_persen(Decimal(total), Decimal(persen))
    assert n == Decimal(harap) and n == n.to_integral_value()


def test_hitung_dp_so_memakai_rumus_yang_sama():
    assert DD.hitung_dp(Decimal("8025000"), Decimal("62.31"), None)["dp_amount"] == DD.nominal_dari_persen(8025000, "62.31")


def test_proforma_buat_dan_ubah_memakai_rumus_rupiah_bukan_float():
    src = _src("routers/proformas.py")
    assert "* percent / 100.0" not in src and "percent / 100" not in src  # float 2 desimal = akar Pergatsi
    assert "nominal_dari_persen(order[\"total_amount\"], body.percent_of_order)" in _fungsi("routers/proformas.py", "create_proforma")
    assert "nominal_dari_persen(order[\"total_amount\"], body.percent_of_order)" in _fungsi("routers/proformas.py", "_rencana_ubah")


def test_nominal_diketik_juga_rupiah():
    assert PF.rupiah(Decimal("5000377.50")) == Decimal("5000378") and PF.rupiah("1500.4") == Decimal("1500")
    assert "rupiah(body.amount)" in _fungsi("routers/proformas.py", "create_proforma")
    assert "rupiah(body.amount)" in _fungsi("routers/proformas.py", "_rencana_ubah")


# ---------------- A: konversi Penawaran -> SO menandai nominal 'manual' ----------------
def test_konversi_penawaran_ke_so_mengisi_dp_amount_source():
    t = _src("routers/quotes.py")
    m = re.search(r"INSERT INTO sales_orders \((.*?)\) VALUES \((.*?)\)", t, re.S)
    kolom = [c.strip() for c in m.group(1).split(",")]
    nilai = [v.strip() for v in m.group(2).split(",")]
    assert len(kolom) == len(nilai)
    assert dict(zip(kolom, nilai))["dp_amount_source"] == "$21"
    assert '"manual" if quote["dp_amount"] is not None else None' in t


# ---------------- P2: kelebihan bayar = uang muka pesanan; sisa tak pernah negatif ----------------
@pytest.mark.parametrize("amount,bayar,paid,sisa,lebih,lunas", [
    ("5000000", "3000000", "3000000", "2000000", "0", False),
    ("5000000", "5000000", "5000000", "0", "0", True),
    ("5000000", "6000000", "5000000", "0", "1000000", True),       # S3: dulu outstanding -1.000.000
    ("5000377.50", "5000000", "5000000", "377.50", "0", False),     # Pergatsi lama: tetap terlihat, tak disembunyikan
    ("5000000", "0", "0", "5000000", "0", False),
])
def test_bayar_proforma(amount, bayar, paid, sisa, lebih, lunas):
    b = PT.bayar_proforma(Decimal(amount), Decimal(bayar))
    assert (b["paid_amount"], b["outstanding_amount"], b["excess_amount"], b["is_fully_paid"]) == (
        Decimal(paid), Decimal(sisa), Decimal(lebih), lunas)


def _baris(amount):
    return {"id": uuid.uuid4(), "proforma_number": "PRO-X", "proforma_date": None, "due_date": None,
            "sales_order_id": uuid.uuid4(), "customer_id": None, "customer_name": "c", "purpose": "DP",
            "percent_of_order": None, "amount": Decimal(amount), "currency": "IDR", "terms": None, "notes": None,
            "payment_bank_name": None, "payment_account_number": None, "payment_account_holder": None,
            "status": "issued", "issued_at": None, "cancelled_at": None, "cancelled_reason": None,
            "created_at": None, "updated_at": None}


def test_serialize_proforma_kontrak_p2():
    d = PF.serialize_proforma(_baris("5000000"), "SO-1", 6000000.0)
    assert d["paid_amount"] == 5000000.0 and d["outstanding_amount"] == 0.0 and d["excess_amount"] == 1000000.0
    assert d["is_fully_paid"] is True
    d = PF.serialize_proforma(_baris("5000000"), "SO-1", 3000000.0)
    assert (d["paid_amount"], d["outstanding_amount"], d["excess_amount"]) == (3000000.0, 2000000.0, 0.0)


def test_pdf_proforma_memakai_aturan_p2():
    src = _src("routers/proformas.py")
    assert '"outstanding_amount": round(amount - paid, 2)' not in src
    assert src.count('bayar_proforma(row["amount"], paid)') == 3


# ---------------- D: posisi TANPA toleransi (kurang Rp1 pun = belum lunas) ----------------
def test_posisi_tanpa_toleransi():
    src = _fungsi("services/so_posisi.py", "fakta_daftar")
    assert 'terbayar.get(p["id"], {}).get("paid", NOL) < Decimal(str(p["amount"]))' in src
    from app.services.so_posisi import posisi
    assert posisi("confirmed", Decimal("3025000"), [{"purpose": "DP", "termin_ke": None}], False, Decimal(0), False,
                  Decimal("5000000"))[0] == "Menunggu DP"


# ---------------- P1: pindah uang muka antar-proforma ----------------
def test_rute_pindah_terpetakan_izin_p():
    t = _src("middleware/permission_middleware.py")
    assert '(r"^/api/customer-deposits/[^/]+/move-proforma$", ["POST"], "customer_deposit", "P")' in t
    assert '(r"^/api/customer-deposits/[^/]+/move-proforma/preview$", ["POST"], "customer_deposit", "P")' in t


def test_rute_pindah_ada_di_tabel_rute_router():
    jalur = {(m, r.path) for r in CD.router.routes for m in getattr(r, "methods", [])}
    assert ("POST", "/{deposit_id}/move-proforma") in jalur and ("POST", "/{deposit_id}/move-proforma/preview") in jalur


def test_penulis_pindah_nol_jurnal_nol_bank():
    src = _fungsi("routers/customer_deposits.py", "_tulis_pindah")
    tulis = re.findall(r"(INSERT INTO|UPDATE|DELETE FROM)\s+(\w+)", src)
    assert tulis == [("UPDATE", "customer_deposits")], tulis
    assert "SET proforma_id = $3" in src and "proforma_id IS NOT DISTINCT FROM $4" in src  # hanya tautan, cek-lalu-ubah
    assert "DEPOSIT_PROFORMA_MOVED" in src


class Konek:
    """Koneksi tiruan untuk penentu pindah."""
    def __init__(self, dep, tujuan, lama_nomor=None):
        self.dep, self.tujuan, self.lama_nomor, self.log = dep, tujuan, lama_nomor, []

    async def fetchrow(self, sql, *a):
        sql = " ".join(sql.split())
        self.log.append(sql)
        if "FROM customer_deposits" in sql:
            return self.dep
        if "FROM proformas" in sql and "SELECT id, proforma_number, status" in sql:
            return self.tujuan
        if "SELECT sales_order_id FROM proformas" in sql:
            return {"sales_order_id": self.dep["sales_order_id"]}
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        return self.lama_nomor

    async def execute(self, sql, *a):
        self.log.append(" ".join(sql.split()) + " " + repr(a))


SO, CUST, PF_LAMA, PF_BARU = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _dep(**kw):
    d = {"id": uuid.uuid4(), "deposit_number": "DEP-1", "amount": Decimal("5000000"), "status": "posted",
         "customer_id": CUST, "sales_order_id": SO, "proforma_id": PF_LAMA}
    d.update(kw)
    return d


def _pf(**kw):
    d = {"id": PF_BARU, "proforma_number": "PRO-BARU", "status": "issued", "sales_order_id": SO, "customer_id": CUST,
         "amount": Decimal("5000000")}
    d.update(kw)
    return d


def _rencana(dep, tujuan, target=None):
    k = Konek(dep, tujuan, "PRO-LAMA")
    r = asyncio.run(CD._rencana_pindah(k, "t", dep["id"], CD.PindahProformaRequest(target_proforma_id=str(target or PF_BARU))))
    return r, k


def test_pindah_sah_tanpa_blok_dan_mengunci_so():
    r, k = _rencana(_dep(), _pf())
    assert r["blocks"] == [] and not r["noop"] and r["lama_nomor"] == "PRO-LAMA"
    assert any("pg_advisory_xact_lock" in s and f"PROFORMA_SO:t:{SO}" in s for s in k.log)
    assert any("FOR UPDATE" in s for s in k.log)


@pytest.mark.parametrize("dep_kw,pf_kw,kode", [
    ({"status": "applied"}, {}, "DP_TIDAK_BISA_DIPINDAH"),
    ({"status": "void"}, {}, "DP_TIDAK_BISA_DIPINDAH"),
    ({}, {"status": "draft"}, "PROFORMA_BUKAN_TERBIT"),
    ({}, {"status": "cancelled"}, "PROFORMA_BUKAN_TERBIT"),
    ({}, {"sales_order_id": uuid.uuid4()}, "PROFORMA_BEDA_PESANAN"),
    ({}, {"customer_id": uuid.uuid4()}, "PROFORMA_BEDA_PELANGGAN"),
])
def test_pindah_ditolak(dep_kw, pf_kw, kode):
    r, _ = _rencana(_dep(**dep_kw), _pf(**pf_kw))
    assert kode in [b["code"] for b in r["blocks"]]


def test_pindah_proforma_tak_ada_atau_tenant_lain():
    r, _ = _rencana(_dep(), None)
    assert [b["code"] for b in r["blocks"]] == ["PROFORMA_TIDAK_VALID"]


def test_pindah_ke_tautan_sekarang_noop_idempoten():
    r, _ = _rencana(_dep(proforma_id=PF_BARU), _pf())
    assert r["noop"] and r["blocks"] == []


def test_pindah_lebih_dari_sisa_tujuan_boleh_p2():
    # tujuan 1 jt, uang muka 5 jt -> BUKAN blok (kelebihan = uang muka pesanan, tampil excess_amount)
    r, _ = _rencana(_dep(), _pf(amount=Decimal("1000000")))
    assert r["blocks"] == []


# ---------------- Koreksi nominal proforma TERBIT (11 Okt; pola SAP down-payment request) ----------------
def test_koreksi_izin_u_di_rute_dan_c_di_handler_tak_lebih_longgar_dari_batal():
    t = _src("middleware/permission_middleware.py")
    assert '(r"^/api/proformas/[^/]+/correct-amount(/preview)?$", ["POST"], "proforma", "U")' in t
    # batal/terbit = aturan umum POST /api/proformas -> proforma:C; koreksi menuntut C JUGA di handler
    assert '(r"^/api/proformas", ["POST"], "proforma", "C")' in t
    assert t.index("correct-amount") < t.index('(r"^/api/proformas", ["POST"], "proforma", "C")')  # aturan spesifik lebih dulu
    assert 'await _wajib_izin(request, "C", "proforma")' in _fungsi("routers/proformas.py", "_jalankan_koreksi")


def test_koreksi_izin_gagal_tertutup(monkeypatch):
    from starlette.requests import Request
    import app.services.policy_engine_client as PEC

    class Mesin:
        async def get_user_context(self, **kw): return object()
        async def can(self, *a): return False
    monkeypatch.setattr(PEC, "get_policy_engine", lambda: Mesin())
    r = Request({"type": "http", "method": "POST", "headers": [], "path": "/", "query_string": b"", "state": {}})
    r.state.user = {"tenant_id": "t", "user_id": "u"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(PF._wajib_izin(r, "C", "proforma"))
    assert e.value.status_code == 403

    def meledak():
        raise RuntimeError("engine mati")
    monkeypatch.setattr(PEC, "get_policy_engine", meledak)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PF._wajib_izin(r, "C", "proforma"))
    assert e.value.status_code == 403  # galat -> TOLAK, bukan lolos


def test_koreksi_rute_ada_dan_penulis_hanya_proforma_nol_jurnal():
    jalur = {(m, r.path) for r in PF.router.routes for m in getattr(r, "methods", [])}
    assert ("POST", "/{proforma_id}/correct-amount") in jalur and ("POST", "/{proforma_id}/correct-amount/preview") in jalur
    src = _fungsi("routers/proformas.py", "_tulis_koreksi")
    assert re.findall(r"(INSERT INTO|UPDATE|DELETE FROM)\s+(\w+)", src) == [("UPDATE", "proformas")]
    assert "percent_of_order = NULL" in src and "status = 'issued' AND amount = $4" in src  # nominal = sumber; cek-lalu-ubah
    assert 'f"Nominal dikoreksi {tg.rp(r[\'lama\'])} → {tg.rp(r[\'baru\'])}: {alasan}"' in src


class KonekKoreksi:
    def __init__(self, sisa):
        self.sisa = sisa

    async def fetchrow(self, sql, *a):
        return {"id": a[0], "order_number": "SO-1", "customer_id": None, "customer_name": "c", "total_amount": Decimal("8025000"),
                "status": "confirmed", "payment_bank_name": None, "payment_account_number": None, "payment_account_holder": None}


def _koreksi(monkeypatch, status="issued", amount="5000000", reason="salah nominal", sisa=Decimal("8025000")):
    dicatat = {}

    async def pagar(conn, tid, so, total, amt, exclude_id=None):
        dicatat["exclude_id"], dicatat["amt"] = exclude_id, amt
        if Decimal(str(amt)) > sisa:
            raise HTTPException(status_code=400, detail="melebihi sisa yang bisa ditagih")
    monkeypatch.setattr(PF, "assert_within_order_total", pagar)
    cur = {"id": uuid.uuid4(), "sales_order_id": uuid.uuid4(), "status": status, "amount": Decimal("5000377.50")}
    r = asyncio.run(PF._rencana_koreksi(KonekKoreksi(sisa), {"tenant_id": "t"}, cur,
                                        PF.KoreksiNominalRequest(amount=Decimal(amount), reason=reason)))
    return r, cur, dicatat


def test_koreksi_pergatsi_sah_plafon_mengecualikan_proforma_ini(monkeypatch):
    r, cur, d = _koreksi(monkeypatch)
    assert r["blocks"] == [] and r["baru"] == Decimal("5000000") and r["lama"] == Decimal("5000377.50") and not r["noop"]
    assert d["exclude_id"] == cur["id"]


@pytest.mark.parametrize("kw,kode", [
    ({"status": "draft"}, "PROFORMA_NOT_ISSUED"),
    ({"status": "cancelled"}, "PROFORMA_NOT_ISSUED"),
    ({"reason": "   "}, "CORRECT_REASON_REQUIRED"),
    ({"reason": None}, "CORRECT_REASON_REQUIRED"),
    ({"amount": "0"}, "PROFORMA_AMOUNT_INVALID"),
    ({"amount": "9000000", "sisa": Decimal("8025000")}, "PROFORMA_EXCEEDS_BILLABLE"),   # merah: melebihi sisa bisa ditagih
])
def test_koreksi_ditolak(monkeypatch, kw, kode):
    r, _, _ = _koreksi(monkeypatch, **kw)
    assert kode in [b["code"] for b in r["blocks"]]


def test_koreksi_nominal_sama_noop_dan_nominal_dibulatkan(monkeypatch):
    r, _, _ = _koreksi(monkeypatch, amount="5000377.50")
    assert r["baru"] == Decimal("5000378") and not r["noop"]          # rupiah penuh, bukan no-op
    r, _, _ = _koreksi(monkeypatch, amount="5000000.4")
    assert r["baru"] == Decimal("5000000")
