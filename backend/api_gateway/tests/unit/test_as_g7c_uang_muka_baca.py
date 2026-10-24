"""AS-G7c (24 Okt 2026, desain disetujui MASTER): pratinjau aksi uang muka pelanggan (void/refund/apply/reverse) + pindah
proforma = penentu bersama BACA SAJA. Penulis (inti) memegang kunci yang sama lalu memanggil penentu yang sama.
Perilaku nyata (LAMA == BARU di semua medan kecuali nomor jurnal, pratinjau tak menunggu kunci penulis, penulis||penulis
tetap serial, net 0) = harness scripts/ops/uji-nyata-as-g7c."""
import ast
import inspect
import re
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as CD
from app.services import bank_sync as BS
from app.services import proforma_atribusi as PA
from app.services import uang_muka_rencana as UMR

TULIS = re.compile(r"\b(UPDATE\s+\w+\s+SET|INSERT\s+INTO|DELETE\s+FROM)\b|FOR UPDATE|pg_advisory", re.I)
INTI = {"apply_deposit_core": ("UMR.rencana_terapkan(", 'f"DEPOSIT:{deposit_id}"'),
        "refund_deposit_core": ("UMR.rencana_kembalikan(", 'f"DEPOSIT:{deposit_id}"'),
        "void_deposit_core": ("UMR.rencana_batal(", 'f"DEPOSIT:{deposit_id}"'),
        "reverse_deposit_application_core": ("UMR.rencana_lepas(", 'f"DEPOSIT:{deposit_id}"')}


def _tanpa_doc(fn) -> str:
    src = inspect.getsource(fn)
    n = ast.parse(src).body[0]
    if isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
        doc = ast.get_source_segment(src, n.body[0])
        src = src.replace(doc, "", 1)
    return src


@pytest.mark.parametrize("nama", sorted(INTI))
def test_inti_kunci_lalu_penentu_lalu_tulis(nama):
    src = _tanpa_doc(getattr(CD, nama))
    rencana, kunci = INTI[nama]
    i_k, i_r = src.index(kunci), src.index(rencana)
    tulis = [m.start() for m in re.finditer(r"INSERT INTO|DELETE FROM", src)]
    assert i_k < i_r < min(tulis), nama  # kunci (tak berubah) -> penentu -> tulisan pertama


@pytest.mark.parametrize("fn", [UMR.rencana_terapkan, UMR.rencana_lepas, UMR.rencana_kembalikan, UMR.rencana_batal,
                                UMR.keadaan_sesudah, UMR.jurnal_dan_bank, UMR._neto_liabilitas, CD._rencana_dp])
def test_penentu_tak_menulis_dan_tak_mengunci(fn):
    assert not TULIS.search(_tanpa_doc(fn)), fn.__name__


def test_pola_tulis_bisa_gagal():
    """Kontrol: pola penjaga menangkap tulisan & kunci (bukan penjaga yang selalu hijau)."""
    for q in ("INSERT INTO journal_lines", "UPDATE customer_deposits SET x", "DELETE FROM customer_deposits",
              "SELECT 1 FROM x FOR UPDATE", "SELECT pg_advisory_xact_lock(1)"):
        assert TULIS.search(q), q
    assert not TULIS.search("SELECT * FROM customer_deposits WHERE id = $1")


def test_pratinjau_read_only_tanpa_inti():
    p = _tanpa_doc(CD._pratinjau_dp)
    assert "conn.transaction(readonly=True)" in p and "_core(" not in p and "_RENCANA_DP[aksi](" in p
    assert "savepoint" not in p and "rollback" not in p


def test_pindah_pratinjau_read_only_tanpa_penulis():
    src = _tanpa_doc(CD._jalankan_pindah)
    i_p, i_t = src.index("if pratinjau:"), src.index("async with conn.transaction():")
    cabang = src[i_p:i_t]
    assert "conn.transaction(readonly=True)" in cabang and "kunci=False" in cabang and "_tulis_pindah(" not in cabang
    assert "ganti_proforma=" in cabang
    assert "kunci=True" in src[i_t:] and "_tulis_pindah(" in src[i_t:]


def test_peta_pembalik_bank_satu_sumber():
    src = inspect.getsource(BS.create_reversal_bank_transaction)
    assert "JENIS_PEMBALIK.get(" in src and "type_reversal_map" not in src
    assert BS.JENIS_PEMBALIK["withdrawal"] == "deposit" and BS.JENIS_PEMBALIK["deposit"] == "withdrawal"
    assert "JENIS_PEMBALIK.get(" in inspect.getsource(UMR.rencana_batal)


class _Baca:
    """Koneksi tiruan pratinjau: hanya SELECT; tulisan/kunci = AssertionError."""

    def __init__(self, jawab):
        self.jawab, self.sql = jawab, []

    def _cek(self, q):
        self.sql.append(q)
        assert not TULIS.search(q), f"menulis/mengunci: {q[:80]}"

    async def fetchrow(self, q, *a):
        self._cek(q)
        return self.jawab(q, a, "row")

    async def fetchval(self, q, *a):
        self._cek(q)
        return self.jawab(q, a, "val")

    async def fetch(self, q, *a):
        self._cek(q)
        return self.jawab(q, a, "rows")

    async def execute(self, q, *a):
        self._cek(q)


T = "t-uji"
DEP = uuid.uuid4()
DP_AKUN, AR_AKUN, INV, PLG = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
CTX = {"tenant_id": T, "user_id": "u1"}


@pytest.fixture
def lingkungan(monkeypatch):
    async def akun(conn, tid, role):
        return DP_AKUN if "DEPOSIT" in str(role) else AR_AKUN

    async def label(conn, tid, did, kapital=False):
        return "Uang Muka" if kapital else "Uang muka"

    async def nol(*a, **k):
        return None

    async def sisa_dp(conn, tid, did):
        return Decimal("100000")

    async def sisa_faktur(conn, tid, iid):
        return Decimal("60000")

    async def tgl(conn, tid):
        from datetime import date
        return date(2026, 10, 24)
    monkeypatch.setattr(UMR, "resolve_account_id_by_role", akun)
    monkeypatch.setattr(UMR, "label_deposit", label)
    monkeypatch.setattr(CD, "_assert_ar_side_is_ar_trade", nol)
    monkeypatch.setattr(CD, "compute_deposit_remaining", sisa_dp)
    monkeypatch.setattr(CD, "get_invoice_remaining_from_journal", sisa_faktur)
    monkeypatch.setattr(CD, "tanggal_dokumen", tgl)


def _jawab_terapkan(q, a, jenis):
    if "FROM customer_deposits" in q:
        return {"id": DEP, "status": "posted", "deposit_number": "DEP-1", "customer_id": PLG, "amount_applied": 0,
                "amount_refunded": 0}
    if "FROM sales_invoices" in q:
        return {"id": INV, "invoice_number": "INV-1", "customer_id": uuid.UUID(PLG), "customer_name": "X", "total_amount": 1, "status": "posted"}
    if "FROM customer_deposit_applications" in q:
        return None
    raise AssertionError(q)


def _body(*jumlah):
    return SimpleNamespace(application_date=None,
                           applications=[SimpleNamespace(invoice_id=str(INV), amount=Decimal(j)) for j in jumlah])


@pytest.mark.asyncio
async def test_terapkan_rencana_baris_jurnal_tanpa_tulis(lingkungan):
    p = await UMR.rencana_terapkan(_Baca(_jawab_terapkan), CTX, DEP, _body("40000"))
    [it] = p["items"]
    assert it["jurnal"]["source_type"] == "DEPOSIT_APPLICATION" and it["jurnal"]["total"] == Decimal("40000")
    assert [(a, d, k) for a, d, k, _ in it["jurnal"]["lines"]] == [(DP_AKUN, Decimal("40000"), 0), (AR_AKUN, 0, Decimal("40000"))]
    assert p["total"] == Decimal("40000")


@pytest.mark.asyncio
@pytest.mark.parametrize("jumlah,pesan", [(("30000", "30000"), "Uang muka ini sudah diterapkan ke faktur itu."),
                                          (("40000", "30000"), "Nilai penerapan melebihi sisa tagihan faktur.")])
async def test_terapkan_dua_baris_faktur_sama_galat_sama_dengan_tulis_berurutan(lingkungan, jumlah, pesan):
    """Penulis lama menulis baris 1 dulu: sisa faktur 60.000 - 40.000 = 20.000 < 30.000 -> 'melebihi'; 30.000 + 30.000 muat ->
    baris 2 ditolak 'sudah diterapkan' (aplikasi aktif baris 1). Penentu mensimulasikan efek baris sebelumnya."""
    with pytest.raises(HTTPException) as e:
        await UMR.rencana_terapkan(_Baca(_jawab_terapkan), CTX, DEP, _body(*jumlah))
    assert e.value.status_code == 400 and e.value.detail == pesan


def test_sisa_faktur_sesudah():
    p = {"items": [{"invoice_key": "a", "amount": Decimal("10")}, {"invoice_key": "b", "amount": Decimal("5")}]}
    assert UMR.sisa_faktur_sesudah("apply", p, {"a": Decimal("30"), "b": Decimal("5")}) == {"a": Decimal("20"), "b": Decimal("0")}
    r = {"sudah": False, "app_row": {"invoice_id": "a"}, "amount": Decimal("7")}
    assert UMR.sisa_faktur_sesudah("reverse", r, {"a": Decimal("1")}) == {"a": Decimal("8")}


@pytest.mark.asyncio
@pytest.mark.parametrize("aksi,dipakai,kembali,total,status", [
    ("apply", "0", "0", "100000", "applied"), ("apply", "0", "0", "40000", "partial"),
    ("refund", "60000", "0", "40000", "applied"), ("reverse", "100000", "0", "100000", "posted")])
async def test_keadaan_sesudah_mengikuti_aturan_trigger(lingkungan, aksi, dipakai, kembali, total, status, monkeypatch):
    async def neto(conn, tid, jid):
        return Decimal("-100000")
    monkeypatch.setattr(UMR, "_neto_liabilitas", neto)

    def jawab(q, a, jenis):
        if "FROM customer_deposits" in q:
            return {"status": "partial" if dipakai != "0" or kembali != "0" else "posted", "amount": Decimal("100000"),
                    "amount_applied": Decimal(dipakai), "amount_refunded": Decimal(kembali)}
        if "FROM customer_deposit_applications" in q:
            return Decimal(dipakai)
        if "FROM customer_deposit_refunds" in q:
            return Decimal(kembali)
        raise AssertionError(q)
    t = Decimal(total)
    p = {"apply": {"total": t}, "refund": {"jurnal": {"total": t}}, "reverse": {"amount": t, "original_journal_id": 1}}[aksi]
    h = await UMR.keadaan_sesudah(_Baca(jawab), T, DEP, aksi, p)
    assert h["status"] == status and h["exists"] is True


@pytest.mark.asyncio
async def test_atribusi_ganti_proforma_di_memori(monkeypatch):
    """muat_atribusi(ganti_proforma) = seperti sesudah UPDATE proforma_id, tanpa menulis."""
    so, p1, p2, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tangkap = {}

    async def per(conn, tid, so_ids):
        return [{"id": d, "status": "posted", "amount": Decimal("10"), "proforma_id": p1, "created_at": None, "so_id": so,
                 "terima": Decimal("10"), "terap": Decimal("0"), "lain": Decimal("0"), "neto": Decimal("10")}]

    def atribusikan(pros, deps):
        tangkap["deps"] = deps
        return {}

    class C:
        async def fetch(self, q, *a):
            return []
    import app.services.uang_muka_pesanan as UMP
    monkeypatch.setattr(UMP, "per_uang_muka", per)
    monkeypatch.setattr(PA, "atribusikan", atribusikan)
    await PA.muat_atribusi(C(), T, [so], ganti_proforma={d: p2})
    assert [x["proforma_id"] for x in tangkap["deps"]] == [p2]
    await PA.muat_atribusi(C(), T, [so])
    assert [x["proforma_id"] for x in tangkap["deps"]] == [p1]


@pytest.mark.asyncio
async def test_rencana_pindah_tanpa_kunci_baca_saja_dan_kontrol_berkunci(monkeypatch):
    so = uuid.uuid4()

    async def order(conn, tid, sid, pid):
        return so
    monkeypatch.setattr(CD, "resolve_order_id_for_deposit", order)

    async def label(*a, **k):
        return "Uang muka"
    monkeypatch.setattr(CD, "label_deposit", label)

    def jawab(q, a, jenis):
        if "FROM customer_deposits" in q:
            return {"id": DEP, "deposit_number": "DEP-1", "amount": Decimal("10"), "status": "posted", "customer_id": "c1",
                    "sales_order_id": so, "proforma_id": None}
        if "FROM proformas" in q:
            return None
        raise AssertionError(q)
    body = SimpleNamespace(target_proforma_id=str(uuid.uuid4()), reason=None)
    r = await CD._rencana_pindah(_Baca(jawab), T, DEP, body, kunci=False)
    assert [b["code"] for b in r["blocks"]] == ["PROFORMA_TIDAK_VALID"]
    with pytest.raises(AssertionError):  # kontrol: jalur aksi (kunci=True) MEMANG mengunci -> tiruan baca-saja meledak
        await CD._rencana_pindah(_Baca(jawab), T, DEP, body, kunci=True)
