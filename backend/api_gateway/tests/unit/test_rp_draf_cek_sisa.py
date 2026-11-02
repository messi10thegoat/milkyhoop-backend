"""DRAF-CEK-SISA (2 Nov 2026; putusan LANGSUNG pemilik: posting draf memeriksa ulang sisa & menolak, cegah piutang minus).
SPEK: rute posting draf memanggil _periksa_sisa_draf(kunci=True) SEBELUM _post_payment; pratinjau posting memakai penentu
YANG SAMA (kunci=False); kalimat SATU sumber dengan buat; SETIAP pemanggil _post_payment lewat pemeriksaan sisa."""
import ast
import asyncio
import inspect
import pathlib
import uuid
from decimal import Decimal

from app.routers import receive_payments as RP
from app.services import teks_galat as tg

APP = pathlib.Path(RP.__file__).resolve().parents[1]


def _j(c):
    return asyncio.run(c)


class Conn:
    def __init__(self, alok):
        self.alok, self.q = alok, []

    async def fetch(self, sql, *a):
        self.q.append(" ".join(sql.split()))
        return self.alok

    async def fetchval(self, sql, *a):
        self.q.append(" ".join(sql.split()))
        return None


def test_kalimat_satu_sumber():
    assert RP._kalimat_alokasi_melebihi(Decimal("600000"), Decimal("500000")) == \
        f"Alokasi {tg.rp(Decimal('600000'))} melebihi sisa tagihan faktur {tg.rp(Decimal('500000'))}."
    assert "detail=_kalimat_alokasi_melebihi(alloc.amount_applied, invoice_remaining)" in inspect.getsource(RP._periksa_penerimaan)


def test_periksa_sisa_draf_blok_dan_kunci(monkeypatch):
    i1, i2 = uuid.uuid4(), uuid.uuid4()
    sisa = {i1: Decimal("500000"), i2: Decimal("1000000")}

    async def rem(conn, tid, iid):
        return sisa[iid]
    monkeypatch.setattr(RP, "get_invoice_remaining_from_journal", rem)
    c = Conn([{"invoice_id": i1, "amount_applied": Decimal("600000")}, {"invoice_id": i2, "amount_applied": Decimal("1000000")}])
    b = _j(RP._periksa_sisa_draf(c, "t", uuid.uuid4(), kunci=True))
    assert b == [{"code": "RP_ALLOCATION_EXCEEDS", "message": RP._kalimat_alokasi_melebihi(Decimal("600000"), Decimal("500000"))}]
    assert sum("FOR UPDATE" in q for q in c.q) == 2 and "<> 'reversed'" in c.q[0]
    c2 = Conn([{"invoice_id": i1, "amount_applied": Decimal("500000")}])
    assert _j(RP._periksa_sisa_draf(c2, "t", uuid.uuid4(), kunci=False)) == [] and not any("FOR UPDATE" in q for q in c2.q)


def test_rute_posting_dan_pratinjau_memakai_penentu_yang_sama():
    r = inspect.getsource(RP.post_receive_payment)
    assert r.index("_periksa_sisa_draf(conn, ctx[\"tenant_id\"], payment_id, kunci=True)") < r.index("_post_payment(conn, ctx, payment_id)")
    # blok pertama WAJIB menghentikan posting dengan 400 berkalimat (S1: cek tanpa raise -> merah di unit, bukan hanya nyata)
    i = r.index("_blok_sisa = await _periksa_sisa_draf(")
    assert 'if _blok_sisa:\n                    raise HTTPException(status_code=400, detail=_blok_sisa[0]["message"])' in r[i:i + 400]
    assert "_periksa_sisa_draf(conn, tid, payment_id, kunci=False)" in inspect.getsource(RP._pratinjau_draf_penerimaan)


def test_setiap_pemanggil_post_payment_lewat_pemeriksaan_sisa():
    """Pemindai: _post_payment hanya boleh dipanggil oleh buat_penerimaan (alokasi baru diperiksa _periksa_penerimaan) dan
    post_receive_payment (draf diperiksa _periksa_sisa_draf). Pemanggil lain = jalur posting draf tanpa cek -> merah."""
    pemanggil = set()
    for p in list((APP / "routers").glob("*.py")) + list((APP / "services").rglob("*.py")):
        src = p.read_text(errors="ignore")
        if "_post_payment(" not in src:
            continue
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name != "_post_payment":
                if any(isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", "")) == "_post_payment" for c in ast.walk(n)):
                    pemanggil.add(f"{p.name}:{n.name}")
    assert pemanggil == {"receive_payments.py:buat_penerimaan", "receive_payments.py:post_receive_payment"}, pemanggil
    assert "_periksa_penerimaan(conn, ctx, body)" in inspect.getsource(RP.buat_penerimaan)
