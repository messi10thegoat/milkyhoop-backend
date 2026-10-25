"""Rute percakapan Milkybook (kontrak rancangan §11.10 + kontrak T3 #7/#9/#10). Didaftarkan lewat app/milkybook/pasang.py.

- Pengguna, tenant, dan izin dari token (request.state.user, diisi middleware autentikasi); badan tak pernah memuat tenant.
  SEMUA rute sesi memeriksa sesi milik tenant x pengguna itu (pengguna lain / tenant lain = 404).
- Flag tenant `milkybook_v1` mati = 404.
- Penyimpanan sesi bersama (Redis; gateway 2 worker), kunci per sesi.
- #10: Accept: text/event-stream -> SSE (langkah/kartu/galat mengalir; teks utuh sesudah penjaga; ": ping" tiap 15 dtk;
  X-Accel-Buffering: no; byte pertama SEGERA, Cloudflare memutus ~100 dtk tanpa byte). Tanpa header -> JSON lama.
  Giliran berjalan di tugas latar: klien putus = giliran tetap selesai, dipulihkan lewat GET /sesi/{id}.
- Kotak masuk: pesan saat sesi sibuk -> 202 antre (maks aliran.MAKS_ANTRE), dibaca inti sebelum panggilan model berikutnya;
  Setuju/Tolak atomik (antre diproses SESUDAHNYA sebagai giliran baru). Setuju/Tolak berbarengan tetap 409.
  Tak ada pesan terlantar: tugas yang selesai memeriksa antre lagi SESUDAH melepas kunci; penambah antre mencoba
  mengambil kunci SESUDAH menambah (salah satunya pasti memproses).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import aliran
from .audit import BarisAudit
from .pelaksana import PelaksanaASGI
from .penyimpanan import AntreanPenuh, SesiSibuk

logger = logging.getLogger(__name__)

FLAG = "milkybook_v1"
MAKS_TAMPILAN = 400  # peristiwa tampilan per sesi (sesi hidup 6 jam); lebih = yang tertua dibuang
_TUGAS: set = set()  # rujukan tugas latar (tanpa ini asyncio boleh membuangnya)


class PesanMasuk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    teks: str = Field(min_length=1, max_length=8000)
    id_pesan: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9-]{8,64}$")  # uuid dari FE (idempoten)


class UbahKartu(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict  # payload kartu LENGKAP sesudah diedit FE (#8)


def buat_router(putaran_fn: Callable, penyimpanan, *, app_fn: Callable, izin_fn_untuk: Callable,
                flag_fn: Callable, hari_ini_fn: Callable) -> APIRouter:
    """putaran_fn() -> Putaran (dibuat malas: kunci API & model dari env saat permintaan pertama);
    app_fn() -> app FastAPI; izin_fn_untuk(user) -> IzinFn; flag_fn(tenant_id) -> awaitable bool;
    hari_ini_fn(tenant_id) -> awaitable date (tanggal bisnis tenant)."""
    r = APIRouter()

    async def _pengguna(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user or not user.get("tenant_id") or not user.get("user_id"):
            raise HTTPException(401, "Tidak terautentikasi")
        if not await flag_fn(user["tenant_id"]):
            raise HTTPException(404, "Tidak ditemukan")
        return user

    def _alat(request: Request, user: dict):
        """Pelaksana + izin yang tetap sah sesudah permintaan selesai (tugas latar)."""
        auth = request.headers.get("Authorization", "")
        return lambda: PelaksanaASGI(app_fn(), auth), izin_fn_untuk(user)

    async def _sesi(user, sesi_id):
        s = await penyimpanan.ambil(user["tenant_id"], user["user_id"], sesi_id)
        if s is None:
            raise HTTPException(404, "Sesi tidak ditemukan")
        return s

    def _mau_aliran(request: Request) -> bool:
        return "text/event-stream" in request.headers.get("accept", "")

    # ------------------------------------------------------------------ giliran (JSON atau SSE)
    async def _lanjut_antre(s, a, pegangan, alat):
        """Pesan antre yang belum terbaca -> giliran baru (sesudah Setuju/Tolak, atau datang sesudah panggilan model terakhir)."""
        buat_pelaksana, izin = alat
        while True:
            pesan = await penyimpanan.antre_ambil_semua(s.tenant_id, s.user_id, s.sesi_id)
            if not pesan:
                return
            await pegangan.perpanjang()
            a.pancar({"jenis": "antre_dibaca", "id_pesan": [m["id_pesan"] for m in pesan]})
            for m in pesan:
                a.pancar({"jenis": "pengguna", "id_pesan": m["id_pesan"], "teks": m["teks"], "dari_antrean": True})
                s.diproses_id.append(m["id_pesan"])
                a.antre_dibaca.append(aliran.sidik_pesan(m["teks"]))
            a.boleh_sisip = True
            for e in await putaran_fn().giliran(s, "\n\n".join(m["teks"] for m in pesan), buat_pelaksana(), izin):
                a.pancar(e)

    async def _kerjakan(s, cm, pegangan, a, kerja, masuk, alat, sse: bool):
        """Bug non-SDK: SSE -> galat kode internal (stream tak putus diam); JSON -> TETAP 500 (putusan #9). Keduanya:
        Traceback di log + audit baris model "galat:internal:<jenis>"."""
        token = aliran.pasang(a)
        bug: Optional[BaseException] = None
        try:
            try:
                if masuk:
                    s.tampilan.append(masuk)
                    if masuk.get("id_pesan"):
                        s.diproses_id.append(masuk["id_pesan"])
                for e in await kerja(s):
                    a.pancar(e)  # yang belum dipancarkan (jalur sederhana: galat kartu, tolak)
                await _lanjut_antre(s, a, pegangan, alat)
            except Exception as e:
                logger.exception("milkybook: giliran gagal")
                await _audit_bug(s, e)
                if sse:
                    a.pancar({"jenis": "galat", "kode": "internal", "pesan": "Terjadi kesalahan. Coba lagi."})
                    if a.selesai is None:  # giliran berakhir jelas (FE tak perlu menebak)
                        a.pancar({"jenis": "selesai", "galat": True})
                else:
                    bug = e
            finally:
                s.tampilan.extend(a.semua)
                del s.tampilan[:-MAKS_TAMPILAN]
                try:
                    await penyimpanan.simpan(s)  # simpan juga bila galat di tengah: riwayat append-only tetap utuh
                finally:
                    await cm.__aexit__(None, None, None)
            # Balapan: pesan yang masuk antre sesudah pemeriksaan terakhir tapi sebelum kunci lepas.
            if await penyimpanan.antre_daftar(s.tenant_id, s.user_id, s.sesi_id):
                await _proses_antre_berkunci(s.tenant_id, s.user_id, s.sesi_id, a, alat)
        finally:
            aliran.lepas(token)
            a.tutup()
        if bug is not None:
            raise bug  # JSON: tugas gagal -> handler 500 (kunci sudah lepas, sesi tersimpan)

    async def _audit_bug(s, e):
        try:
            await putaran_fn().audit.tulis(BarisAudit(s.tenant_id, s.user_id, s.sesi_id, "model",
                                                      hasil=f"galat:internal:{type(e).__name__}"[:500]))
        except Exception:
            logger.exception("milkybook: audit galat gagal")

    async def _proses_antre_berkunci(tenant_id, user_id, sesi_id, a, alat):
        try:
            cm = penyimpanan.kunci(tenant_id, user_id, sesi_id)
            pegangan = await cm.__aenter__()
        except SesiSibuk:
            return  # giliran lain sedang berjalan: ia yang membaca antre
        try:
            s = await penyimpanan.ambil(tenant_id, user_id, sesi_id)
            if s is None:
                return
            n = len(a.semua)
            try:
                await _lanjut_antre(s, a, pegangan, alat)
            except Exception as e:
                logger.exception("milkybook: giliran antre gagal")
                await _audit_bug(s, e)
                a.pancar({"jenis": "galat", "kode": "internal", "pesan": "Terjadi kesalahan. Coba lagi."})
            finally:
                s.tampilan.extend(a.semua[n:])
                del s.tampilan[:-MAKS_TAMPILAN]
                await penyimpanan.simpan(s)
        finally:
            await cm.__aexit__(None, None, None)

    def _latar(coro):
        t = asyncio.create_task(coro)
        _TUGAS.add(t)
        t.add_done_callback(_TUGAS.discard)
        return t

    async def _sse(a: aliran.Aliran):
        yield ": mulai\n\n"  # byte pertama segera (Cloudflare 524 bila origin diam ~100 dtk)
        while True:
            try:
                e = await asyncio.wait_for(a.antrian.get(), aliran.DETIK_DETAK)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if e is None:
                return
            yield f"event: {e['jenis']}\ndata: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"

    def _respons_aliran(a):
        return StreamingResponse(_sse(a), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def _jalankan(request, user, sesi_id, kerja, masuk: dict, *, boleh_sisip: bool):
        """Kunci (SesiSibuk -> pemanggil), lalu giliran di tugas latar. SSE: aliran; JSON: tunggu tugas (shield)."""
        cm = penyimpanan.kunci(user["tenant_id"], user["user_id"], sesi_id)
        pegangan = await cm.__aenter__()
        try:
            s = await _sesi(user, sesi_id)
        except BaseException:
            await cm.__aexit__(None, None, None)
            raise
        tenant_id, user_id = user["tenant_id"], user["user_id"]
        a = aliran.Aliran(ambil_antre=lambda: penyimpanan.antre_ambil_semua(tenant_id, user_id, sesi_id), boleh_sisip=boleh_sisip)
        sse = _mau_aliran(request)
        tugas = _latar(_kerjakan(s, cm, pegangan, a, kerja, masuk, _alat(request, user), sse))
        if sse:
            return _respons_aliran(a)
        await asyncio.shield(tugas)  # klien putus: tugas tetap selesai
        return {"peristiwa": a.semua + ([a.selesai] if a.selesai else [])}

    def _duplikat(request):
        if _mau_aliran(request):
            a = aliran.Aliran()
            a.pancar({"jenis": "selesai", "duplikat": True})
            a.tutup()
            return _respons_aliran(a)
        return {"duplikat": True, "peristiwa": []}

    # ------------------------------------------------------------------ rute
    @r.post("/sesi")
    async def sesi_baru(request: Request):
        user = await _pengguna(request)
        hari_ini = await hari_ini_fn(user["tenant_id"])
        s = await penyimpanan.buat(user["tenant_id"], user["user_id"], konteks=f"Hari ini {hari_ini.isoformat()}.",
                                   hari_ini=hari_ini)
        return {"sesi_id": s.sesi_id}

    @r.get("/sesi/{sesi_id}")
    async def pulihkan(sesi_id: str, request: Request):
        """#7: tampilan percakapan untuk dimuat ulang FE (tanpa kunci giliran; baca saja). 404 = sesi kedaluwarsa/tenant lain."""
        user = await _pengguna(request)
        s = await _sesi(user, sesi_id)
        k = s.kartu_menunggu()
        t, u = user["tenant_id"], user["user_id"]
        return {"sesi_id": s.sesi_id, "peristiwa": s.tampilan, "kartu_menunggu": k.kartu_id if k else None,
                "sedang_bekerja": await penyimpanan.sibuk(t, u, sesi_id), "antre": await penyimpanan.antre_daftar(t, u, sesi_id)}

    @r.post("/sesi/{sesi_id}/pesan")
    async def pesan(sesi_id: str, body: PesanMasuk, request: Request):
        user = await _pengguna(request)
        t, u = user["tenant_id"], user["user_id"]
        s = await _sesi(user, sesi_id)
        idp = body.id_pesan
        if idp:
            st = await penyimpanan.antre_status(t, u, sesi_id, idp)
            if idp in s.diproses_id or st == "dibaca":
                return _duplikat(request)
            if st == "antre":
                posisi = next(x["posisi"] for x in await penyimpanan.antre_daftar(t, u, sesi_id) if x["id_pesan"] == idp)
                return JSONResponse({"antre": True, "posisi": posisi, "id_pesan": idp}, status_code=202)
        masuk = {"jenis": "pengguna", "teks": body.teks, **({"id_pesan": idp} if idp else {})}
        buat_pelaksana, izin = _alat(request, user)
        try:
            return await _jalankan(request, user, sesi_id,
                                   lambda s: putaran_fn().giliran(s, body.teks, buat_pelaksana(), izin), masuk, boleh_sisip=True)
        except SesiSibuk:
            pass
        # Sesi sibuk: masuk kotak masuk (dibaca giliran yang sedang berjalan).
        if len(body.teks) > aliran.MAKS_TEKS_ANTRE:
            raise HTTPException(422, {"kode": "pesan_terlalu_panjang",
                                      "detail": f"Pesan saat Milkybook bekerja maksimal {aliran.MAKS_TEKS_ANTRE} karakter."})
        idp = idp or uuid.uuid4().hex
        try:
            posisi = await penyimpanan.antre_tambah(t, u, sesi_id, idp, body.teks)
        except AntreanPenuh:
            return JSONResponse({"detail": "Antrean pesan penuh. Tunggu Milkybook selesai.", "kode": "antrean_penuh"},
                                status_code=429)
        _latar(_proses_antre_latar(t, u, sesi_id, _alat(request, user)))
        return JSONResponse({"antre": True, "posisi": posisi, "id_pesan": idp}, status_code=202)

    async def _proses_antre_latar(tenant_id, user_id, sesi_id, alat):
        """Penambah antre mencoba kunci SESUDAH menambah: giliran sudah selesai -> proses sendiri (tanpa pendengar;
        hasil masuk tampilan, FE melihatnya lewat GET /sesi)."""
        a = aliran.Aliran(ambil_antre=lambda: penyimpanan.antre_ambil_semua(tenant_id, user_id, sesi_id))
        token = aliran.pasang(a)
        try:
            await _proses_antre_berkunci(tenant_id, user_id, sesi_id, a, alat)
        finally:
            aliran.lepas(token)

    @r.delete("/sesi/{sesi_id}/antre/{id_pesan}")
    async def batal_antre(sesi_id: str, id_pesan: str, request: Request):
        """Esc di FE: batalkan pesan antre yang BELUM dibaca model."""
        user = await _pengguna(request)
        await _sesi(user, sesi_id)
        hasil = await penyimpanan.antre_batal(user["tenant_id"], user["user_id"], sesi_id, id_pesan)
        if hasil == "dibatalkan":
            return {"dibatalkan": True, "id_pesan": id_pesan}
        if hasil == "sudah_dibaca":
            return JSONResponse({"detail": "Pesan sudah dibaca Milkybook.", "kode": "antre_sudah_dibaca"}, status_code=409)
        raise HTTPException(404, "Pesan antre tidak ditemukan")

    async def _sesi_kartu(user, kartu_id):
        sid = await penyimpanan.sesi_untuk_kartu(user["tenant_id"], user["user_id"], kartu_id)
        if not sid:
            raise HTTPException(404, "Kartu tidak ditemukan")
        return sid

    async def _aksi_kartu(request, user, sid, kerja, masuk):
        try:
            return await _jalankan(request, user, sid, kerja, masuk, boleh_sisip=False)  # Setuju/Tolak atomik
        except SesiSibuk:
            raise HTTPException(409, "Percakapan ini sedang diproses. Tunggu sebentar.")

    @r.post("/kartu/{kartu_id}/setuju")
    async def setuju(kartu_id: str, request: Request):
        user = await _pengguna(request)
        sid = await _sesi_kartu(user, kartu_id)
        buat_pelaksana, izin = _alat(request, user)
        return await _aksi_kartu(request, user, sid, lambda s: putaran_fn().setujui(s, kartu_id, buat_pelaksana(), izin),
                                 {"jenis": "aksi_pengguna", "aksi": "setuju", "kartu_id": kartu_id})

    @r.post("/kartu/{kartu_id}/ubah")
    async def ubah(kartu_id: str, body: UbahKartu, request: Request):
        """#8 ubah isian langsung di kartu: pratinjau ulang, kartu lama BASI, kartu BARU (menggantikan). Nol model.
        X-Idempotency-Key: ulang = peristiwa respons pertama."""
        user = await _pengguna(request)
        sid = await _sesi_kartu(user, kartu_id)
        s = await _sesi(user, sid)
        kunci = request.headers.get("X-Idempotency-Key") or None
        if kunci and kunci in s.ubah_idem:
            return {"peristiwa": s.ubah_idem[kunci], "replay": True}
        cek = putaran_fn().periksa_ubah(s, kartu_id, body.payload)
        if cek:
            return JSONResponse({"kode": cek[1], "detail": cek[2]}, status_code=cek[0])
        buat_pelaksana, izin = _alat(request, user)
        return await _aksi_kartu(request, user, sid,
                                 lambda s: putaran_fn().ubah_kartu(s, kartu_id, body.payload, buat_pelaksana(), izin, kunci),
                                 {"jenis": "aksi_pengguna", "aksi": "ubah", "kartu_id": kartu_id})

    @r.post("/kartu/{kartu_id}/tolak")
    async def tolak(kartu_id: str, request: Request):
        user = await _pengguna(request)
        sid = await _sesi_kartu(user, kartu_id)
        return await _aksi_kartu(request, user, sid, lambda s: putaran_fn().tolak(s, kartu_id),
                                 {"jenis": "aksi_pengguna", "aksi": "tolak", "kartu_id": kartu_id})

    return r
