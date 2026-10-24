"""SEKURITI 24 Okt 2026 -- isolasi tenant hanya ditegakkan kode (Law 24: RLS dekoratif untuk gateway). Empat celah lintas tenant:
A GET /products/recent-sales ?tenant_id= menimpa token · B consolidation entity_tenant_id tanpa cek keanggotaan ·
C intercompany from_entity_tenant_id != tenant pemanggil · D /auth/mfa/validate user_id dari badan.
Plus sapuan: nol handler router menerima tenant_id dari Query/Body, nol `X or request.state.user.get("tenant_id")`."""
import ast
import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

APP = Path(__file__).resolve().parents[2] / "app"
ROUTERS = APP / "routers"


def _fungsi(berkas, nama):
    t = ast.parse((ROUTERS / berkas).read_text())
    return next(n for n in ast.walk(t) if isinstance(n, ast.AsyncFunctionDef) and n.name == nama)


def _req(**user):
    return SimpleNamespace(state=SimpleNamespace(user=user or None), headers={}, client=None, query_params={})


# ------------------------------------------------------------------ sapuan seluruh router
def _tenant_dari_klien(src: str) -> list:
    t = ast.parse(src)
    temuan = []
    for f in ast.walk(t):
        if not isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        a = f.args
        bawaan = [None] * (len(a.args) - len(a.defaults)) + list(a.defaults)
        for arg, d in list(zip(a.args, bawaan)) + list(zip(a.kwonlyargs, a.kw_defaults)):
            if arg.arg == "tenant_id" and isinstance(d, ast.Call) and getattr(d.func, "id", "") in ("Query", "Body", "Form", "Header"):
                temuan.append(f"{f.name}({arg.arg}={d.func.id})")
    for n in ast.walk(t):  # X or request.state.user.get("tenant_id") -> klien bisa menimpa token
        if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or) and len(n.values) >= 2:
            ujung = ast.unparse(n.values[-1])
            if "state.user" in ujung and "tenant_id" in ujung:
                temuan.append(f"baris {n.lineno}: {ast.unparse(n)[:80]}")
    return temuan


def test_sapuan_nol_tenant_id_dari_klien_di_semua_router():
    salah = {p.name: f for p in sorted(ROUTERS.glob("*.py")) if (f := _tenant_dari_klien(p.read_text()))}
    assert not salah, salah


def test_sapuan_kontrol_merah_menangkap_pola_lama():
    lama = '''
async def get_recent_sales_products(request, limit: int = Query(5), tenant_id: str = Query(None)):
    effective_tenant = tenant_id or request.state.user.get("tenant_id")
'''
    assert len(_tenant_dari_klien(lama)) == 2


# ------------------------------------------------------------------ A
def test_a_recent_sales_tenant_hanya_dari_token(monkeypatch):
    from app.routers import products as P
    dipakai = []

    class K:
        async def fetch(self, sql, *a):
            dipakai.append(a[0])
            return []

        async def close(self):
            pass

    async def gdc():
        return K()
    monkeypatch.setattr(P, "get_db_connection", gdc)
    assert "tenant_id" not in [a.arg for a in _fungsi("products.py", "get_recent_sales_products").args.args]
    asyncio.run(P.get_recent_sales_products(_req(tenant_id="T1", user_id="u"), limit=5))
    assert dipakai == ["T1"]


# ------------------------------------------------------------------ B
def test_b_pastikan_anggota(monkeypatch):
    from app.routers import consolidation as C
    panggil = []

    async def anggota(conn, uid, tid):
        panggil.append((uid, tid))
        return tid == "T-ANGGOTA"
    monkeypatch.setattr(C, "has_active_membership", anggota)
    uid = uuid4()
    asyncio.run(C._pastikan_anggota(None, {"tenant_id": "T1", "user_id": uid}, "T1"))
    assert panggil == []  # tenant sendiri: tanpa kueri
    asyncio.run(C._pastikan_anggota(None, {"tenant_id": "T1", "user_id": uid}, "T-ANGGOTA"))
    for ctx in ({"tenant_id": "T1", "user_id": uid}, {"tenant_id": "T1", "user_id": None}):
        with pytest.raises(HTTPException) as e:
            asyncio.run(C._pastikan_anggota(None, ctx, "T-KORBAN"))
        assert e.value.status_code == 403
    assert panggil == [(str(uid), "T-ANGGOTA"), (str(uid), "T-KORBAN")]  # user None: ditolak tanpa kueri


def _baris(f, pred):
    return [n.lineno for n in ast.walk(f) if pred(n)]


def _panggil(nama):
    return lambda n: isinstance(n, ast.Call) and getattr(n.func, "id", "") == nama


def _sql(potongan):
    return lambda n: isinstance(n, ast.Constant) and isinstance(n.value, str) and potongan in n.value


@pytest.mark.parametrize("nama,sql", [
    ("add_entity", "INSERT INTO consolidation_entities"),
    ("auto_map_accounts", "FROM chart_of_accounts"),
    ("process_run", "SET status = 'processing'"),
])
def test_b_cek_anggota_sebelum_baca_tulis_entitas(nama, sql):
    f = _fungsi("consolidation.py", nama)
    cek, tulis = _baris(f, _panggil("_pastikan_anggota")), _baris(f, _sql(sql))
    assert cek and tulis and min(cek) < min(tulis), (nama, cek, tulis)


def test_b_add_entity_bukan_anggota_tak_menulis(monkeypatch):
    from app.routers import consolidation as C
    from app.schemas.consolidation import CreateConsolidationEntityRequest as R
    tulis = []

    class K:
        async def fetchval(self, sql, *a):
            if "INSERT" in sql:
                tulis.append(sql)
            return 1 if "consolidation_groups" in sql else None

    class Pool:
        def acquire(self):
            k = K()

            class A:
                async def __aenter__(s):
                    return k

                async def __aexit__(s, *e):
                    return False
            return A()

    async def gp():
        return Pool()

    async def bukan(*a):
        return False
    monkeypatch.setattr(C, "get_pool", gp)
    monkeypatch.setattr(C, "has_active_membership", bukan)
    wajib = {k: ("T-KORBAN" if k == "entity_tenant_id" else "X") for k, v in R.model_fields.items() if v.is_required()}
    with pytest.raises(HTTPException) as e:
        asyncio.run(C.add_entity(_req(tenant_id="T1", user_id=str(uuid4())), uuid4(), R(**wajib)))
    assert e.value.status_code == 403 and tulis == []


# ------------------------------------------------------------------ C
class _PoolDipanggil(Exception):
    pass


@pytest.fixture
def pool_jebakan(monkeypatch):
    from app.routers import intercompany as ic

    async def gp():
        raise _PoolDipanggil()
    monkeypatch.setattr(ic, "get_pool", gp)
    return ic


def _ic_body(frm, to="T-X"):
    from app.schemas.intercompany import CreateIntercompanyTransactionRequest as R
    return R(transaction_date=date(2026, 10, 1), from_entity_tenant_id=frm, to_entity_tenant_id=to, transaction_type="sale", amount=1000)


def test_c_from_tenant_lain_403_sebelum_db(pool_jebakan):
    with pytest.raises(HTTPException) as e:
        asyncio.run(pool_jebakan.create_transaction(_req(tenant_id="T1", user_id=None), _ic_body("T-KORBAN")))
    assert e.value.status_code == 403


def test_c_kontrol_positif_from_sendiri_lanjut_ke_db(pool_jebakan):
    with pytest.raises(HTTPException) as e:  # penjaga lolos -> get_pool dipanggil -> galat generik 500
        asyncio.run(pool_jebakan.create_transaction(_req(tenant_id="T1", user_id=None), _ic_body("T1")))
    assert e.value.status_code == 500


def test_c_tujuan_dicek_ada_sebelum_insert():
    f = _fungsi("intercompany.py", "create_transaction")
    cek = _baris(f, _sql('FROM "Tenant" WHERE id = $1 AND suspended_at IS NULL'))
    assert cek and min(cek) < min(_baris(f, _sql("INSERT INTO intercompany_transactions")))


# ------------------------------------------------------------------ D
class _Repo:
    def __init__(self):
        self.dicari = []

    async def find_unique(self, where):
        self.dicari.append(where["userId"])
        return None

    async def update(self, **k):
        raise AssertionError("tak boleh menulis")

    async def create(self, **k):
        return None


@pytest.fixture
def prisma_palsu(monkeypatch):
    from app.routers import mfa
    p = SimpleNamespace(usersecurity=_Repo(), mfaauditlog=_Repo())
    monkeypatch.setattr(mfa, "prisma", p)
    return mfa, p


def test_d_user_id_badan_lain_403_tanpa_baca_korban(prisma_palsu):
    mfa, p = prisma_palsu
    with pytest.raises(HTTPException) as e:
        asyncio.run(mfa.validate_mfa_code(mfa.MFAValidateRequest(user_id="korban", code="123456"), _req(user_id="penyerang")))
    assert e.value.status_code == 403 and p.usersecurity.dicari == []


def test_d_kontrol_positif_pakai_token(prisma_palsu):
    mfa, p = prisma_palsu
    asyncio.run(mfa.validate_mfa_code(mfa.MFAValidateRequest(code="123456"), _req(user_id="penyerang")))
    asyncio.run(mfa.validate_mfa_code(mfa.MFAValidateRequest(user_id="penyerang", code="123456"), _req(user_id="penyerang")))
    assert p.usersecurity.dicari == ["penyerang", "penyerang"]


def test_d_tanpa_sesi_401(prisma_palsu):
    mfa, _ = prisma_palsu
    with pytest.raises(HTTPException) as e:
        asyncio.run(mfa.validate_mfa_code(mfa.MFAValidateRequest(code="123456"), _req()))
    assert e.value.status_code == 401


def test_d_body_user_id_tak_dipakai_lagi():
    f = _fungsi("mfa.py", "validate_mfa_code")
    pakai = [n.lineno for n in ast.walk(f) if isinstance(n, ast.Attribute) and n.attr == "user_id"
             and isinstance(n.value, ast.Name) and n.value.id == "body"]
    assert len(pakai) == 2  # hanya penjaga `if body.user_id and body.user_id != user_id`
