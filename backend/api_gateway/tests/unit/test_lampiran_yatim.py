"""#30 (25 Sep 2026): hapus draf tak lagi meninggalkan lampiran yatim.

Terukur baca-saja 25 Sep: tautan hub document_attachments yatim (expense kaos 14, grapgrap 4) +
sales_invoice_attachments yatim 3 (kaos, tabel TANPA FK) + objek bill_attachments tertinggal
(FK CASCADE hapus baris, bukan objek).
- Tautan hub -> trigger V308 (semantiknya: gerbang DB scratch scripts/gate_v308_lampiran.sh).
- Berkas milik -> baris dihapus DI transaksi, objek SESUDAH commit; rollback = objek utuh.
"""
import ast
import re
import uuid
from pathlib import Path

import pytest

from app.services import lampiran_milik as LM

APP = Path(LM.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations" / "V308__lepas_tautan_dokumen_saat_entitas_dihapus.sql"
TENANT = "kaos-biru-konveksi"


# ---------- V308 = peta hub (satu sumber) ----------

def _pasangan_migrasi():
    sql = MIG.read_text()
    return set(re.findall(
        r"CREATE TRIGGER trg_lepas_dokumen AFTER DELETE ON (\w+)\s+FOR EACH ROW EXECUTE FUNCTION "
        r"lepas_tautan_dokumen_entitas\('(\w+)'\)", sql))


def _pasangan_hub():
    from app.routers import documents as D
    return {(tabel, jenis) for jenis, calon in D._ENTITAS_BACA.items() for tabel, _ in calon}


def test_trigger_menutup_semua_entitas_hub():
    mig, hub = _pasangan_migrasi(), _pasangan_hub()
    assert len(hub) >= 20
    assert hub - mig == set(), f"entitas hub TANPA trigger: {sorted(hub - mig)}"
    assert mig - hub == set(), f"trigger untuk entitas di luar hub: {sorted(mig - hub)}"


def test_trigger_tiap_tabel_didrop_dulu_idempoten():
    sql = MIG.read_text()
    for tabel, _ in _pasangan_migrasi():
        assert f"DROP TRIGGER IF EXISTS trg_lepas_dokumen ON {tabel};" in sql, tabel


def test_fungsi_trigger_berpagar_jenis_dan_tenant_hanya_tautan():
    sql = MIG.read_text()
    badan = sql.split("CREATE OR REPLACE FUNCTION lepas_tautan_dokumen_entitas()", 1)[1].split("$$;", 1)[0]
    assert "DELETE FROM document_attachments" in badan
    assert "entity_type = TG_ARGV[0]" in badan and "entity_id = OLD.id" in badan
    assert "tenant_id = OLD.tenant_id::text" in badan
    assert "documents" not in badan.replace("document_attachments", "")   # dokumen hub TIDAK dihapus
    assert "AFTER DELETE" in sql and "BEFORE DELETE" not in sql


def test_migrasi_tak_menghapus_yatim_lama():
    sql = MIG.read_text()
    kode = "\n".join(b for b in sql.splitlines() if not b.lstrip().startswith("--"))
    badan_fn = kode.split("$$", 2)
    luar = badan_fn[0] + badan_fn[2]
    # pernyataan pengubah data di luar badan fungsi (bukan "AFTER DELETE" deklarasi trigger)
    assert not re.search(r"^\s*(DELETE|UPDATE|TRUNCATE|INSERT)\b", luar, re.I | re.M)
    assert re.search(r"^\s*(DELETE|UPDATE|TRUNCATE|INSERT)\b", "x;\n  DELETE FROM a;", re.I | re.M)  # bisa merah


# ---------- helper berkas milik ----------

class _ConnSQL:
    def __init__(self, paths):
        self.paths, self.sql = paths, []

    async def fetch(self, q, *a):
        self.sql.append((q, a))
        return [{"file_path": p} for p in self.paths]


@pytest.mark.asyncio
async def test_lepas_berkas_milik_hapus_baris_kembalikan_path():
    c = _ConnSQL(["a/1.pdf", None, "a/2.png"])
    bid = uuid.uuid4()
    assert await LM.lepas_berkas_milik(c, "bills", bid) == ["a/1.pdf", "a/2.png"]
    (q, a), = c.sql
    assert q.startswith("DELETE FROM bill_attachments WHERE bill_id = $1") and "RETURNING file_path" in q
    assert a == (bid,)
    c2 = _ConnSQL([])
    await LM.lepas_berkas_milik(c2, "sales_invoices", bid)
    assert c2.sql[0][0].startswith("DELETE FROM sales_invoice_attachments WHERE invoice_id = $1")
    with pytest.raises(KeyError):
        await LM.lepas_berkas_milik(c2, "expenses", bid)


class _Storage:
    def __init__(self, hasil):
        self.hasil, self.dihapus = hasil, []

    async def delete_file(self, p):
        self.dihapus.append(p)
        h = self.hasil.get(p, True)
        if isinstance(h, Exception):
            raise h
        return h


@pytest.fixture
def storage(monkeypatch):
    from app.services import storage_service as SS

    s = _Storage({})
    monkeypatch.setattr(SS, "get_storage_service", lambda: s)
    return s


@pytest.mark.asyncio
async def test_hapus_objek_false_dan_galat_dihitung_gagal_bersuara(storage, caplog):
    storage.hasil = {"x/gagal.pdf": False, "x/meledak.pdf": RuntimeError("minio")}
    r = await LM.hapus_objek_sesudah_commit(["x/ok.pdf", "x/gagal.pdf", "x/meledak.pdf"], "uji")
    assert r == {"dihapus": 1, "gagal": ["x/gagal.pdf", "x/meledak.pdf"]}
    assert storage.dihapus == ["x/ok.pdf", "x/gagal.pdf", "x/meledak.pdf"]
    teks = caplog.text
    assert "[LAMPIRAN_YATIM]" in teks and "x/gagal.pdf" in teks and "x/meledak.pdf" in teks


@pytest.mark.asyncio
async def test_hapus_objek_kosong_tak_menyentuh_storage(monkeypatch):
    from app.services import storage_service as SS

    def _meledak():
        raise AssertionError("storage tak boleh disentuh")
    monkeypatch.setattr(SS, "get_storage_service", _meledak)
    assert await LM.hapus_objek_sesudah_commit([], "uji") == {"dihapus": 0, "gagal": []}


# ---------- urutan: baris DI transaksi, objek SESUDAH commit ----------

class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.log.append("BEGIN")

    async def __aexit__(self, et, e, tb):
        self.conn.log.append("ROLLBACK" if et else "COMMIT")
        return False


class _ConnUrut:
    """Mencatat urutan: BEGIN / SQL / COMMIT|ROLLBACK; objek dicatat storage ke log yang sama."""

    def __init__(self, baris_induk, paths, gagal_pada=None):
        self.baris_induk, self.paths, self.gagal_pada, self.log = baris_induk, paths, gagal_pada, []

    def transaction(self):
        return _Tx(self)

    def _catat(self, q):
        kunci = " ".join(q.split())[:60]
        self.log.append(kunci)
        if self.gagal_pada and self.gagal_pada in q:
            raise self.gagal_galat

    async def execute(self, q, *a):
        self._catat(q)
        return "OK"

    async def fetchrow(self, q, *a):
        self._catat(q)
        return self.baris_induk

    async def fetchval(self, q, *a):
        self._catat(q)
        return None

    async def fetch(self, q, *a):
        self._catat(q)
        if "RETURNING file_path" in q:
            return [{"file_path": p} for p in self.paths]
        return []


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *e):
                return False
        return _Ctx()


@pytest.fixture
def storage_log(monkeypatch):
    from app.services import storage_service as SS

    class S:
        conn = None

        async def delete_file(self, p):
            self.conn.log.append(f"OBJEK {p}")
            return True
    s = S()
    monkeypatch.setattr(SS, "get_storage_service", lambda: s)
    return s


def _idx(log, awalan):
    return [i for i, x in enumerate(log) if x.startswith(awalan)]


async def _hapus_faktur(monkeypatch, conn):
    from app.routers import sales_invoices as R

    async def _pool():
        return _Pool(conn)
    monkeypatch.setattr(R, "get_pool", _pool)
    monkeypatch.setattr(R, "get_user_context", lambda r: {"tenant_id": TENANT, "user_id": "u1"})
    return await R.delete_invoice(request=None, invoice_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_hapus_draf_faktur_baris_di_tx_objek_sesudah_commit(monkeypatch, storage_log):
    c = _ConnUrut({"id": 1, "invoice_number": "INV-X", "status": "draft"}, ["si/a.pdf", "si/b.pdf"])
    storage_log.conn = c
    res = await _hapus_faktur(monkeypatch, c)
    assert res["success"] is True
    log = c.log
    (b,), (cm,) = _idx(log, "BEGIN"), _idx(log, "COMMIT")
    (lamp,) = _idx(log, "DELETE FROM sales_invoice_attachments")
    (induk,) = _idx(log, "DELETE FROM sales_invoices")
    obj = _idx(log, "OBJEK")
    assert b < lamp < induk < cm, log
    assert [log[i] for i in obj] == ["OBJEK si/a.pdf", "OBJEK si/b.pdf"] and min(obj) > cm, log


@pytest.mark.asyncio
async def test_hapus_draf_faktur_gagal_rollback_objek_utuh(monkeypatch, storage_log):
    from fastapi import HTTPException

    c = _ConnUrut({"id": 1, "invoice_number": "INV-X", "status": "draft"}, ["si/a.pdf"],
                  gagal_pada="DELETE FROM sales_invoices")
    c.gagal_galat = RuntimeError("fk")
    storage_log.conn = c
    with pytest.raises(HTTPException):
        await _hapus_faktur(monkeypatch, c)
    assert "ROLLBACK" in c.log and not _idx(c.log, "OBJEK"), c.log


@pytest.mark.asyncio
async def test_faktur_bukan_draf_tak_menyentuh_lampiran(monkeypatch, storage_log):
    from fastapi import HTTPException

    c = _ConnUrut({"id": 1, "invoice_number": "INV-X", "status": "posted"}, ["si/a.pdf"])
    storage_log.conn = c
    with pytest.raises(HTTPException):
        await _hapus_faktur(monkeypatch, c)
    assert not _idx(c.log, "DELETE") and not _idx(c.log, "OBJEK")


async def _hapus_tagihan(conn):
    from app.services.bills_service import BillsService

    svc = BillsService.__new__(BillsService)
    svc.pool = _Pool(conn)
    return await svc.delete_bill(TENANT, uuid.uuid4(), "u1")


_BILL_DRAF = {"id": 1, "amount_paid": 0, "status": "draft", "status_v2": "draft",
              "accounting_status": "draft", "operational_status": "draft"}


@pytest.mark.asyncio
async def test_hapus_draf_tagihan_objek_sesudah_commit(storage_log):
    c = _ConnUrut(_BILL_DRAF, ["bill/a.pdf"])
    storage_log.conn = c
    res = await _hapus_tagihan(c)
    assert res["success"] is True, res
    log = c.log
    (cm,) = _idx(log, "COMMIT")
    (lamp,) = _idx(log, "DELETE FROM bill_attachments")
    (induk,) = _idx(log, "DELETE FROM bills")
    (obj,) = _idx(log, "OBJEK")
    assert lamp < induk < cm < obj, log


@pytest.mark.asyncio
async def test_hapus_draf_tagihan_fk_gagal_objek_utuh(storage_log):
    import asyncpg

    c = _ConnUrut(_BILL_DRAF, ["bill/a.pdf"], gagal_pada="DELETE FROM bills")
    c.gagal_galat = asyncpg.ForeignKeyViolationError("fk")
    storage_log.conn = c
    res = await _hapus_tagihan(c)
    assert res["success"] is False
    assert not _idx(c.log, "OBJEK"), c.log


# ---------- penjaga AST: tiga jalur, objek di LUAR transaksi ----------

JALUR = [("routers/sales_invoices.py", "delete_invoice"), ("services/bills_service.py", "delete_bill"),
         ("routers/production.py", "cancel_order")]


def _dalam_tx(fn):
    """{nama panggilan: [True bila di dalam `async with ...transaction()`]}."""
    hasil = {}

    def jalan(node, dalam):
        for anak in ast.iter_child_nodes(node):
            d = dalam
            if isinstance(anak, ast.AsyncWith) and any(
                    isinstance(i.context_expr, ast.Call) and isinstance(i.context_expr.func, ast.Attribute)
                    and i.context_expr.func.attr == "transaction" for i in anak.items):
                d = True
            if isinstance(anak, ast.Call):
                f = anak.func
                nama = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if nama in ("lepas_berkas_milik", "hapus_objek_sesudah_commit"):
                    hasil.setdefault(nama, []).append(dalam)
            jalan(anak, d)
    jalan(fn, False)
    return hasil


@pytest.mark.parametrize("berkas,nama", JALUR)
def test_jalur_hapus_urutan_kompensasi(berkas, nama):
    t = ast.parse((APP / berkas).read_text())
    (fn,) = [n for n in ast.walk(t) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == nama]
    h = _dalam_tx(fn)
    assert h.get("lepas_berkas_milik") and all(h["lepas_berkas_milik"]), (berkas, h)
    assert h.get("hapus_objek_sesudah_commit") == [False], (berkas, h)


def test_penjaga_ast_bisa_merah():
    t = ast.parse("async def f(conn):\n    async with conn.transaction():\n"
                  "        await hapus_objek_sesudah_commit([], 'x')\n    await lepas_berkas_milik(conn, 'bills', 1)\n")
    h = _dalam_tx(t.body[0])
    assert h == {"hapus_objek_sesudah_commit": [True], "lepas_berkas_milik": [False]}
