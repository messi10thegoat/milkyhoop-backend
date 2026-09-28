"""Dua sesi per akun (MASTER 28 Sep 2026): SATU sesi aktif per kelas {web_desktop, web_mobile}.

Diukur sebelum perbaikan: login HP (UA iPhone/Android) SUDAH bertipe 'mobile' sejak 21 Sep, tetapi
  (a) DeviceService.register_device cabang mobile menonaktifkan SEMUA baris web + mencabut refresh + WS force_logout
      (kaos 25 Sep 14:35: login iPhone mematikan 1 sesi web);
  (b) logout HP = revoke_all -> kunci Redis web ikut terhapus -> desktop SESSION_REPLACED;
  (c) HP "situs desktop"/iPadOS mengirim UA Mac -> 'web' -> menendang desktop. Kini klien boleh menyebut kelasnya.
"""
import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import auth as A  # noqa: E402
from app.services import device_service as DS  # noqa: E402
from app.services import session_manager as SM  # noqa: E402

UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36"
UA_HP = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
UID, TEN = "u-dua-sesi", "kaos"


# ---------------- penentu kelas ----------------

@pytest.mark.parametrize("kind,ua,harap", [
    ("web_mobile", UA_MAC, "mobile"),      # HP "situs desktop"/iPadOS: klien menang atas UA
    ("web_desktop", UA_HP, "web"),
    (" WEB_MOBILE ", None, "mobile"),
    (None, UA_HP, "mobile"),               # klien lama tanpa client_kind: UA (perilaku 21 Sep) tetap
    (None, UA_MAC, "web"),
    ("app", UA_MAC, "web"),                # tak dikenal -> UA
    ("", None, "web"),
    (None, None, "web"),
])
def test_resolve_device_type(kind, ua, harap):
    assert SM.resolve_device_type(kind, ua) == harap


def test_peta_kelas_dua_arah_dan_hanya_nilai_check_db():
    assert SM.CLIENT_KIND_KE_TIPE == {"web_desktop": "web", "web_mobile": "mobile"}
    assert set(SM.CLIENT_KIND_KE_TIPE.values()) == {"mobile", "web"}   # user_devices chk_device_type
    assert SM.TIPE_KE_CLIENT_KIND == {"web": "web_desktop", "mobile": "web_mobile"}


# ---------------- Redis: satu sesi per kelas ----------------

class _Redis:
    def __init__(self):
        self.d = {}

    def set(self, k, v, ex=None):
        self.d[k] = v

    def get(self, k):
        return self.d.get(k)

    def delete(self, k):
        return 1 if self.d.pop(k, None) is not None else 0

    def pipeline(self, transaction=True):
        r = self

        class P:
            def __init__(self):
                self.ops = []

            def set(self, *a, **kw):
                self.ops.append(("set", a, kw))

            def delete(self, *a):
                self.ops.append(("delete", a, {}))

            def execute(self):
                for op, a, kw in self.ops:
                    getattr(r, op)(*a, **kw)
        return P()


@pytest.fixture
def sm():
    m = object.__new__(SM.SessionManager)
    m.redis = _Redis()
    return m


def test_desktop_dan_hp_hidup_bersama_login_kedua_sekelas_hanya_menendang_kelasnya(sm):
    sm.set_active_device(UID, "web", "desk-1")
    sm.set_active_device(UID, "mobile", "hp-1")
    assert sm.is_session_valid(UID, "web", "desk-1") and sm.is_session_valid(UID, "mobile", "hp-1")
    sm.set_active_device(UID, "web", "desk-2")                     # desktop kedua
    assert not sm.is_session_valid(UID, "web", "desk-1")
    assert sm.is_session_valid(UID, "web", "desk-2") and sm.is_session_valid(UID, "mobile", "hp-1")
    sm.set_active_device(UID, "mobile", "hp-2")                    # HP kedua
    assert not sm.is_session_valid(UID, "mobile", "hp-1")
    assert sm.is_session_valid(UID, "mobile", "hp-2") and sm.is_session_valid(UID, "web", "desk-2")


# ---------------- DeviceService: baris user_devices + refresh + WS ----------------

class _Tabel:
    def __init__(self, baris):
        self.baris = baris

    async def find_many(self, where):
        return [b for b in self.baris if all(getattr(b, k) == v for k, v in where.items())]

    async def update(self, where, data):
        for b in self.baris:
            if b.id == where["id"]:
                for k, v in data.items():
                    setattr(b, k, v)

    async def create(self, data):
        b = SimpleNamespace(**{"id": data.get("id", "baru"), **data})
        self.baris.append(b)
        return b


class _Token:
    def __init__(self):
        self.dicabut = []

    async def update_many(self, where, data):
        self.dicabut.append(where["tokenHash"])


def _dev(i, tipe):
    return SimpleNamespace(id=i, userId=UID, tenantId=TEN, deviceType=tipe, isActive=True, refreshTokenHash=f"h-{i}")


@pytest.fixture
def ds(monkeypatch, sm):
    ws = []

    async def force(dev_id, pesan):
        ws.append(dev_id)
        return 1

    async def tidur(_):
        pass
    monkeypatch.setattr(DS, "websocket_hub", SimpleNamespace(force_logout_device=force))
    monkeypatch.setattr(DS, "session_manager", sm)
    monkeypatch.setattr(DS.asyncio, "sleep", tidur)
    baris = [_dev("desk-1", "web"), _dev("hp-1", "mobile")]
    prisma = SimpleNamespace(userdevice=_Tabel(baris), refreshtoken=_Token())
    return SimpleNamespace(svc=DS.DeviceService(prisma), baris=baris, ws=ws, tok=prisma.refreshtoken)


def _aktif(ds):
    return {b.id for b in ds.baris if b.isActive}


async def _daftar(ds, tipe, dev_id):
    return await ds.svc.register_device(user_id=UID, tenant_id=TEN, device_type=tipe, browser_id="b",
                                        user_agent=UA_HP if tipe == "mobile" else UA_MAC,
                                        refresh_token_hash=f"h-{dev_id}", device_id=dev_id)


@pytest.mark.asyncio
async def test_login_hp_kedua_tak_menyentuh_desktop(ds):
    await _daftar(ds, "mobile", "hp-2")
    assert _aktif(ds) == {"desk-1", "hp-2"}
    assert ds.ws == ["hp-1"] and ds.tok.dicabut == ["h-hp-1"]      # desktop: tanpa WS, refresh tak dicabut


@pytest.mark.asyncio
async def test_login_desktop_kedua_hanya_menendang_desktop_pertama(ds):
    await _daftar(ds, "web", "desk-2")
    assert _aktif(ds) == {"hp-1", "desk-2"}
    assert ds.ws == ["desk-1"] and ds.tok.dicabut == ["h-desk-1"]


# ---------------- login: client_kind sampai ke klaim & sesi ----------------

@pytest.mark.asyncio
@pytest.mark.parametrize("kind,ua,harap", [("web_mobile", UA_MAC, "mobile"), ("web_desktop", UA_HP, "web"),
                                           (None, UA_HP, "mobile"), (None, UA_MAC, "web")])
async def test_login_meneruskan_kelas_ke_klaim_jwt(monkeypatch, kind, ua, harap):
    dapat = {}

    async def login_user(email, password, device_id=None, device_type=None):
        dapat["tipe"] = device_type
        return {"success": False, "error": "berhenti di sini"}
    monkeypatch.setattr(A.auth_client, "login_user", login_user)

    async def audit(**kw):
        pass
    monkeypatch.setattr(A, "log_auth_event", audit)
    req = SimpleNamespace(headers={"User-Agent": ua} if ua else {}, client=SimpleNamespace(host="1.2.3.4"))
    body = A.LoginRequest(email="x@y.id", password="p", **({"client_kind": kind} if kind else {}))
    try:
        await A.login_user(body, req)
    except Exception:
        pass
    assert dapat["tipe"] == harap


def test_login_request_menerima_client_kind_opsional():
    assert A.LoginRequest(email="a", password="b").client_kind is None
    assert A.LoginRequest(email="a", password="b", client_kind="web_mobile").client_kind == "web_mobile"


# ---------------- logout: hanya kelas sendiri ----------------

import hashlib  # noqa: E402

# refresh token -> baris user_devices (id, device_type) di DB palsu; 'rt-usang' = desktop yang sudah digantikan
PERANGKAT = {"rt-desk": ("desk-1", "web"), "rt-hp": ("hp-1", "mobile"), "rt-usang": ("desk-0", "web")}


@pytest.fixture
def keluar(monkeypatch, sm):
    async def ident(req, rt):
        return UID

    async def logout(**kw):
        return {"success": True, "revoked_tokens": 1}

    async def audit(**kw):
        pass

    class _Pool:
        async def fetchrow(self, sql, h, uid):
            assert "user_devices" in sql and uid == UID
            for rt, (i, t) in PERANGKAT.items():
                if hashlib.sha256(rt.encode()).hexdigest() == h:
                    return {"id": i, "device_type": t}
            return None

    async def pool():
        return _Pool()

    async def validate(tok):
        i, t = tok.split(":")
        return {"valid": True, "user_id": UID, "device_id": i, "device_type": t}
    monkeypatch.setattr(A, "_identitas_logout", ident)
    monkeypatch.setattr(A, "get_pool", pool)
    monkeypatch.setattr(A.auth_client, "logout", logout)
    monkeypatch.setattr(A.auth_client, "validate_token", validate)
    monkeypatch.setattr(A, "log_auth_event", audit)
    monkeypatch.setattr(A, "session_manager", sm)
    sm.set_active_device(UID, "web", "desk-1")
    sm.set_active_device(UID, "mobile", "hp-1")

    async def jalan(rt, semua=False, bearer=None):
        # FE memanggil /logout TANPA Authorization -> request.state.user TAK ada (jalur publik)
        req = SimpleNamespace(state=SimpleNamespace(), headers={"Authorization": f"Bearer {bearer}"} if bearer else {},
                              client=SimpleNamespace(host="1.2.3.4"))
        await A.logout_user(A.LogoutRequest(refresh_token=rt, logout_all_devices=semua), req)
    return jalan


def _hidup(sm):
    return (sm.is_session_valid(UID, "web", "desk-1"), sm.is_session_valid(UID, "mobile", "hp-1"))


@pytest.mark.asyncio
async def test_logout_hp_tanpa_authorization_tak_membunuh_desktop(keluar, sm):
    await keluar("rt-hp")
    assert _hidup(sm) == (True, False)


@pytest.mark.asyncio
async def test_logout_desktop_tak_membunuh_hp(keluar, sm):
    await keluar("rt-desk")
    assert _hidup(sm) == (False, True)


@pytest.mark.asyncio
async def test_logout_lewat_bearer_memakai_klaim_perangkat(keluar, sm):
    await keluar(None, bearer="hp-1:mobile")
    assert _hidup(sm) == (True, False)


@pytest.mark.asyncio
async def test_logout_sesi_usang_tak_mencabut_sesi_sekelas_yang_lebih_baru(keluar, sm):
    await keluar("rt-usang")
    assert _hidup(sm) == (True, True)


@pytest.mark.asyncio
async def test_logout_tanpa_bukti_perangkat_tak_mencabut_apa_pun(keluar, sm):
    await keluar("rt-tak-dikenal")
    assert _hidup(sm) == (True, True)


@pytest.mark.asyncio
async def test_logout_semua_perangkat_tetap_membunuh_keduanya(keluar, sm):
    await keluar("rt-hp", semua=True)
    assert _hidup(sm) == (False, False)
