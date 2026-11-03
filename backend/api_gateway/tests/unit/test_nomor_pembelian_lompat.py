"""NOMOR PEMBELIAN (3 Nov 2026, CONVERSATIONAL; repro BACKEND2 di PG sekali-pakai master 5958ee069): nomor manual = nomor otomatis
berikutnya dulu membuat SETIAP create otomatis sesudahnya bentrok 400 (penghitung tak pernah maju karena rollback).
- migrasi V475: generator PB-/BILL-/PO- melompati nomor yang sudah ada (pola generate_sales_order_number)
- nomor manual: trim; sudah dipakai -> 409 (bukan 400); balapan serentak (UniqueViolation) -> 409
- PO menerima nomor manual opsional
Perilaku fungsi PL/pgSQL diuji di harness PG sekali-pakai (SALINAN=1); di sini: isi migrasi, pembantu Python, sambungan router."""
import asyncio
import re
from pathlib import Path

import asyncpg
import pytest
from fastapi import HTTPException

from app.routers import bills as RB
from app.routers import purchase_orders as PO
from app.services import bills_service as BS
from tests.unit import test_po_tulis as TPO

MIG = sorted((Path(__file__).resolve().parents[2].parent / "migrations").glob("V*__nomor_pembelian_lompati_terpakai.sql"))


def test_migrasi_tiga_generator_melompati_terpakai():
    assert len(MIG) == 1, MIG
    s = MIG[0].read_text()
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", s, re.M | re.I)   # apply_mig menolak BEGIN/COMMIT
    for fn, tabel, kolom, awal in (("generate_bill_number", "bills", "invoice_number", "p_prefix || '-'"),
                                   ("generate_purchase_bill_number", "bills", "invoice_number", "'PB-'"),
                                   ("generate_purchase_order_number", "purchase_orders", "po_number", "p_prefix || '-'")):
        i = s.index(f"CREATE OR REPLACE FUNCTION public.{fn}(")
        badan = s[i:s.index("$function$;", i)]
        assert "LOOP" in badan and "END LOOP" in badan, fn
        assert re.search(rf"EXIT WHEN NOT EXISTS \(SELECT 1 FROM {tabel} WHERE tenant_id = p_tenant_id AND {kolom} = v_\w+\)", badan), fn
        assert "v_tries > 100000" in badan and "RAISE EXCEPTION" in badan, fn
        assert awal in badan and "LPAD(v_next_number::TEXT, 4, '0')" in badan, fn   # format nomor TIDAK berubah


class _K:
    def __init__(self, terpakai=()):
        self.terpakai, self.sql = set(terpakai), []

    async def fetchval(self, q, *a):
        self.sql.append((" ".join(q.split()), a))
        if "FROM bills WHERE tenant_id = $1 AND invoice_number = $2" in q:
            return 1 if a[1] in self.terpakai else None
        if "generate_purchase_bill_number" in q:
            return "PB-2611-0003"
        raise AssertionError(q)


@pytest.mark.parametrize("manual,harap", [("  PB-2611-0009 ", "PB-2611-0009"), (None, "PB-2611-0003"), ("   ", "PB-2611-0003"), ("", "PB-2611-0003")])
def test_nomor_tagihan_manual_trim_atau_otomatis(manual, harap):
    k = _K()
    assert asyncio.run(BS.nomor_tagihan(k, "t1", manual, "SELECT generate_purchase_bill_number($1)")) == harap
    assert all(a[0] == "t1" for _, a in k.sql)   # tenant eksplisit


def test_nomor_tagihan_manual_terpakai_409_berkalimat():
    with pytest.raises(BS.NomorTerpakai, match="Nomor faktur sudah digunakan"):
        asyncio.run(BS.nomor_tagihan(_K({"PB-2611-0002"}), "t1", "PB-2611-0002", "SELECT generate_purchase_bill_number($1)"))


def test_create_tagihan_v1_v2_memakai_pembantu_satu_aturan():
    src = Path(BS.__file__).read_text()
    assert src.count("await nomor_tagihan(conn, tenant_id, request.get(\"invoice_number\")") == 2
    assert 'request.get("invoice_number")\n                if not invoice_number' not in src


def test_router_tagihan_duplikat_409_bukan_400():
    src = Path(RB.__file__).read_text()
    for nama in ("async def create_bill(request: Request, body: CreateBillRequest):", "async def create_bill_v2("):
        i = src.index(nama)
        blok = src[i:src.index("\n@router.", i)]
        assert "except NomorTerpakai" in blok and "status_code=409" in blok, nama
        j = blok.index("except asyncpg.exceptions.UniqueViolationError")
        assert "status_code=409" in blok[j:j + 300], nama
    assert 'status_code=400,\n            detail="Nomor faktur sudah digunakan' not in src


# ---------------------------------------------------------------- PO manual (jalur nyata create_purchase_order + koneksi palsu)
class _KonPO(TPO.Kon):
    def __init__(self, terpakai=(), unik=False, **kw):
        super().__init__(**kw)
        self.terpakai, self.unik = set(terpakai), unik

    async def fetchval(self, sql, *a):
        q = " ".join(sql.split())
        if "FROM purchase_orders WHERE tenant_id = $1 AND po_number = $2" in q:
            self.log.append(("cek", a))
            return 1 if a[1] in self.terpakai else None
        if q.startswith("INSERT INTO purchase_orders") and self.unik:
            raise asyncpg.exceptions.UniqueViolationError("duplicate key value violates unique constraint uq_po_tenant_number")
        return await super().fetchval(sql, *a)


def test_po_nomor_manual_dipakai_apa_adanya_tanpa_generator(monkeypatch):
    k = _KonPO()
    TPO._pool(monkeypatch, k)
    r = asyncio.run(PO.create_purchase_order(TPO._Req(), TPO._body(po_number="  PO-MANUAL-77 ")))
    assert r["data"]["po_number"] == "PO-MANUAL-77"
    assert not any("generate_purchase_order_number" in q for _, q in k.log if isinstance(q, str))
    ins = next(a for q, a in k.writes if q.startswith("INSERT INTO purchase_orders"))
    assert ins[1] == "PO-MANUAL-77"


def test_po_nomor_kosong_otomatis(monkeypatch):
    k = _KonPO()
    TPO._pool(monkeypatch, k)
    r = asyncio.run(PO.create_purchase_order(TPO._Req(), TPO._body(po_number="   ")))
    assert r["data"]["po_number"] == "PO-2610-0001"


def test_po_nomor_manual_terpakai_409_tanpa_tulis(monkeypatch):
    k = _KonPO(terpakai={"PO-2611-0002"})
    TPO._pool(monkeypatch, k)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PO.create_purchase_order(TPO._Req(), TPO._body(po_number="PO-2611-0002")))
    assert e.value.status_code == 409 and "Nomor PO sudah digunakan" in e.value.detail
    assert not [q for q, _ in k.writes if q.startswith("INSERT INTO purchase_orders")]


def test_po_balapan_serentak_unique_409(monkeypatch):
    k = _KonPO(unik=True)
    TPO._pool(monkeypatch, k)
    with pytest.raises(HTTPException) as e:
        asyncio.run(PO.create_purchase_order(TPO._Req(), TPO._body(po_number="PO-2611-0005")))
    assert e.value.status_code == 409 and "Nomor PO sudah digunakan" in e.value.detail
