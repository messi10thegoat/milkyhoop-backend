"""
Unit PANDAS: pandas HARUS ada di image gateway, dan jalur yang memakainya
HARUS benar-benar bekerja -- bukan sekadar "tidak crash".

Kenapa ada: 24 Sep 2026 terukur `import pandas` di kontainer gateway =
ModuleNotFoundError. Tiga jalur memakai pandas di dalam try/except yang
MENELAN galat itu, jadi kegagalannya diam:
  * tool_executor._execute_import_bank_statement: deteksi kolom otomatis
    (file_ref tanpa pemetaan kolom) jatuh ke "Column detection error" dan
    impor dikirim TANPA pemetaan -> endpoint memakai default 'date'/'description'.
  * document_processor._parse_structured (CSV/XLSX) -> ValueError selalu.
  * routers/bank_reconciliation.import_statement (CSV/XLSX) -> 500 generik.

Sengaja TIDAK memakai pytest.importorskip: di image tanpa pandas tes ini
harus MERAH, bukan dilewati. Skip = tidak ada sisi merah.

Nol DB, nol storage, nol HTTP, nol LLM: storage/pool/httpx dipalsukan.
"""
import hashlib
import io
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.services import document_processor as DP  # noqa: E402
from app.services import storage_service as SS  # noqa: E402
from app.services.unified_agent import db_utils  # noqa: E402
from app.services.unified_agent import tool_executor as TE  # noqa: E402

TENANT = "kaos-biru-konveksi"
BUCKET = "milkyhoop-documents"
REQ = Path(__file__).resolve().parents[2] / "requirements.txt"

# Kolom bergaya rekening koran bank Indonesia. Tanggal ISO supaya deteksi
# format tanggal menghasilkan nilai yang BEDA dari default (DD/MM/YYYY):
# kalau deteksi tak berjalan, nilai default tak bisa lolos menyamar.
KOLOM = ["Tanggal", "Keterangan", "Debit", "Kredit", "Saldo"]
BARIS = [
    ["2026-09-01", "Setoran tunai", "", "150000", "1150000"],
    ["2026-09-02", "Bayar listrik", "50000", "", "1100000"],
]
ISI_CSV = (",".join(KOLOM) + "\n" + "\n".join(",".join(b) for b in BARIS) + "\n").encode()

# Yang HARUS dikirim ke endpoint impor bila deteksi kolom berjalan.
PETA_DIHARAPKAN = {
    "date_column": "Tanggal",
    "description_column": "Keterangan",
    "debit_column": "Debit",
    "credit_column": "Kredit",
    "balance_column": "Saldo",
    "date_format": "YYYY-MM-DD",
}


def _isi_xlsx(kolom, baris):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(kolom)
    for b in baris:
        # tanggal sebagai TEKS agar sama dengan jalur CSV (sel tanggal Excel
        # akan menjadi Timestamp -> "2026-09-01 00:00:00")
        ws.append([v if v == "" or not re.fullmatch(r"\d+", v) else int(v) for v in b])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ISI_XLSX = _isi_xlsx(KOLOM, BARIS)


# ------------------------------------------------------------------ palsu
class FakeBody:
    def __init__(self, data):
        self._b = io.BytesIO(data)

    def read(self, n=-1):
        return self._b.read(n)

    def close(self):
        pass


class FakeClient:
    def __init__(self):
        self.objek = {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objek:
            raise KeyError(Key)
        return {"Body": FakeBody(self.objek[(Bucket, Key)])}


class FakeStorage:
    def __init__(self):
        self.client = FakeClient()
        self.config = SimpleNamespace(bucket=BUCKET)

    def taruh(self, isi, ext):
        sha = hashlib.sha256(isi).hexdigest()
        self.client.objek[(BUCKET, f"{TENANT}/uploads/chat/{sha}{ext}")] = isi
        return f"chat_upload:{sha}{ext}"


class FakePool:
    """Tier-1 (template tersimpan) kosong: fetchrow None, execute no-op."""

    def __init__(self):
        self.kueri = []

    async def fetchrow(self, q, *a):
        self.kueri.append(q)
        return None

    async def execute(self, q, *a):
        self.kueri.append(q)
        return "OK"


class FakeResp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"data": {"lines_imported": 2}}


@pytest.fixture
def lingkungan(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(SS, "get_storage_service", lambda: storage)
    pool = FakePool()

    async def _pool():
        return pool

    monkeypatch.setattr(db_utils, "get_session_db_pool", _pool)

    kirim = []

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            kirim.append((url, kw))
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return SimpleNamespace(storage=storage, pool=pool, kirim=kirim)


def _executor():
    return SimpleNamespace(
        context=SimpleNamespace(tenant_id=TENANT),
        _build_headers=lambda: {"Authorization": "Bearer x", "Content-Type": "application/json"},
    )


# ======================================================= 0. image = requirements
def test_pandas_terpasang_sesuai_pin_requirements():
    """Image yang menjalankan tes ini memuat pandas PERSIS versi yang di-pin.
    Merah di image lama (ModuleNotFoundError), dan merah bila requirements
    dan image berselisih (mis. image dibangun dari requirements lama)."""
    import pandas  # noqa: F401  -- sengaja bukan importorskip
    import numpy

    pin = {}
    for baris in REQ.read_text().splitlines():
        m = re.match(r"^(pandas|numpy)==([\w.]+)", baris.strip())
        if m:
            pin[m.group(1)] = m.group(2)
    assert pin.get("pandas"), "pandas tidak di-pin (==) di requirements.txt"
    assert pin.get("numpy"), "numpy tidak di-pin (==) di requirements.txt"
    assert pandas.__version__ == pin["pandas"], (pandas.__version__, pin)
    assert numpy.__version__ == pin["numpy"], (numpy.__version__, pin)


# ================================ 1. deteksi kolom otomatis impor rekening koran
async def _impor_tanpa_peta(lingkungan, isi, ext):
    ref = lingkungan.storage.taruh(isi, ext)
    out = await TE.ToolExecutor._execute_import_bank_statement(
        _executor(), {"session_id": "sesi-1", "file_ref": ref, "config": {}}
    )
    assert out["success"] is True, out
    assert len(lingkungan.kirim) == 1
    config = json.loads(lingkungan.kirim[0][1]["data"]["config"])
    return config


@pytest.mark.asyncio
async def test_deteksi_kolom_csv_terkirim_ke_endpoint_impor(lingkungan):
    config = await _impor_tanpa_peta(lingkungan, ISI_CSV, ".csv")
    terdeteksi = {k: config.get(k) for k in PETA_DIHARAPKAN}
    assert terdeteksi == PETA_DIHARAPKAN, config
    assert config["format"] == "csv"
    # tier-1 (template) sungguh ditanya dengan pool palsu -> jalur deteksi berjalan
    assert any("mapping_templates" in q for q in lingkungan.pool.kueri)


@pytest.mark.asyncio
async def test_deteksi_kolom_xlsx_terkirim_ke_endpoint_impor(lingkungan):
    config = await _impor_tanpa_peta(lingkungan, ISI_XLSX, ".xlsx")
    terdeteksi = {k: config.get(k) for k in PETA_DIHARAPKAN}
    assert terdeteksi == PETA_DIHARAPKAN, config
    assert config["format"] == "xlsx"  # format dari ekstensi tak ditimpa deteksi


@pytest.mark.asyncio
async def test_peta_dari_pengguna_tetap_menang(lingkungan):
    ref = lingkungan.storage.taruh(ISI_CSV, ".csv")
    out = await TE.ToolExecutor._execute_import_bank_statement(
        _executor(),
        {"session_id": "sesi-1", "file_ref": ref,
         "config": {"date_column": "Tanggal", "description_column": "Keterangan",
                    "amount_column": "Kredit"}},
    )
    assert out["success"] is True, out
    config = json.loads(lingkungan.kirim[0][1]["data"]["config"])
    assert config["amount_column"] == "Kredit"
    assert "debit_column" not in config  # deteksi tidak dijalankan sama sekali
    assert lingkungan.pool.kueri == []


# ========================================= 2. document_processor._parse_structured
def _prosesor():
    return DP.DocumentProcessor(pool=None)


@pytest.mark.asyncio
async def test_parse_structured_csv_rekening_koran():
    hasil = await _prosesor()._parse_structured(ISI_CSV, "text/csv")
    assert hasil["doc_type_hint"] == "bank_statement", hasil
    assert hasil["raw_text"] == ", ".join(KOLOM)


@pytest.mark.asyncio
async def test_parse_structured_xlsx_rekening_koran():
    hasil = await _prosesor()._parse_structured(
        ISI_XLSX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert hasil["doc_type_hint"] == "bank_statement", hasil
    assert hasil["raw_text"] == ", ".join(KOLOM)


@pytest.mark.asyncio
async def test_parse_structured_csv_faktur_membaca_baris():
    isi = b"Barang,Qty,Harga,Total\nKaos biru,10,50000,500000\nTopi,,25000,\n"
    hasil = await _prosesor()._parse_structured(isi, "text/csv")
    assert hasil["doc_type_hint"] == "invoice", hasil
    items = hasil["line_items"]
    assert [i["description"] for i in items] == ["Kaos biru", "Topi"]
    # Kolom yang punya sel kosong jadi float64 di pandas (NaN), jadi "10"
    # tampil "10.0" (terukur di image baru). Yang dijaga di sini NILAINYA,
    # bukan ejaannya: Decimal membaca keduanya sama.
    assert Decimal(items[0]["quantity"]) == 10
    assert Decimal(items[0]["unit_price"]) == 50000
    assert Decimal(items[0]["total_price"]) == 500000
    # sel kosong -> None (pd.notna), bukan "nan"
    assert items[1]["quantity"] is None and items[1]["total_price"] is None
