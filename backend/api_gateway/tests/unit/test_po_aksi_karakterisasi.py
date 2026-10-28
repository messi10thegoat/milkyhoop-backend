"""KARAKTERISASI handler tunggal PO kirim / hapus / batal (B1b-1, 28 Okt 2026) -- DITULIS SEBELUM inti dipindah ke kirim_po_core /
hapus_po_draf_core / batal_po_core, dan WAJIB tetap hijau sesudahnya (arahan MASTER: status, idem, galat, respons).

Yang dikunci per handler: respons sukses persis; SQL tulis + parameternya; 404 / status salah / 401 / 500 (kode + teks); urutan
idem -> kunci advisory -> baca FOR UPDATE -> penentu -> tulis; replay ber-kunci = respons lama tanpa kunci/tulis; tanpa kunci = tanpa
simpan; app.user_id hapus diset di transaksi yang sama SEBELUM DELETE (trigger trg_log_deletion V230)."""
import asyncio
import os
import uuid
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers import purchase_orders as PO  # noqa: E402
from app.schemas import purchase_orders as PS  # noqa: E402

T = "kaos-biru-konveksi"
PID = uuid.UUID("11111111-2222-3333-4444-555555555555")
UID = uuid.UUID("99999999-8888-7777-6666-555555555555")
D = Decimal


def _n(sql):
    return " ".join(sql.split())


class _Tx:
    def __init__(self, kon): self.kon = kon
    async def __aenter__(self):
        self.kon.log.append(("tx", "BEGIN", ()))
        return self
    async def __aexit__(self, et, *a):
        self.kon.log.append(("tx", "ROLLBACK" if et else "COMMIT", ()))
        return False


class _H(dict):
    """header tanpa peka huruf (cukup untuk kunci_dari)."""
    def get(self, k, d=None):
        for kk, v in self.items():
            if kk.lower() == k.lower():
                return v
        return d


class _Req:
    def __init__(self, user_id=UID, kunci=None):
        self.headers = _H({"X-Idempotency-Key": kunci} if kunci else {})
        u = {"tenant_id": T, "user_id": str(user_id) if user_id else None}
        self.state = type("S", (), {"user": u})()


class Kon:
    def __init__(self, po=None, diterima="0", ditagih="0", deposit=(), meledak=None):
        self.po, self.diterima, self.ditagih, self.deposit, self.meledak = po, diterima, ditagih, list(deposit), meledak
        self.log = []

    def transaction(self, **kw): return _Tx(self)

    def _cat(self, jenis, sql, a):
        q = _n(sql)
        self.log.append((jenis, q, a))
        if self.meledak and q.startswith(self.meledak):
            raise RuntimeError("db meledak")
        return q

    async def execute(self, sql, *a):
        self._cat("exec", sql, a)
        return "OK"

    async def fetchrow(self, sql, *a):
        q = self._cat("fetchrow", sql, a)
        if "SUM(quantity_received)" in q:
            return {"diterima": D(self.diterima), "ditagih": D(self.ditagih)}
        if "FROM purchase_orders" in q:
            return self.po
        return None

    async def fetch(self, sql, *a):
        q = self._cat("fetch", sql, a)
        if "FROM vendor_deposits" in q:
            return [{"deposit_number": n} for n in self.deposit]
        return []

    async def fetchval(self, sql, *a):
        self._cat("fetchval", sql, a)
        return None

    # pembantu
    def tulis(self):
        return [(q, a) for k, q, a in self.log if k == "exec" and q.startswith(("UPDATE", "DELETE", "INSERT"))]

    def indeks(self, pred):
        return next(i for i, x in enumerate(self.log) if pred(x))


def _pasang(monkeypatch, kon):
    class CM:
        async def __aenter__(s): return kon
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()

    async def gp(): return P()
    monkeypatch.setattr(PO, "get_pool", gp)


def _po(status="draft"):
    return {"id": PID, "status": status, "po_number": "PO-2610-0042"}


def _jalan(aksi, req, reason="Salah vendor"):
    if aksi == "delete":
        return asyncio.run(PO.delete_purchase_order(req, PID))
    if aksi == "send":
        return asyncio.run(PO.send_purchase_order(req, PID))
    return asyncio.run(PO.cancel_purchase_order(req, PID, PS.CancelPurchaseOrderRequest(reason=reason)))


STATUS_OK = {"delete": "draft", "send": "draft", "cancel": "sent"}
SQL_TULIS = {
    "delete": "DELETE FROM purchase_orders WHERE id = $1 AND tenant_id = $2",
    "send": "UPDATE purchase_orders SET status = 'sent', sent_at = NOW(), sent_by = $2, updated_at = NOW() WHERE id = $1",
    "cancel": "UPDATE purchase_orders SET status = 'cancelled', cancelled_at = NOW(), cancelled_by = $2, cancelled_reason = $3, "
              "updated_at = NOW() WHERE id = $1",
}
ARG_TULIS = {"delete": (PID, T), "send": (PID, UID), "cancel": (PID, UID, "Salah vendor")}
RESP = {
    "delete": {"success": True, "message": "Pesanan pembelian dihapus.", "data": {"id": str(PID), "po_number": "PO-2610-0042"}},
    "send": {"success": True, "message": "Pesanan pembelian ditandai terkirim.",
             "data": {"id": str(PID), "po_number": "PO-2610-0042", "status": "sent"}},
    "cancel": {"success": True, "message": "Pesanan pembelian dibatalkan.", "data": {"id": str(PID), "status": "cancelled"}},
}
PREFIX = {"delete": "PO_DELETE", "send": "PO_SEND", "cancel": "PO_CANCEL"}
KOLOM_KUNCI = {"delete": "id, status, po_number", "send": "id, status, po_number", "cancel": "id, status"}
AKSI = ("delete", "send", "cancel")


# ---------------------------------------------------------------- sukses
@pytest.mark.parametrize("aksi", AKSI)
def test_sukses_respons_dan_sql_tulis_persis(monkeypatch, aksi):
    kon = Kon(_po(STATUS_OK[aksi]))
    _pasang(monkeypatch, kon)
    r = _jalan(aksi, _Req())
    assert r == RESP[aksi]
    assert kon.tulis() == [(SQL_TULIS[aksi], ARG_TULIS[aksi])]
    # satu transaksi, commit
    assert [x[1] for x in kon.log if x[0] == "tx"] == ["BEGIN", "COMMIT"]


@pytest.mark.parametrize("aksi", AKSI)
def test_urutan_tenant_kunci_for_update_lalu_tulis(monkeypatch, aksi):
    kon = Kon(_po(STATUS_OK[aksi]))
    _pasang(monkeypatch, kon)
    _jalan(aksi, _Req())
    i_ten = kon.indeks(lambda x: x[0] == "exec" and "set_config('app.tenant_id'" in x[1])
    i_lock = kon.indeks(lambda x: x[0] == "exec" and "pg_advisory_xact_lock" in x[1])
    i_baca = kon.indeks(lambda x: x[0] == "fetchrow" and "FROM purchase_orders" in x[1])
    i_tulis = kon.indeks(lambda x: x[0] == "exec" and x[1].startswith(("UPDATE purchase_orders", "DELETE FROM purchase_orders")))
    assert kon.log[i_ten][2] == (T,)
    assert kon.log[i_lock][2] == (f"PO:{T}:{PID}",)
    assert kon.log[i_baca][1] == f"SELECT {KOLOM_KUNCI[aksi]} FROM purchase_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE"
    assert kon.log[i_baca][2] == (PID, T)
    assert i_ten < i_lock < i_baca < i_tulis


def test_hapus_app_user_id_diset_di_transaksi_sebelum_delete(monkeypatch):
    kon = Kon(_po("draft"))
    _pasang(monkeypatch, kon)
    _jalan("delete", _Req())
    i_user = max(i for i, x in enumerate(kon.log) if x[0] == "exec" and "set_config('app.user_id'" in x[1])
    i_del = kon.indeks(lambda x: x[1].startswith("DELETE FROM purchase_orders"))
    i_begin = kon.indeks(lambda x: x == ("tx", "BEGIN", ()))
    assert i_begin < i_user < i_del and kon.log[i_user][2] == (str(UID),)


def test_hapus_tanpa_user_id_tetap_jalan_dengan_user_kosong(monkeypatch):
    """perilaku lama: DELETE tidak memeriksa user_id (beda dengan kirim/batal) -> app.user_id = ''."""
    kon = Kon(_po("draft"))
    _pasang(monkeypatch, kon)
    r = _jalan("delete", _Req(user_id=None))
    assert r == RESP["delete"]
    assert any(x[1].startswith("SELECT set_config('app.user_id'") and x[2] == ("",) for x in kon.log)


# ---------------------------------------------------------------- galat
@pytest.mark.parametrize("aksi", AKSI)
def test_tak_ada_404_teks_tanpa_tulis(monkeypatch, aksi):
    kon = Kon(None)
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(aksi, _Req())
    assert e.value.status_code == 404 and e.value.detail == "Pesanan pembelian tidak ditemukan."
    assert kon.tulis() == []


@pytest.mark.parametrize("aksi,status,teks", [
    ("delete", "sent", "Hanya pesanan pembelian berstatus Draf yang bisa dihapus. Yang sudah terkirim dibatalkan."),
    ("delete", "cancelled", "Hanya pesanan pembelian berstatus Draf yang bisa dihapus. Yang sudah terkirim dibatalkan."),
    ("send", "sent", "Pesanan pembelian berstatus 'sent' tidak bisa dikirim. Hanya Draf."),
    ("send", "cancelled", "Pesanan pembelian berstatus 'cancelled' tidak bisa dikirim. Hanya Draf."),
    ("cancel", "cancelled", "Pesanan pembelian berstatus 'cancelled' tidak bisa dibatalkan."),
    ("cancel", "closed", "Pesanan pembelian berstatus 'closed' tidak bisa dibatalkan."),
])
def test_status_salah_400_teks_persis_tanpa_tulis(monkeypatch, aksi, status, teks):
    kon = Kon(_po(status))
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(aksi, _Req())
    assert e.value.status_code == 400 and e.value.detail == teks
    assert kon.tulis() == []


@pytest.mark.parametrize("diterima,ditagih,teks", [
    ("2", "0", "Pesanan pembelian yang barangnya sudah diterima tidak bisa dibatalkan."),
    ("0", "1", "Pesanan pembelian yang sudah ditagih tidak bisa dibatalkan."),
    ("2", "1", "Pesanan pembelian yang barangnya sudah diterima tidak bisa dibatalkan."),  # blok PERTAMA menang
])
def test_batal_diterima_ditagih_400_blok_pertama(monkeypatch, diterima, ditagih, teks):
    kon = Kon(_po("partial_received"), diterima=diterima, ditagih=ditagih)
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan("cancel", _Req())
    assert e.value.status_code == 400 and e.value.detail == teks and kon.tulis() == []


def test_batal_alasan_spasi_422_berkode(monkeypatch):
    kon = Kon(_po("sent"))
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan("cancel", _Req(), reason="   ")
    assert e.value.status_code == 422
    assert e.value.detail == {"code": "ALASAN_WAJIB", "message": "Alasan pembatalan wajib diisi."}
    assert kon.tulis() == []


def test_batal_uang_muka_bertaut_hanya_catatan_bukan_blok(monkeypatch):
    kon = Kon(_po("sent"), deposit=("VD-2026-00001",))
    _pasang(monkeypatch, kon)
    assert _jalan("cancel", _Req()) == RESP["cancel"]


@pytest.mark.parametrize("aksi", ["send", "cancel"])
def test_kirim_batal_tanpa_user_id_401_sebelum_db(monkeypatch, aksi):
    kon = Kon(_po(STATUS_OK[aksi]))
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(aksi, _Req(user_id=None))
    assert e.value.status_code == 401 and e.value.detail == "ID pengguna wajib ada."
    assert kon.log == []


@pytest.mark.parametrize("aksi,teks", [
    ("delete", "Gagal menghapus pesanan pembelian."),
    ("send", "Gagal mengirim pesanan pembelian."),
    ("cancel", "Gagal membatalkan pesanan pembelian."),
])
def test_galat_tak_terduga_500_teks(monkeypatch, aksi, teks):
    kon = Kon(_po(STATUS_OK[aksi]), meledak="UPDATE purchase_orders" if aksi != "delete" else "DELETE FROM purchase_orders")
    _pasang(monkeypatch, kon)
    with pytest.raises(HTTPException) as e:
        _jalan(aksi, _Req())
    assert e.value.status_code == 500 and e.value.detail == teks
    assert [x[1] for x in kon.log if x[0] == "tx"] == ["BEGIN", "ROLLBACK"]


# ---------------------------------------------------------------- idempotensi
class Idem:
    def __init__(self, lama=None):
        self.lama, self.mulai, self.disimpan = lama, [], []

    def pasang(self, monkeypatch):
        async def mulai_aksi(conn, ctx, kunci, prefix, doc_id, isi, response=None):
            conn.log.append(("idem", "mulai", (prefix,)))
            self.mulai.append({"ctx": ctx, "kunci": kunci, "prefix": prefix, "doc": doc_id, "isi": isi() if callable(isi) else isi})
            if not kunci:
                return None, None, None
            return f"{prefix}:{ctx['user_id']}:{doc_id}:{kunci}", "SIDIK", self.lama

        async def simpan(conn, ctx, kp, sd, source_type, resp, result_id):
            self.disimpan.append({"kp": kp, "sd": sd, "st": source_type, "resp": resp, "rid": result_id})
            return {"disimpan": True, **resp}
        monkeypatch.setattr(PO.idem_buat, "mulai_aksi", mulai_aksi)
        monkeypatch.setattr(PO.idem_buat, "simpan", simpan)
        return self


ISI = {"delete": {}, "send": {}, "cancel": {"reason": "Salah vendor"}}


@pytest.mark.parametrize("aksi", AKSI)
def test_idem_berkunci_prefix_isi_dan_simpan_sesudah_tulis(monkeypatch, aksi):
    kon = Kon(_po(STATUS_OK[aksi]))
    _pasang(monkeypatch, kon)
    idem = Idem().pasang(monkeypatch)
    r = _jalan(aksi, _Req(kunci="K-1"))
    m = idem.mulai[0]
    assert (m["kunci"], m["prefix"], m["doc"], m["isi"]) == ("K-1", PREFIX[aksi], PID, ISI[aksi])
    assert m["ctx"]["tenant_id"] == T and m["ctx"]["user_id"] == UID
    s = idem.disimpan[0]
    assert (s["kp"], s["sd"], s["st"], s["resp"], s["rid"]) == (f"{PREFIX[aksi]}:{UID}:{PID}:K-1", "SIDIK", PREFIX[aksi], RESP[aksi], PID)
    assert r == {"disimpan": True, **RESP[aksi]}
    # idem SEBELUM kunci PO
    i_idem = kon.indeks(lambda x: x[0] == "idem")
    i_lock = kon.indeks(lambda x: x[0] == "exec" and "pg_advisory_xact_lock" in x[1])
    assert i_idem < i_lock


@pytest.mark.parametrize("aksi", AKSI)
def test_idem_replay_respons_lama_tanpa_kunci_dan_tanpa_tulis(monkeypatch, aksi):
    lama = {"success": True, "message": "lama", "data": {"id": str(PID)}}
    kon = Kon(_po("cancelled"))  # status sekarang TAK boleh menolak replay
    _pasang(monkeypatch, kon)
    idem = Idem(lama=lama).pasang(monkeypatch)
    assert _jalan(aksi, _Req(kunci="K-1")) == lama
    assert kon.tulis() == [] and idem.disimpan == []
    assert not any("pg_advisory_xact_lock" in x[1] for x in kon.log if x[0] == "exec")


@pytest.mark.parametrize("aksi", AKSI)
def test_tanpa_kunci_tanpa_simpan_respons_dict_biasa(monkeypatch, aksi):
    kon = Kon(_po(STATUS_OK[aksi]))
    _pasang(monkeypatch, kon)
    idem = Idem().pasang(monkeypatch)
    assert _jalan(aksi, _Req()) == RESP[aksi]
    assert idem.mulai[0]["kunci"] is None and idem.disimpan == []


@pytest.mark.parametrize("aksi", AKSI)
def test_idem_asli_kunci_sama_isi_beda_409(monkeypatch, aksi):
    """idem_buat.mulai_aksi ASLI: LookupError dari pembaca replay -> 409 IDEMPOTENCY_KEY_REUSED, tanpa tulis."""
    kon = Kon(_po(STATUS_OK[aksi]))
    _pasang(monkeypatch, kon)

    async def ambil(*a, **k):
        raise LookupError
    monkeypatch.setattr(PO.idem_buat, "ambil_replay_klien", ambil)
    with pytest.raises(HTTPException) as e:
        _jalan(aksi, _Req(kunci="K-1"))
    assert e.value.status_code == 409 and e.value.detail["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert kon.tulis() == []
