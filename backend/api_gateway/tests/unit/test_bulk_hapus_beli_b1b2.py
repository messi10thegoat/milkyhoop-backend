"""B1b-2 (29 Okt 2026): hapus DRAF massal Pembelian -- Faktur Pembelian, Pembayaran Keluar, Uang Muka Pembelian, Nota Debit.

Spek: (1) rute = /bulk/delete + /bulk/delete/preview per modul, POST, daftar putih; (2) izin = izin DELETE tunggal (D); (3) jalur TUNGGAL:
DELETE /{id} dan hapus massal memanggil inti yang SAMA, handler tak lagi menulis sendiri; pemindai: modul uang hanya dipakai lewat inti hapus
+ penentu baca (tanpa posting/void/refund/terapkan); (4) tulis per item: kode stabil (= kode blok pratinjau), pesan = rute tunggal, idem
BULK_DELETE per item; (5) pratinjau BACA SAJA (readonly, tanpa kunci/FOR UPDATE, nol tulis); (6) mesin bersama: _sesudah_commit (objek
lampiran tagihan) jalan HANYA sesudah commit item, tak tersimpan di hasil/idem, galatnya tak mengubah hasil; (7) kunci wajib, batas 50."""
import ast
import asyncio
import inspect
import os
import uuid
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.middleware import permission_middleware as PM  # noqa: E402
from app.routers import bill_payments as RBP  # noqa: E402
from app.routers import bills as RB  # noqa: E402
from app.routers import bulk_hapus_beli as BH  # noqa: E402
from app.routers import vendor_credits as RVC  # noqa: E402
from app.routers import vendor_deposits as RVD  # noqa: E402
from app.services import bills_service as BS  # noqa: E402
from app.services import bulk as B  # noqa: E402
from app.services import idem_buat as IB  # noqa: E402
from app.services import po_daftar as PDF  # noqa: E402

APP = Path(__file__).resolve().parents[2] / "app"
T = "kaos-biru-konveksi"
UID = uuid.UUID("99999999-8888-7777-6666-555555555555")
D = Decimal
MODUL = {"bills": "purchase_invoice", "bill-payments": "send_payment", "vendor-deposits": "vendor_deposit", "vendor-credits": "debit_note"}


def _n(sql):
    return " ".join(sql.split())


def _jalan(c):
    return asyncio.run(c)


# ---------------- (1) rute + (2) izin ----------------
def test_rute_hanya_hapus_dan_pratinjau_per_modul():
    assert set(BH.ROUTERS) == set(MODUL) and BH.AKSI == ("delete",) and set(BH.AKSI) <= B.AKSI_DIIZINKAN
    for m, r in BH.ROUTERS.items():
        assert sorted((x.path, tuple(sorted(x.methods))) for x in r.routes) == [("/bulk/delete", ("POST",)), ("/bulk/delete/preview", ("POST",))], m


@pytest.mark.parametrize("prefiks", sorted(MODUL))
def test_izin_massal_sama_dengan_delete_tunggal(prefiks):
    assert PM.izin_untuk_jalur(f"/api/{prefiks}/X", "DELETE") == (MODUL[prefiks], "D")
    assert PM.izin_untuk_jalur(f"/api/{prefiks}/bulk/delete", "POST") == (MODUL[prefiks], "D")
    assert PM.izin_untuk_jalur(f"/api/{prefiks}/bulk/delete/preview", "POST") == (MODUL[prefiks], "D")


# ---------------- (3) jalur tunggal + pemindai ----------------
TUNGGAL = [(BS.BillsService.delete_bill, "hapus_draf_tagihan_core(", "DELETE FROM bills"),
           (RBP.delete_bill_payment, "hapus_draf_bayar_core(", "DELETE FROM bill_payment"),
           (RVD.delete_vendor_deposit, "hapus_draf_umv_core(", "DELETE FROM vendor_deposits"),
           (RVC.delete_vendor_credit, "hapus_draf_nd_core(", "DELETE FROM vendor_credits")]


def _kode(fn) -> str:
    """KODE fungsi tanpa docstring/komentar (ast.unparse): docstring yang MENYEBUT "FOR UPDATE" bukan kode yang mengunci."""
    import textwrap
    f = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    if f.body and isinstance(f.body[0], ast.Expr) and isinstance(f.body[0].value, ast.Constant) and isinstance(f.body[0].value.value, str):
        f.body = f.body[1:]
    return ast.unparse(f)


@pytest.mark.parametrize("fn,inti,sql", TUNGGAL, ids=[t[1] for t in TUNGGAL])
def test_rute_tunggal_memakai_inti_dan_tak_menulis_sendiri(fn, inti, sql):
    src = _kode(fn)
    assert inti in src and sql not in src and "FOR UPDATE" not in src and "pg_advisory_xact_lock" not in src


def test_kode_tanpa_docstring_kontrol():
    """Kontrol: _kode membuang docstring/komentar tapi MEMPERTAHANKAN string SQL di kode."""
    ns = {}
    sumber = 'async def f(conn):\n    """FOR UPDATE di docstring"""\n    # FOR UPDATE di komentar\n    await conn.execute("SELECT 1 FOR UPDATE")\n'
    exec(compile(sumber, "<kontrol-kode>", "exec"), ns)
    import linecache
    linecache.cache["<kontrol-kode>"] = (len(sumber), None, sumber.splitlines(True), "<kontrol-kode>")
    assert _kode(ns["f"]).count("FOR UPDATE") == 1


def test_pemindai_modul_uang_hanya_inti_hapus_dan_penentu_baca():
    """bulk_hapus_beli mengimpor router/servis yang JUGA memuat posting/void/refund: hanya atribut di daftar ini yang boleh dipakai."""
    boleh = {"RBP": {"hapus_draf_bayar_core"}, "RVD": {"hapus_draf_umv_core"}, "RVC": {"hapus_draf_nd_core", "rencana_hapus_nd"},
             "BS": {"hapus_draf_tagihan_core", "PESAN_TAGIHAN_TAK_ADA"}, "BKR": {"rencana_hapus_draf_bayar"}, "UMVR": {"rencana_hapus"},
             "TA": {"blok_hapus", "ref_hapus"}}
    pohon = ast.parse((APP / "routers/bulk_hapus_beli.py").read_text(encoding="utf-8"))
    dipakai = {}
    for n in ast.walk(pohon):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in boleh:
            dipakai.setdefault(n.value.id, set()).add(n.attr)
    for alias, attr in dipakai.items():
        assert attr <= boleh[alias], (alias, attr - boleh[alias])
    assert dipakai["RBP"] == {"hapus_draf_bayar_core"}  # kontrol: pemindai memang melihat pemakaian
    for kata in ("posting", "void", "refund", "apply", "terapkan", "journal"):
        assert kata not in " ".join(sorted(a for s in dipakai.values() for a in s)).lower(), kata


# ---------------- tiruan DB ----------------
class _Tx:
    def __init__(self, kon, ro=False): self.kon, self.ro = kon, ro
    async def start(self): self.kon.log.append(("tx", "BEGIN RO" if self.ro else "BEGIN", ()))
    async def rollback(self): self.kon.log.append(("tx", "ROLLBACK", ()))
    async def __aenter__(self):
        await self.start()
        return self
    async def __aexit__(self, et, *a):
        self.kon.log.append(("tx", "ROLLBACK" if et else "COMMIT", ()))
        return False


TABEL = {"bills": "bills", "bill-payments": "bill_payments_v2", "vendor-deposits": "vendor_deposits", "vendor-credits": "vendor_credits"}
NOMOR = {"bills": "invoice_number", "bill-payments": "payment_number", "vendor-deposits": "deposit_number", "vendor-credits": "credit_number"}


class Kon:
    """dok: {tabel: {id: baris}}"""
    def __init__(self, dok):
        self.dok = dok
        self.log = []

    def transaction(self, readonly=False, **kw): return _Tx(self, readonly)

    def _c(self, j, sql, a):
        q = _n(sql)
        self.log.append((j, q, a))
        return q

    def _baris(self, q, a):
        for tabel, isi in self.dok.items():
            if f"FROM {tabel} WHERE" in q:
                r = isi.get(uuid.UUID(str(a[0])))
                return dict(r) if r and a[1] == T else None
        return None

    async def execute(self, sql, *a):
        q = self._c("exec", sql, a)
        for tabel, isi in self.dok.items():
            if q.startswith(f"DELETE FROM {tabel} WHERE"):
                isi.pop(uuid.UUID(str(a[0])), None)
        return "OK"

    async def fetchrow(self, sql, *a):
        return self._baris(self._c("fetchrow", sql, a), a)

    async def fetch(self, sql, *a):
        q = self._c("fetch", sql, a)
        if q.startswith("DELETE FROM bill_attachments"):
            return [{"file_path": f"lampiran/{a[0]}.pdf"}]
        if "AS n FROM" in q:
            tabel = q.split("AS n FROM ")[1].split()[0]
            kolom = q.split("SELECT id, ")[1].split(" AS n")[0]
            return [{"id": i, "n": r[kolom]} for i, r in self.dok.get(tabel, {}).items() if i in a[1]]
        return []

    async def fetchval(self, sql, *a):
        q = self._c("fetchval", sql, a)
        if q.startswith("DELETE FROM vendor_credits"):
            i = uuid.UUID(str(a[0]))
            r = self.dok["vendor_credits"].get(i)
            if r and r["status"] == "draft":
                self.dok["vendor_credits"].pop(i)
                return i
        return None

    def tulis(self):
        return [(q, a) for j, q, a in self.log if j in ("exec", "fetchval", "fetch") and q.startswith(("DELETE", "UPDATE"))]


class Pool:
    def __init__(self, kon): self.kon = kon

    def acquire(self):
        kon = self.kon

        class CM:
            async def __aenter__(s): return kon
            async def __aexit__(s, *a): return False
        return CM()


class Idem:
    def __init__(self): self.mulai, self.disimpan, self.lama = [], [], {}

    def pasang(self, mp):
        async def mulai_aksi(conn, ctx, kunci, prefix, doc_id, isi, response=None):
            if not kunci:
                return None, None, None
            kp = f"{prefix}:{ctx['user_id']}:{doc_id}:{kunci}"
            self.mulai.append(kp)
            return kp, "S", self.lama.get(kp)

        async def simpan(conn, ctx, kp, sd, st, resp, rid):
            from fastapi.encoders import jsonable_encoder
            resp = jsonable_encoder(resp)
            if kp:
                self.disimpan.append((kp, resp))
                self.lama[kp] = resp
            return resp
        mp.setattr(IB, "mulai_aksi", mulai_aksi)
        mp.setattr(IB, "simpan", simpan)
        return self


class _Req:
    def __init__(self, kunci="K-BATCH"):
        self.headers = {"X-Idempotency-Key": kunci} if kunci else {}
        self.state = type("S", (), {"user": {"tenant_id": T, "user_id": str(UID)}})()


def _ids(n): return [uuid.UUID(int=i + 1) for i in range(n)]


def _baris(modul, i, status, **x):
    if modul == "bills":
        st = {"draft": ("unpaid", "draft"), "posted": ("unpaid", "posted"), "void": ("void", "void")}[status]
        return {"id": i, "invoice_number": f"PB-{i.int:04d}", "amount_paid": D("0"), "status": st[0], "status_v2": st[1],
                "accounting_status": st[1], "operational_status": "unpaid", **x}
    if modul == "bill-payments":
        return {"id": i, "payment_number": f"BP-{i.int:04d}", "status": {"void": "voided"}.get(status, status), "total_amount": D("1"), **x}
    if modul == "vendor-deposits":
        return {"id": i, "deposit_number": f"VD-{i.int:04d}", "status": status, "tenant_id": T, **x}
    return {"id": i, "credit_number": f"VC-{i.int:04d}", "status": status, **x}


def _siap(mp, modul, status):
    ids = _ids(len(status))
    kon = Kon({TABEL[modul]: {i: _baris(modul, i, s) for i, s in zip(ids, status)}})
    async def gp(): return Pool(kon)
    for mod in (BH, RB, RBP, RVD, RVC):
        mp.setattr(mod, "get_pool", gp)

    async def svc():
        return BS.BillsService(Pool(kon), accounting_facade=None)
    mp.setattr(RB, "get_bills_service", svc)
    objek = []

    async def hapus_objek(paths, konteks):
        kon.log.append(("objek", "hapus", tuple(paths)))
        objek.append(list(paths))
    mp.setattr(BS, "hapus_objek_sesudah_commit", hapus_objek)
    mp.setattr(BH, "hapus_objek_sesudah_commit", hapus_objek)

    async def terkait(conn, tid, bid): return []
    mp.setattr(PDF, "po_terkait_bill", terkait)
    mp.setattr(BH, "po_terkait_bill", terkait)
    return kon, ids, objek


def _ep(modul, path):
    return next(r.endpoint for r in BH.ROUTERS[modul].routes if r.path == path)


KODE_BUKAN_DRAF = {"bills": "BUKAN_DRAF", "bill-payments": "STATUS_BUKAN_DRAF", "vendor-deposits": "BUKAN_DRAF", "vendor-credits": "ND_BUKAN_DRAF"}
KODE_TAK_ADA = {"bills": "TAGIHAN_TAK_ADA", "bill-payments": "TIDAK_DITEMUKAN", "vendor-deposits": "UANG_MUKA_TAK_ADA", "vendor-credits": "ND_TAK_ADA"}


# ---------------- (4) tulis per item ----------------
@pytest.mark.parametrize("modul", sorted(MODUL))
def test_hapus_massal_per_item_kode_stabil_idem_dan_hanya_draf_terhapus(monkeypatch, modul):
    kon, ids, _ = _siap(monkeypatch, modul, ["draft", "posted", "draft"])
    idem = Idem().pasang(monkeypatch)
    hantu = uuid.UUID(int=99)
    semua = ids + [hantu]
    r = _jalan(_ep(modul, "/bulk/delete")(_Req(), BH.BulkHapusRequest(ids=[str(i) for i in semua])))["data"]
    st = [(x["status"], x["code"]) for x in r["items"]]
    assert st == [("ok", None), ("rejected", KODE_BUKAN_DRAF[modul]), ("ok", None), ("rejected", KODE_TAK_ADA[modul])]
    assert [x["http"] for x in r["items"]] == [200, 400, 200, 404]
    assert set(kon.dok[TABEL[modul]]) == {ids[1]}  # hanya draf yang hilang
    assert idem.mulai == [f"BULK_DELETE:{UID}:{i}:K-BATCH" for i in semua]
    assert r["items"][0]["result"]["id"] == str(ids[0])
    assert [x["number"] for x in r["items"]] == [_baris(modul, i, "draft")[NOMOR[modul]] for i in ids] + [None]
    assert any(q.startswith("INSERT INTO audit_logs") and a[0] == "BULK_DELETE" for j, q, a in kon.log if j == "exec")
    # ulang kunci sama: replay item ok, nol hapus baru
    n = len(kon.tulis())
    r2 = _jalan(_ep(modul, "/bulk/delete")(_Req(), BH.BulkHapusRequest(ids=[str(i) for i in semua])))["data"]
    assert r2["items"][0]["replay"] is True and r2["items"][0]["result"] == r["items"][0]["result"]
    assert len([t for t in kon.tulis()[n:] if t[0].startswith(("DELETE FROM bills", "DELETE FROM bill_payments_v2", "DELETE FROM vendor"))]) == 0


@pytest.mark.parametrize("modul", sorted(MODUL))
def test_pesan_tolak_massal_sama_dengan_delete_tunggal(monkeypatch, modul):
    for status in ("posted", "void"):
        kon, (i,), _ = _siap(monkeypatch, modul, [status])
        Idem().pasang(monkeypatch)
        with pytest.raises(HTTPException) as e:
            if modul == "bills":
                _jalan(RB.delete_bill(_Req(None), i))
            elif modul == "bill-payments":
                _jalan(RBP.delete_bill_payment(_Req(None), str(i)))
            elif modul == "vendor-deposits":
                _jalan(RVD.delete_vendor_deposit(_Req(None), i))
            else:
                _jalan(RVC.delete_vendor_credit(_Req(None), i))
        it = _jalan(_ep(modul, "/bulk/delete")(_Req(), BH.BulkHapusRequest(ids=[str(i)])))["data"]["items"][0]
        assert (it["http"], it["message"]) == (e.value.status_code, e.value.detail), (modul, status)


def test_tagihan_massal_objek_lampiran_sesudah_commit_dan_tak_masuk_hasil(monkeypatch):
    kon, ids, objek = _siap(monkeypatch, "bills", ["draft", "posted"])
    Idem().pasang(monkeypatch)
    r = _jalan(_ep("bills", "/bulk/delete")(_Req(), BH.BulkHapusRequest(ids=[str(i) for i in ids])))["data"]
    assert objek == [[f"lampiran/{ids[0]}.pdf"]]  # hanya item yang commit
    assert "_sesudah_commit" not in r["items"][0]["result"]
    assert r["items"][0]["result"] == {"id": str(ids[0]), "invoice_number": f"PB-{ids[0].int:04d}"}
    i_commit = max(k for k, x in enumerate(kon.log) if x == ("tx", "COMMIT", ()) and k < kon.log.index(("objek", "hapus", (f"lampiran/{ids[0]}.pdf",))))
    i_del = next(k for k, x in enumerate(kon.log) if x[1].startswith("DELETE FROM bills"))
    assert i_del < i_commit
    i_user = max(k for k, x in enumerate(kon.log) if "set_config('app.user_id'" in x[1] and k < i_del)
    assert kon.log[i_user][2] == (str(UID),)


# ---------------- (5) pratinjau ----------------
@pytest.mark.parametrize("modul", sorted(MODUL))
def test_pratinjau_baca_saja_tanpa_kunci_nol_tulis(monkeypatch, modul):
    kon, ids, objek = _siap(monkeypatch, modul, ["draft", "posted"])
    r = _jalan(_ep(modul, "/bulk/delete/preview")(_Req(None), BH.BulkHapusRequest(ids=[str(i) for i in ids] + [str(uuid.UUID(int=99))])))["data"]
    assert [(x["ok"], [b["code"] for b in x["blocks"]][:1]) for x in r["items"]] == [
        (True, []), (False, [KODE_BUKAN_DRAF[modul]]), (False, [KODE_TAK_ADA[modul]])]
    assert ("tx", "BEGIN RO", ()) in kon.log and kon.log[-1] == ("tx", "ROLLBACK", ())
    assert kon.tulis() == [] and objek == []
    assert not any("pg_advisory_xact_lock" in x[1] or "FOR UPDATE" in x[1] for x in kon.log)
    assert r["items"][0]["preview"]["id"] == str(ids[0])


# ---------------- (6) mesin bersama: _sesudah_commit ----------------
class _KonMesin:
    def __init__(self): self.log = []
    def transaction(self, **kw): return _Tx(self)
    async def execute(self, sql, *a): self.log.append(("exec", _n(sql), a))


def test_mesin_sesudah_commit_hanya_jalan_bila_commit_dan_galatnya_tak_mengubah_hasil(monkeypatch):
    kon = _KonMesin()
    Idem().pasang(monkeypatch)
    jalan = []

    async def fn(conn, ctx, i):
        if i.int == 2:
            raise HTTPException(status_code=400, detail={"code": "X", "message": "tolak"})

        async def s():
            jalan.append((i.int, kon.log[-1][1]))
            if i.int == 3:
                raise RuntimeError("objek gagal")
        return {"ok": i.int, "_sesudah_commit": s}
    r = _jalan(B.jalankan_per_item(Pool(kon), {"tenant_id": T, "user_id": UID}, "delete", "bills", _ids(3), fn, kunci_batch="K"))
    assert [x["status"] for x in r["items"]] == ["ok", "rejected", "ok"]
    assert jalan == [(1, "COMMIT"), (3, "COMMIT")]  # sesudah commit item; item ditolak tak menjalankannya
    assert all("_sesudah_commit" not in (x["result"] or {}) for x in r["items"])


# ---------------- (7) pagar permintaan ----------------
@pytest.mark.parametrize("modul", sorted(MODUL))
def test_tanpa_kunci_400_dan_batas_50(monkeypatch, modul):
    _siap(monkeypatch, modul, ["draft"])
    with pytest.raises(HTTPException) as e:
        _jalan(_ep(modul, "/bulk/delete")(_Req(None), BH.BulkHapusRequest(ids=[str(uuid.UUID(int=1))])))
    assert e.value.detail["code"] == "BULK_KUNCI_WAJIB"
    with pytest.raises(HTTPException) as e:
        _jalan(_ep(modul, "/bulk/delete")(_Req(), BH.BulkHapusRequest(ids=[str(i) for i in _ids(51)])))
    assert e.value.detail["code"] == "BULK_TERLALU_BANYAK"
