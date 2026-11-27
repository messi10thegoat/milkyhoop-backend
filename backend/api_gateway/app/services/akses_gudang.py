"""PARITAS-4b (28 Nov 2026; DESAIN-akses-gudang-20261128; V516): akses gudang per pengguna, ikut Accurate (Pengguna -> Akses Gudang).
SATU penentu untuk pratinjau DAN tulis. Flag gelap `akses_gudang_v1` mati = semua gudang (perilaku lama).
Tanpa baris akses = SEMUA gudang. OWNER/ADMIN selalu semua."""
from typing import Iterable, Optional

from .galat_berkode import GalatBerkode

FLAG = "akses_gudang_v1"
PERAN_SEMUA = ("OWNER", "ADMIN")
KODE_TANPA_AKSES = "GUDANG_TANPA_AKSES"


async def flag_nyala(conn, tid: str) -> bool:
    return (await conn.fetchval("SELECT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = $1 AND feature = $2 AND enabled)", tid, FLAG)) is True


async def daftar(conn, tid: str, user_id) -> list:
    """Gudang yang dicentang untuk pengguna (kosong = semua). Tanpa flag/peran: data mentah untuk layar Pengguna."""
    rows = await conn.fetch(
        """SELECT a.warehouse_id, w.name FROM user_warehouse_access a JOIN warehouses w ON w.id = a.warehouse_id AND w.tenant_id = a.tenant_id
           WHERE a.tenant_id = $1 AND a.user_id = $2 ORDER BY w.name""", tid, user_id)
    return [{"warehouse_id": str(r["warehouse_id"]), "nama": r["name"]} for r in rows]


async def boleh(conn, ctx: dict) -> Optional[set]:
    """None = semua gudang; set(str uuid) = hanya itu. ctx: tenant_id, user_id, business_role_code (opsional)."""
    tid = ctx["tenant_id"]
    if (ctx.get("business_role_code") or "").upper() in PERAN_SEMUA or not ctx.get("user_id"):
        return None
    if not await flag_nyala(conn, tid):
        return None
    rows = await conn.fetch("SELECT warehouse_id FROM user_warehouse_access WHERE tenant_id = $1 AND user_id = $2", tid, ctx["user_id"])
    return {str(r["warehouse_id"]) for r in rows} or None


async def wajib(conn, ctx: dict, warehouse_ids: Iterable, medan: str = "warehouse_id") -> None:
    """Gudang di luar akses -> 403 GUDANG_TANPA_AKSES berkalimat (nama gudang). None/kosong dilewati."""
    ids = [str(w) for w in warehouse_ids if w]
    if not ids:
        return
    izin = await boleh(conn, ctx)
    if izin is None:
        return
    for w in ids:
        if w not in izin:
            nama = await conn.fetchval("SELECT name FROM warehouses WHERE id::text = $1 AND tenant_id = $2", w, ctx["tenant_id"]) or "ini"
            raise GalatBerkode(403, KODE_TANPA_AKSES,
                               f"Anda tidak punya akses ke gudang {nama}. Minta pemilik menambahkannya di Pengguna → Akses Gudang.", field=medan)


def saring(izin: Optional[set], kolom: str, nomor_param: int) -> str:
    """Potongan SQL ' AND <kolom>::text = ANY($n)' (izin None = '' ). Pemanggil menambahkan list(izin) sebagai parameter ke-n."""
    return "" if izin is None else f" AND {kolom}::text = ANY(${nomor_param}::text[])"


def teks_akses(gudang: list) -> str:
    return "Semua gudang" if not gudang else ", ".join(g["nama"] for g in gudang)


async def rencana_ganti(conn, tid: str, target_user_id: str, warehouse_ids: list) -> dict:
    """Pratinjau == tulis: validasi gudang milik usaha + aktif; dari_text/ke_text. Kosong = Semua gudang."""
    blocks, ids = [], []
    for w in warehouse_ids:
        s = str(w)
        if s not in ids:
            ids.append(s)
    gudang = []
    if ids:
        rows = await conn.fetch("SELECT id::text AS id, name, COALESCE(is_active, true) AS aktif FROM warehouses WHERE tenant_id = $1 AND id::text = ANY($2::text[])",
                                tid, ids)
        ada = {r["id"]: r for r in rows}
        for s in ids:
            r = ada.get(s)
            if r is None:
                blocks.append({"code": "GUDANG_TIDAK_DITEMUKAN", "status": 422, "message": "Gudang yang dipilih tidak ditemukan.", "field": "warehouse_ids"})
            elif not r["aktif"]:
                blocks.append({"code": "GUDANG_NONAKTIF", "status": 422, "message": f"Gudang {r['name']} nonaktif.", "field": "warehouse_ids"})
            else:
                gudang.append({"warehouse_id": s, "nama": r["name"]})
        gudang.sort(key=lambda g: g["nama"])
    lama = await daftar(conn, tid, target_user_id)
    return {"ok": not blocks, "blocks": blocks, "peringatan": [],
            "rencana": {"dari_text": teks_akses(lama), "ke_text": teks_akses(gudang), "gudang": gudang,
                        "berubah": sorted(g["warehouse_id"] for g in lama) != sorted(g["warehouse_id"] for g in gudang)}}


async def ganti(conn, tid: str, target_user_id: str, gudang: list, actor_id) -> None:
    """Tulis ganti-semua (DI DALAM transaksi pemanggil)."""
    await conn.execute("DELETE FROM user_warehouse_access WHERE tenant_id = $1 AND user_id = $2", tid, target_user_id)
    for g in gudang:
        await conn.execute("INSERT INTO user_warehouse_access (tenant_id, user_id, warehouse_id, created_by) VALUES ($1, $2, $3::uuid, $4)",
                           tid, target_user_id, g["warehouse_id"], actor_id)


def ctx_dari(request) -> dict:
    """ctx penentu dari request.state.user (business_role_code diisi permission_middleware)."""
    u = getattr(request.state, "user", None) or {}
    return {"tenant_id": u.get("tenant_id"), "user_id": u.get("user_id"), "business_role_code": u.get("business_role_code")}
