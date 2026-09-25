"""hub-authz — izin hub dokumen + 500 tanpa teks exception (25 Sep 2026).

Latar (diukur, baca saja):
  (1) GET /api/documents/{id}/download: cukup satu tenant (READ /api/documents
      default-open) -> staf mana pun mengunduh dokumen modul apa pun asal tahu
      id-nya. FE tak memanggil rute ini (0 pemakai di src; 10 panggilan nginx
      23 Sep = url lampiran pembayaran sebelum Unit 1a).
  (2) hub attach/detach/daftar untuk `employee` hanya izin modul -- tanpa
      pay-group (RULE: setiap endpoint ber-employee_id wajib memfilter).
  (3) AuthMiddleware menangkap exception hilir -> 500 `detail: str(e)` =
      teks pydantic/botocore/SQL bocor ke klien. FE hanya membaca detail 500
      dari HTTPException yang disengaja (useProfitability support_code), yang
      tak lewat jalur ini.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import Request as SRequest

from app.routers import documents as DOC

TENANT = "kaos-biru-konveksi"
USER_ID = "22222222-2222-2222-2222-222222222222"
LAIN = "99999999-9999-9999-9999-999999999999"
DOKID = uuid.UUID("55555555-5555-5555-5555-555555555555")
BEBAN = uuid.UUID("66666666-6666-6666-6666-666666666666")
KAR = uuid.UUID("77777777-7777-7777-7777-777777777777")
CHAT = uuid.UUID("88888888-8888-8888-8888-888888888888")
WAKTU = datetime(2026, 9, 25, tzinfo=timezone.utc)


class _Ctx:
    def __init__(self, v=None):
        self.v = v

    async def __aenter__(self):
        return self.v

    async def __aexit__(self, *a):
        return False


class Conn:
    def __init__(self, tautan=(), pengunggah=USER_ID, ada=True, entitas_ada=True):
        self.tautan = list(tautan)
        self.pengunggah = pengunggah
        self.ada = ada
        self.entitas_ada = entitas_ada
        self.calls = []

    async def execute(self, sql, *a):
        self.calls.append(("execute", sql, a))

    async def fetchrow(self, sql, *a):
        self.calls.append(("fetchrow", sql, a))
        if not self.ada:
            return None
        return {"file_name": "x.pdf", "file_path": "k/x.pdf", "file_type": "application/pdf",
                "storage_type": "s3", "uploaded_by": self.pengunggah}

    async def fetch(self, sql, *a):
        self.calls.append(("fetch", sql, a))
        if "FROM document_attachments" in sql and "entity_type, entity_id" in sql:
            assert a == (DOKID, TENANT)
            return [{"entity_type": et, "entity_id": eid} for et, eid in self.tautan]
        return []  # daftar #25

    async def fetchval(self, sql, *a):
        self.calls.append(("fetchval", sql, a))
        return 1 if self.entitas_ada else None

    def transaction(self):
        return _Ctx()


class Eng:
    def __init__(self, peran, izin=(), aktif=True):
        self.peran, self.izin, self.aktif = peran, set(izin), aktif

    async def get_user_context(self, uid, tid, role):
        return SimpleNamespace(business_role_id="peran-uji", business_role_code=self.peran, membership_active=self.aktif)

    async def can(self, c, aksi, modul):
        return (modul, aksi) in self.izin


@pytest.fixture
def pasang(monkeypatch):
    def _p(conn, eng, lingkup=True):
        async def _pool():
            return SimpleNamespace(acquire=lambda: _Ctx(conn))

        monkeypatch.setattr(DOC, "get_pool", _pool)
        from app.services import policy_engine_client as pec
        from app.services import pay_group_access as pga

        monkeypatch.setattr(pec, "get_policy_engine", lambda: eng)
        panggil = []

        async def _scope(c, tenant_id, user_id, employee_id):
            panggil.append((tenant_id, str(user_id), employee_id))
            return lingkup

        monkeypatch.setattr(pga, "employee_in_scope", _scope)
        st = SimpleNamespace(config=SimpleNamespace(bucket="b"), client=SimpleNamespace(
            get_object=lambda Bucket, Key: {"Body": SimpleNamespace(read=lambda n=-1: b"", close=lambda: None)}))
        monkeypatch.setattr(DOC, "get_storage_service", lambda: st)
        return panggil
    return _p


def req(uid=USER_ID):
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": uid, "role": "USER"}))


def modul(segmen):
    return DOC._izin_modul(segmen, BEBAN)[0]


# ------------------------------------------------------------ (1) unduh by-id
@pytest.mark.asyncio
async def test_unduh_staf_tanpa_izin_entitas_tertaut_403(pasang):
    pasang(Conn(tautan=[("expense", BEBAN)]), Eng("STAFF", izin=[(modul("sales-invoices"), "R")]))
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), DOKID)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_unduh_staf_dengan_izin_baca_entitas_tertaut_200(pasang):
    pasang(Conn(tautan=[("expense", BEBAN)]), Eng("STAFF", izin=[(modul("expenses"), "R")]))
    r = await DOC.download_document(req(), DOKID)
    assert isinstance(r, StreamingResponse)


@pytest.mark.asyncio
async def test_unduh_cukup_satu_tautan_sah(pasang):
    # tautan pertama jenis tak dipetakan (chat_message) tak menggagalkan tautan kedua
    pasang(Conn(tautan=[("chat_message", CHAT), ("expense", BEBAN)]),
           Eng("STAFF", izin=[(modul("expenses"), "R")]))
    r = await DOC.download_document(req(), DOKID)
    assert isinstance(r, StreamingResponse)


@pytest.mark.asyncio
async def test_unduh_tautan_basi_entitas_hilang_403(pasang):
    pasang(Conn(tautan=[("expense", BEBAN)], entitas_ada=False),
           Eng("STAFF", izin=[(modul("expenses"), "R")]))
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), DOKID)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_unduh_tanpa_tautan_hanya_pengunggah(pasang):
    pasang(Conn(tautan=[], pengunggah=USER_ID), Eng("STAFF"))
    assert isinstance(await DOC.download_document(req(), DOKID), StreamingResponse)
    pasang(Conn(tautan=[], pengunggah=LAIN), Eng("STAFF"))
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), DOKID)
    assert e.value.status_code == 403
    pasang(Conn(tautan=[], pengunggah=None), Eng("STAFF"))
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), DOKID)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_unduh_owner_tanpa_membaca_tautan(pasang):
    conn = Conn(tautan=[("expense", BEBAN)], pengunggah=LAIN)
    pasang(conn, Eng("OWNER"))
    assert isinstance(await DOC.download_document(req(), DOKID), StreamingResponse)
    assert not [c for c in conn.calls if c[0] == "fetch"]


@pytest.mark.asyncio
async def test_unduh_dokumen_tak_ada_404(pasang):
    pasang(Conn(ada=False), Eng("OWNER"))
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), DOKID)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_unduh_keanggotaan_tak_aktif_403(pasang):
    pasang(Conn(tautan=[]), Eng("OWNER", aktif=False))
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), DOKID)
    assert e.value.status_code == 403


# ------------------------------------------------------- (2) employee pay-group
@pytest.mark.asyncio
async def test_unduh_dokumen_karyawan_di_luar_pay_group_403(pasang):
    panggil = pasang(Conn(tautan=[("employee", KAR)]),
                     Eng("STAFF", izin=[(modul("employees"), "R")]), lingkup=False)
    with pytest.raises(HTTPException) as e:
        await DOC.download_document(req(), DOKID)
    assert e.value.status_code == 403
    assert panggil == [(TENANT, USER_ID, KAR)]


@pytest.mark.asyncio
async def test_unduh_dokumen_karyawan_dalam_pay_group_200(pasang):
    pasang(Conn(tautan=[("employee", KAR)]),
           Eng("STAFF", izin=[(modul("employees"), "R")]), lingkup=True)
    assert isinstance(await DOC.download_document(req(), DOKID), StreamingResponse)


@pytest.mark.asyncio
async def test_daftar_karyawan_pay_group(pasang):
    panggil = pasang(Conn(), Eng("ADMIN", izin=[(modul("employees"), "R")]), lingkup=False)
    with pytest.raises(HTTPException) as e:
        await DOC.get_entity_documents(req(), "employee", KAR)
    assert e.value.status_code == 403 and panggil == [(TENANT, USER_ID, KAR)]
    pasang(Conn(), Eng("ADMIN", izin=[(modul("employees"), "R")]), lingkup=True)
    r = await DOC.get_entity_documents(req(), "employee", KAR)
    assert r.total == 0


@pytest.mark.asyncio
async def test_attach_karyawan_di_luar_pay_group_403(pasang):
    panggil = pasang(Conn(), Eng("STAFF", izin=[(modul("employees"), "U")]), lingkup=False)
    body = DOC.AttachDocumentRequest(entity_type="employee", entity_id=KAR)
    with pytest.raises(HTTPException) as e:
        await DOC.attach_document(req(), DOKID, body)
    assert e.value.status_code == 403 and panggil


@pytest.mark.asyncio
async def test_detach_karyawan_di_luar_pay_group_403(pasang):
    pasang(Conn(), Eng("STAFF", izin=[(modul("employees"), "U")]), lingkup=False)
    body = DOC.DetachDocumentRequest(entity_type="employee", entity_id=KAR)
    with pytest.raises(HTTPException) as e:
        await DOC.detach_document(req(), DOKID, body)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_bukan_karyawan_tak_memanggil_pay_group(pasang):
    panggil = pasang(Conn(tautan=[("expense", BEBAN)]), Eng("STAFF", izin=[(modul("expenses"), "R")]))
    await DOC.download_document(req(), DOKID)
    assert panggil == []


# ------------------------------------------------ (3) 500 tanpa teks exception
@pytest.mark.asyncio
async def test_500_hilir_tanpa_teks_exception(caplog):
    import json

    from app.middleware.auth_middleware import AuthMiddleware

    mw = AuthMiddleware(app=lambda *a: None)
    rahasia = "duplicate key value violates unique constraint rahasia_tabel password=abc"

    async def call_next(request):
        raise RuntimeError(rahasia)

    scope = {"type": "http", "method": "GET", "path": "/healthz", "headers": [],
             "query_string": b"", "root_path": "", "scheme": "http",
             "server": ("t", 80), "client": ("c", 1)}
    with caplog.at_level("ERROR"):
        r = await mw.dispatch(SRequest(scope), call_next)
    assert r.status_code == 500
    badan = json.loads(r.body)
    teks = r.body.decode()
    assert "rahasia" not in teks and "password" not in teks and "RuntimeError" not in teks
    eid = badan["error_id"]
    assert len(eid) == 12 and eid in badan["detail"]
    # log server tetap lengkap + membawa id korelasi yang sama
    assert any(rahasia in m and eid in m for m in caplog.messages)
