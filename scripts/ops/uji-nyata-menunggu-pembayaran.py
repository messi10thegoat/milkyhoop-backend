"""UJI NYATA MENUNGGU-PEMBAYARAN (23 Okt 2026, KONTRAK-menunggu-bayar): BACA-SAJA, kaos + grapgrap. Mode: baru [merah_dp|merah_alias]
  (1) SO: summary menunggu_pembayaran_count/_value == total daftar ?aksi=menunggu_pembayaran == ?aksi=menunggu_dp (alias) == summary alias
  (2) himpunan SO menunggu_pembayaran == {SO dari proforma belum_dibayar} DIKURANGI pengecualian tertulis (SO tak berjalan / sisa SO <= 0 /
      PELUNASAN + faktur terbit); tiap pengecualian dicetak (syarat MASTER (a))
  (3) Proforma: summary belum_dibayar_count/_value == total daftar ?aksi=belum_dibayar == Σ menunggu_bayar + segera_tempo + lewat_tempo
  (4) nilai SO == Σ sisa proforma ikut; baris daftar SO memakai kunci lama menunggu_dp selama transisi
  Kontrol merah (WAJIB FAIL): merah_dp = aturan lama (hanya proforma DP), merah_alias = alias menunggu_dp dicabut."""
import asyncio, inspect, os, sys
sys.path.insert(0, "/app/backend/api_gateway"); os.environ.setdefault("OPENAI_API_KEY", "boneka")
from decimal import Decimal
import asyncpg
from fastapi.params import Query as QP
from starlette.requests import Request

from app.routers import proformas as RP, sales_orders as RSO
from app.services import proforma_aksi as PA, so_aksi as A, so_posisi as SP
from app.services.proforma_terbayar import ringkasan_pesanan, terbayar_proforma
from app.utils.tanggal_tenant import tanggal_dokumen

MERAH = sys.argv[2] if len(sys.argv) > 2 else ""
gagal = []
NOL = Decimal("0")


def cek(nama, ok, rinci=""):
    print(("PASS " if ok else "FAIL ") + nama + ("" if ok else f"  {str(rinci)[:300]}"))
    if not ok:
        gagal.append(nama)


def req(t, uid):
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}, receive)
    r.state.user = {"tenant_id": t, "user_id": uid, "role": "OWNER"}
    return r


def kw(fn, r, **ov):
    o = {}
    for n, p in inspect.signature(fn).parameters.items():
        if n == "request": o[n] = r
        elif n in ov: o[n] = ov[n]
        elif isinstance(p.default, QP): o[n] = p.default.default
        elif p.default is not inspect._empty: o[n] = p.default
    return o


def data(x):
    if hasattr(x, "model_dump"): x = x.model_dump()
    return x.get("data", x) if isinstance(x, dict) else x


def total_daftar(x):
    x = x.model_dump() if hasattr(x, "model_dump") else x
    for k in ("total", "total_count"):
        if isinstance(x, dict) and k in x: return int(x[k])
    d = x.get("data") if isinstance(x, dict) else None
    if isinstance(d, dict):
        for k in ("total", "total_count"):
            if k in d: return int(d[k])
    raise ValueError(f"bentuk daftar tak dikenal: {list(x)[:8]}")


def baris(x):
    x = x.model_dump() if hasattr(x, "model_dump") else x
    for k in ("items", "data", "orders"):
        v = x.get(k) if isinstance(x, dict) else None
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for kk in ("items", "orders"):
                if isinstance(v.get(kk), list):
                    return v[kk]
    return []


async def main():
    c = await asyncpg.connect(os.environ["URL"]); await c.execute("SET default_transaction_read_only = on")
    _ada = {r[0] for r in await c.fetch('SELECT id FROM "Tenant"')}  # pagar: tenant salah ketik = ukur tenant KOSONG (pelajaran 23 Okt)
    _hilang = set(("kaos-biru-konveksi", "grapgrap-manado")) - _ada
    if _hilang:
        sys.exit(f"TENANT TAK ADA di \"Tenant\": {{sorted(_hilang)}} -- alat ukur menolak (hasil = tenant kosong)")
    class Pool:
        def acquire(s):
            class CM:
                async def __aenter__(x): return c
                async def __aexit__(x, *a): return False
            return CM()
        async def release(s, x): return None
    async def gp(): return Pool()
    for m in (RP, RSO):
        if hasattr(m, "get_pool"): m.get_pool = gp
    try:
        import app.services.db_pool as DBP
        DBP.get_db_pool = gp
    except Exception:
        pass
    if MERAH == "merah_dp":
        asli = SP.menunggu_pembayaran
        SP.menunggu_pembayaran = lambda st, sisa, bb, fak: asli(st, sisa, [p for p in bb if p["purpose"] == "DP"], fak)
    if MERAH == "merah_alias":
        A.AKSI_USANG = {}
    for t in ("kaos-biru-konveksi", "grapgrap-manado"):
        uid = str(await c.fetchval("SELECT created_by FROM sales_orders WHERE tenant_id=$1 AND created_by IS NOT NULL LIMIT 1", t))
        r = req(t, uid)
        hari = await tanggal_dokumen(c, t)
        sm = data(await RSO.get_sales_order_summary(**kw(RSO.get_sales_order_summary, r)))
        n_baru = total_daftar(await RSO.list_sales_orders(**kw(RSO.list_sales_orders, r, aksi="menunggu_pembayaran")))
        n_alias = total_daftar(await RSO.list_sales_orders(**kw(RSO.list_sales_orders, r, aksi="menunggu_dp")))
        cek(f"{t} SO: summary ({sm['menunggu_pembayaran_count']}) == daftar ?aksi=menunggu_pembayaran ({n_baru}) == alias ?aksi=menunggu_dp ({n_alias}) == summary alias",
            sm["menunggu_pembayaran_count"] == n_baru == n_alias == sm["menunggu_dp_count"]
            and sm["menunggu_pembayaran_value"] == sm["menunggu_dp_value"], (sm["menunggu_pembayaran_count"], n_baru, n_alias, sm["menunggu_dp_count"]))
        rows = baris(await RSO.list_sales_orders(**kw(RSO.list_sales_orders, r, aksi="menunggu_pembayaran", limit=100)))
        cek(f"{t} SO: baris memakai kunci lama menunggu_dp selama transisi", all("menunggu_dp" in (x.get("aksi_semua") or []) for x in rows)
            and not any("menunggu_pembayaran" in (x.get("aksi_semua") or []) for x in rows), [x.get("aksi_semua") for x in rows][:3])
        f = await A.fakta(c, t, hari)
        so_set = set(A.id_aksi(f, "menunggu_pembayaran"))
        peta = await RP.aksi_semua_proforma(c, t, hari, ambang=await __import__("app.services.pengingat", fromlist=["x"]).ambang_segera(c, t))
        pf_ids = [pid for pid, (k, _) in peta.items() if k in PA.BELUM_DIBAYAR]
        pfs = {p["id"]: dict(p) for p in await c.fetch(
            "SELECT p.id, p.sales_order_id, p.purpose, p.amount, so.status AS so_status, so.total_amount, so.order_number "
            "FROM proformas p JOIN sales_orders so ON so.id = p.sales_order_id AND so.tenant_id = p.tenant_id "
            "WHERE p.tenant_id = $1 AND p.id = ANY($2::uuid[])", t, pf_ids)}
        so_ids = list({p["sales_order_id"] for p in pfs.values()})
        rg = await ringkasan_pesanan(c, t, so_ids) if so_ids else {}
        faktur = {x["so_id"] for x in await c.fetch(SP.SQL_JUMLAH_FAKTUR, t, so_ids)} if so_ids else set()
        harap, kecuali = set(), []
        for p in pfs.values():
            sid = p["sales_order_id"]
            sisa_so = Decimal(str(p["total_amount"] or 0)) - rg[sid]["tertutup"]
            alasan = ("SO tak berjalan" if p["so_status"] in SP.SO_TAK_BERJALAN else "sisa SO <= 0" if sisa_so <= NOL
                      else "PELUNASAN + faktur terbit" if (p["purpose"] == "PELUNASAN" and sid in faktur) else None)
            if alasan:
                kecuali.append((p["order_number"], p["purpose"], alasan))
            else:
                harap.add(sid)
        print(f"INFO {t} pengecualian proforma belum_dibayar: {kecuali}")
        nomor = {p["sales_order_id"]: p["order_number"] for p in pfs.values()}
        cek(f"{t} himpunan SO menunggu_pembayaran ({len(so_set)}) == SO proforma belum_dibayar − pengecualian ({len(harap)})",
            so_set == harap, (sorted(nomor.get(i, str(i)) for i in so_set - harap), sorted(nomor.get(i, str(i)) for i in harap - so_set)))
        nilai = sum((f[i]["nilai"]["menunggu_pembayaran"] for i in so_set), NOL)
        cek(f"{t} SO nilai summary == Σ sisa proforma ikut ({nilai})", Decimal(str(sm["menunggu_pembayaran_value"])).quantize(Decimal("0.01")) == nilai.quantize(Decimal("0.01")),
            (sm["menunggu_pembayaran_value"], nilai))
        ps = data(await RP.get_proforma_summary(**kw(RP.get_proforma_summary, r)))
        n_pf = total_daftar(await RP.list_proformas(**kw(RP.list_proformas, r, aksi="belum_dibayar")))
        tiga = sum(ps[f"{k}_count"] for k in PA.BELUM_DIBAYAR)
        cek(f"{t} Proforma: summary belum_dibayar ({ps['belum_dibayar_count']}) == daftar ?aksi=belum_dibayar ({n_pf}) == Σ tiga kunci terbit ({tiga}) == pemilih ({len(pf_ids)})",
            ps["belum_dibayar_count"] == n_pf == tiga == len(pf_ids)
            and abs(ps["belum_dibayar_value"] - sum(ps[f"{k}_value"] for k in PA.BELUM_DIBAYAR)) < 0.005, (ps["belum_dibayar_count"], n_pf, tiga, len(pf_ids)))
        print(f"INFO {t} SO menunggu_pembayaran {len(so_set)} / {nilai}: {sorted(nomor.get(i, str(i)) for i in so_set)}")
    await c.close()
    print(f"SELESAI gagal={len(gagal)}")
    sys.exit(1 if gagal else 0)


asyncio.run(main())
