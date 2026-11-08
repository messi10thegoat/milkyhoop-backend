"""ROW (n) 8 Nov 2026: SATU penanda baris mutasi bank batal (kasbank_tulis.tandai_baris_void: status VOIDED + voided_by + voided_at +
void_reason) untuk void manual kasbank_v2, void biaya, dan void transfer. Dulu UPDATE disalin tiga kali dan void transfer hanya
mengisi status + voided_at (uji nyata TRF-2611-0003 kaos: aktor dan alasan kosong di baris asal)."""
import asyncio
import inspect
import re

from app.routers import bank_transfers as BT, expenses as EX, kasbank_v2 as KB
from app.services import kasbank_tulis as KT
from tests.unit.test_bug116_void_transfer import TRF, T, _void

KOLOM_VOID = {"status", "voided_by", "voided_at", "void_reason"}


def _set(sql):
    return {x.split("=")[0].strip() for x in re.search(r"SET (.*?) WHERE", sql, re.S).group(1).split(",")}


def test_void_transfer_baris_asal_memuat_aktor_waktu_alasan(monkeypatch):
    _, exe = _void(monkeypatch)
    upd = [(s, a) for s, a in exe if s.startswith("UPDATE bank_transactions")]
    assert len(upd) == 1, upd
    s, a = upd[0]
    assert _set(s) == KOLOM_VOID and "voided_at = NOW()" in s
    assert a[0] == T and a[1] and a[2] == "salah rekening" and a[3:] == ("transfer", TRF)


class _K:
    def __init__(self):
        self.exe = []

    async def execute(self, sql, *a):
        self.exe.append((" ".join(sql.split()), a))
        return "UPDATE 1"


def test_kontrol_void_manual_per_id_kolom_sama_plus_tenant():
    k = _K()
    asyncio.run(KT.tandai_baris_void(k, T, "u1", "salah input", tx_id="tx1"))
    s, a = k.exe[0]
    assert _set(s) == KOLOM_VOID and "status = 'VOIDED'" in s
    assert "tenant_id = $1" in s and "id = $4" in s and "status <> 'VOIDED'" in s and a == (T, "u1", "salah input", "tx1")


def test_tiga_void_memakai_satu_penanda():
    for fn in (KB.void_transaction, EX._batal_biaya, BT.void_bank_transfer):  # void biaya: inti _batal_biaya (tulis + pratinjau)
        src = inspect.getsource(fn)
        assert "tandai_baris_void(" in src, fn.__name__
    for mod in (KB, EX, BT):  # tak ada salinan UPDATE ... 'VOIDED' lagi di router
        assert not re.search(r"UPDATE bank_transactions\s+SET status = 'VOIDED'", inspect.getsource(mod)), mod.__name__


# ---- (n) tambahan MASTER: GET /bank-accounts/{id}/transactions memuat reference_id (+ jejak batal) untuk FE BUG-116 ----
import ast  # noqa: E402
import pathlib  # noqa: E402
import uuid  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402

from starlette.requests import Request  # noqa: E402

from app.routers import bank_accounts as BA  # noqa: E402
from app.schemas import bank_accounts as SBA  # noqa: E402

RUTE_BA = pathlib.Path(BA.__file__)


def test_semua_kunci_baris_mutasi_dideklarasikan_skema():
    """Pola BUG-097: kunci yang dirakit rute tapi tak ada di response_model DIBUANG diam-diam."""
    pohon = ast.parse(RUTE_BA.read_text())
    f = next(n for n in pohon.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_bank_transactions")
    kamus = [[k.value for k in n.keys if isinstance(k, ast.Constant)] for n in ast.walk(f) if isinstance(n, ast.Dict)]
    kamus = [ks for ks in kamus if "transaction_type" in ks]
    assert kamus and "reference_id" in kamus[0]
    for ks in kamus:
        assert [k for k in ks if k not in SBA.BankTransactionListItem.model_fields] == []


def test_baris_transfer_memuat_reference_id_baris_manual_null(monkeypatch):
    ba_id, trf, u = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    w = datetime(2026, 11, 8, 4, 38, tzinfo=timezone.utc)
    dasar = {"transaction_date": date(2026, 11, 8), "amount": -1000, "running_balance": 0, "description": "x", "payee_payer": None,
             "reference_number": None, "is_reconciled": False, "created_at": w, "source_module": None, "customer_name": None,
             "vendor_name": None, "related_invoice_number": None, "related_bill_number": None, "journal_number": "TRF-1",
             "origin_type": None}
    baris = [{**dasar, "id": uuid.uuid4(), "transaction_type": "transfer_out", "reference_type": "transfer", "reference_id": trf,
              "status": "VOIDED", "voided_by": u, "voided_at": w, "void_reason": "salah rekening"},
             {**dasar, "id": uuid.uuid4(), "transaction_type": "withdrawal", "reference_type": None, "reference_id": None,
              "status": "POSTED", "voided_by": None, "voided_at": None, "void_reason": None}]
    sql = []

    class K:
        async def fetchrow(self, q, *a):
            return {"id": ba_id, "coa_id": uuid.uuid4()}

        async def fetchval(self, q, *a):
            return len(baris)

        async def fetch(self, q, *a):
            sql.append(q)
            return baris

    class P:
        def acquire(self):
            class Cm:
                async def __aenter__(self):
                    return K()

                async def __aexit__(self, *a):
                    return False
            return Cm()

    async def pool():
        return P()
    monkeypatch.setattr(BA, "get_pool", pool)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": T, "user_id": str(u)}
    h = asyncio.run(BA.get_bank_transactions(r, ba_id, None, None, None, None, 0, 50))
    out = SBA.BankTransactionListResponse.model_validate(h).model_dump()["items"]  # lewat response_model, seperti FastAPI
    assert out[0]["reference_id"] == str(trf) and out[0]["void_reason"] == "salah rekening" and out[0]["voided_by"] == str(u)
    assert out[0]["voided_at"] == w.isoformat()
    assert out[1]["reference_id"] is None and out[1]["void_reason"] is None and out[1]["voided_at"] is None
    pilih = re.search(r"SELECT bt\.id(.*?)FROM bank_transactions bt", sql[0], re.S).group(1)  # daftar kolom CTE, bukan JOIN
    assert {"bt.reference_id", "bt.voided_by", "bt.voided_at", "bt.void_reason"} <= set(re.findall(r"bt\.\w+", pilih))
