"""UMP-PO U-B (2 Nov 2026; putusan LANGSUNG pemilik: TERAPKAN OTOMATIS): uang muka pembelian ber-PO diterapkan otomatis saat posting
FPB dari PO, cermin SI (_auto_apply_so_deposits + plan_so_deposit_application) APA ADANYA (putusan MASTER).

SPEK:
- inti terapkan = badan rute /apply dipindah UTUH (rute: idem mulai -> inti -> idem simpan).
- perencana: tautan PO tagihan (purchase_order_id ATAU baris), uang muka bertaut PO itu + status terbit, FIFO, sisa jurnal,
  jatah min(sisa, sisa tagihan tersisa); vendor beda -> dilewati; skip -> dilewati (tidak memakan sisa tagihan).
- kait: tanpa tautan PO = no-op; satu galat inti -> galat naik (posting batal, tanpa try); tanggal penerapan = tanggal faktur.
- SETIAP fungsi yang memposting tagihan (UPDATE bills ... status_v2 = 'posted') memanggil kait (pemindai + kontrol merah).
- pratinjau posting + GET deposit-plan memakai perencana YANG SAMA; draf terhadap total, terposting terhadap sisa jurnal.
"""
import ast
import asyncio
import inspect
import pathlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services import ump_terap_otomatis as UTO
from app.routers import vendor_deposits as RV
from app.routers import bills as RB

APP = pathlib.Path(UTO.__file__).resolve().parents[1]
T = "tenant-uji"
V, V2 = uuid.uuid4(), uuid.uuid4()


def _j(c):
    return asyncio.run(c)


class Conn:
    def __init__(self, bill, pos, deposits, status_v2="posted", total=Decimal("0")):
        self.bill, self.pos, self.deposits, self.q = bill, pos, deposits, []
        self.status_v2, self.total = status_v2, total

    async def fetchrow(self, sql, *a):
        s = " ".join(sql.split())
        self.q.append((s, a))
        if "status_v2, grand_total" in s:
            return {"status_v2": self.status_v2, "grand_total": self.total}
        return self.bill

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        self.q.append((s, a))
        if "UNION" in s:
            return [{"po_id": p} for p in self.pos]
        return self.deposits

    async def fetchval(self, sql, *a):
        return date(2026, 11, 2)


def _dep(vendor, tgl, nomor):
    return {"id": uuid.uuid4(), "deposit_number": nomor, "deposit_date": tgl, "vendor_id": vendor}


def _sisa(monkeypatch, peta):
    async def f(conn, tid, did):
        return peta[str(did)]
    monkeypatch.setattr(UTO.UMV, "sisa_jurnal", f)


def test_perencana_fifo_vendor_beda_skip_sisa_nol(monkeypatch):
    d1, d2, d3, d4, d5 = (_dep(V, date(2026, 10, 1), "VD-1"), _dep(V2, date(2026, 10, 2), "VD-2"), _dep(V, date(2026, 10, 3), "VD-3"),
                          _dep(V, date(2026, 10, 4), "VD-4"), _dep(V, date(2026, 10, 5), "VD-5"))
    _sisa(monkeypatch, {str(d1["id"]): Decimal("300"), str(d2["id"]): Decimal("999"), str(d3["id"]): Decimal("50"),
                        str(d4["id"]): Decimal("0"), str(d5["id"]): Decimal("400")})
    pid = uuid.uuid4()
    c = Conn({"id": uuid.uuid4(), "vendor_id": V}, [pid], [d1, d2, d3, d4, d5])
    r = _j(UTO.rencana(c, T, uuid.uuid4(), Decimal("500"), skip_ids=[str(d3["id"])]))
    assert [(p["deposit_number"], p["planned_amount"], p["skip_reason"]) for p in r] == [
        ("VD-1", Decimal("300"), None), ("VD-2", Decimal("0"), "Vendor uang muka berbeda dengan vendor faktur."),
        ("VD-3", Decimal("0"), "Dilewati pengguna."), ("VD-4", Decimal("0"), None), ("VD-5", Decimal("200"), None)]
    sql_dep, a = c.q[-1]
    assert "ORDER BY deposit_date, deposit_number, id" in sql_dep and a[1] == [pid] and a[2] == ["posted", "partial"]
    sql_po = next(s for s, _ in c.q if "UNION" in s)
    assert "b.purchase_order_id" in sql_po and "bi.purchase_order_item_id" in sql_po


def test_tanpa_tautan_po_noop(monkeypatch):
    c = Conn({"id": uuid.uuid4(), "vendor_id": V}, [], [])
    assert _j(UTO.rencana(c, T, uuid.uuid4(), Decimal("100"))) == []
    dipanggil = []

    async def inti(*a, **k):
        dipanggil.append(a)
    monkeypatch.setattr(RV, "terapkan_uang_muka_inti", inti)
    assert _j(UTO.terapkan_otomatis(c, {"tenant_id": T, "user_id": None}, uuid.uuid4(), date(2026, 11, 2))) == [] and dipanggil == []


def test_kait_memanggil_inti_dengan_tanggal_faktur_dan_galat_naik(monkeypatch):
    d1 = _dep(V, date(2026, 10, 1), "VD-1")
    _sisa(monkeypatch, {str(d1["id"]): Decimal("300")})
    bill_id = uuid.uuid4()
    c = Conn({"id": bill_id, "vendor_id": V}, [uuid.uuid4()], [d1])

    async def sisa_tagihan(conn, tid, bid):
        return Decimal("1000")
    monkeypatch.setattr(RV, "get_bill_remaining_from_journal", sisa_tagihan)
    dipanggil = []

    class Res:
        application_id, journal_id = uuid.uuid4(), uuid.uuid4()

    async def inti(conn, ctx, did, data):
        dipanggil.append((did, data.bill_id, data.amount, data.applied_date))
        return Res()
    monkeypatch.setattr(RV, "terapkan_uang_muka_inti", inti)
    out = _j(UTO.terapkan_otomatis(c, {"tenant_id": T, "user_id": None}, bill_id, date(2026, 10, 20)))
    assert dipanggil == [(d1["id"], bill_id, Decimal("300"), date(2026, 10, 20))]
    assert out[0]["amount"] == Decimal("300") and out[0]["deposit_number"] == "VD-1"

    async def gagal(conn, ctx, did, data):
        raise HTTPException(status_code=400, detail="periode tertutup")
    monkeypatch.setattr(RV, "terapkan_uang_muka_inti", gagal)
    with pytest.raises(HTTPException):  # (a) cermin SI: tanpa try -> posting batal
        _j(UTO.terapkan_otomatis(c, {"tenant_id": T, "user_id": None}, bill_id, date(2026, 10, 20)))


def test_sisa_untuk_rencana_draf_total_terposting_jurnal(monkeypatch):
    c = Conn(None, [], [], status_v2="draft", total=Decimal("750"))
    assert _j(UTO.sisa_untuk_rencana(c, T, uuid.uuid4())) == Decimal("750")

    async def sisa_tagihan(conn, tid, bid):
        return 120
    monkeypatch.setattr(RV, "get_bill_remaining_from_journal", sisa_tagihan)
    assert _j(UTO.sisa_untuk_rencana(Conn(None, [], [], status_v2="posted", total=Decimal("750")), T, uuid.uuid4())) == Decimal("120")


def test_urai_skip():
    assert UTO.urai_skip(["a,b", " c ", ""]) == ["a", "b", "c"] and UTO.urai_skip(object()) == []


# ---- inti terapkan = rute lama dipindah utuh ----
def test_rute_apply_memakai_inti_di_antara_idem():
    s = inspect.getsource(RV.apply_vendor_deposit)
    assert s.index("idem_buat.mulai_aksi(") < s.index("resp = await terapkan_uang_muka_inti(conn, ctx, deposit_id, data)") \
        < s.index('idem_buat.simpan(conn, ctx, kp, sidik, "VD_APPLY", resp, deposit_id)')
    i = inspect.getsource(RV.terapkan_uang_muka_inti)
    urut = ['"SELECT id FROM bills WHERE id = $1 AND tenant_id = $2 FOR UPDATE"', 'f"VENDOR_DEPOSIT_SALDO:{deposit_id}"',
            'f"DEPOSIT_APPLY:{deposit_id}"', '"SELECT * FROM vendor_deposits WHERE id = $1 AND tenant_id = $2 FOR UPDATE"',
            "'DEPOSIT_APPLICATION'", "UPDATE journal_entries SET status = 'POSTED'", "INSERT INTO vendor_deposit_applications"]
    pos = [i.index(x) for x in urut]
    assert pos == sorted(pos)


# ---- pemindai: setiap jalur posting tagihan memanggil kait ----
def _posting_tanpa_kait(akar=APP):
    temu = []
    for p in sorted(list((akar / "routers").glob("*.py")) + list((akar / "services").glob("*.py"))):
        src = p.read_text(errors="ignore")
        if "UPDATE bills" not in src:
            continue
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(src, n) or ""
                badan = " ".join(seg.split())
                if "UPDATE bills" in badan and "status_v2 = 'posted'" in badan and "terapkan_otomatis(" not in badan:
                    temu.append(f"{p.name}:{n.name}")
    return temu


def test_semua_jalur_posting_tagihan_memanggil_kait():
    assert _posting_tanpa_kait() == []
    src = (APP / "services/bills_service.py").read_text()
    assert src.count("_uto.terapkan_otomatis(") == 3


def test_pemindai_kait_bisa_merah(tmp_path):
    (tmp_path / "routers").mkdir()
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "x.py").write_text(
        "async def a(conn):\n    await conn.execute(\"UPDATE bills SET status_v2 = 'posted' WHERE id = $1\")\n"
        "async def b(conn):\n    await conn.execute(\"UPDATE bills SET status_v2 = 'posted' WHERE id = $1\")\n"
        "    await _uto.terapkan_otomatis(conn, {}, 1)\n")
    assert _posting_tanpa_kait(tmp_path) == ["x.py:a"]


# ---- rute ----
def test_rute_posting_pratinjau_deposit_plan():
    p = inspect.getsource(RB.post_bill)
    assert "skip_deposit_ids=_skip" in p and '{"skip_deposit_ids": sorted(_skip)} if _skip else {}' in p
    pv = inspect.getsource(RB.preview_post_bill)
    assert "UTO.rencana(conn" in pv and '"deposit_applications": UTO.untuk_respons(rencana)' in pv and "deposit_application_journals" in pv
    dp = inspect.getsource(RB.get_bill_deposit_plan)
    assert "UTO.sisa_untuk_rencana" in dp and "UTO.rencana(conn" in dp
    bs = (APP / "services/bills_service.py").read_text()
    assert '"deposit_applications": [{**t, "amount": float(t["amount"])} for t in _terap]' in bs
    assert 'bill["issue_date"], skip_deposit_ids)' in bs
