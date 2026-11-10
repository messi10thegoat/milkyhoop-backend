"""Pembaca lampiran Kas&Bank + Kasbon (GET /api/lampiran/{jenis}/{id}) -- uji di SALINAN, handler ASLI. SIAPKAN=1 (pohon LAMA, sekali):
lampiran uji per dokumen (2 s3 + 1 'local' + 1 terhapus) ditulis HANYA di klon. Syarat MASTER 10 Nov:
 D  (baru) per jenis, SETIAP dokumen kaos/nyawit yang dipasangi lampiran: daftar pembaca == pabrik GET /api/{modul}/{id}/attachments
    (id, nama, mime, ukuran, tersedia, url; HIMPUNAN; urutan hanya dicatat -- pembaca urut waktu unggah + id, pabrik
    urut display_order) dan nomor == nomor induk pabrik.
 X  (baru) lintas tenant: dokumen tenant A dibaca tenant B -> pembaca 404 DAN pabrik 404.
 P  (baru) kasbon: pemilik -> 200; pengguna luar pay-group -> pembaca 404 DAN pabrik 404.
 PS (baru) kasbon, pay-group SEMPIT: HR_PAYROLL non-pemilik ber-akses pay-group kasbon -> terbaca == pabrik; ber-akses HANYA pay-group lain
    -> 404 di pembaca DAN pabrik (pengguna + akses dibuat HANYA di klon, SIAPKAN).
 L  jenis lama (dokumen penjualan/pembelian, sampel) -> keluaran LAMA == BARU (ditulis ke KELUAR, dibanding di luar)."""
import asyncio
import json
import os
import sys
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.insert(0, "/app/backend/api_gateway")
from app.middleware import permission_middleware as pm  # noqa: E402
from app.routers import lampiran_dokumen as LD  # noqa: E402
from app.routers import lampiran_induk as LI  # noqa: E402
from app.services.db_pool import get_db_pool  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = hasattr(LD, "JENIS_TUNGGAL")
ORANG_LUAR = "00000000-0000-0000-0000-0000000000b2"
# pay-group SEMPIT (syarat MASTER 10 Nov): dua pengguna NON-pemilik ber-peran HR_PAYROLL, dibuat HANYA di klon (SIAPKAN):
# DALAM = akses ke pay-group kasbon + satu pay-group lain; LUAR = akses ke SATU pay-group LAIN saja (punya akses, tapi bukan kasbon itu)
PG_DALAM = "00000000-0000-0000-0000-0000000000c1"
PG_LUAR = "00000000-0000-0000-0000-0000000000c2"
SPEK = {"bank_transaction": LI.SPEC_MUTASI_BANK, "bank_transfer": LI.SPEC_TRANSFER_BANK, "employee_advance": LI.SPEC_KASBON}
CEK, OUT = [], {"lama_jenis": {}, "urutan": {}}
PG_SIAP = {}


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:600]])


class PESemua:  # izin grup diuji di unit; di sini semua grup boleh agar isi grup terbandingkan
    async def get_user_context(self, user_id, tenant_id, subscription_role):
        return SimpleNamespace(membership_active=True, business_role_id="r", business_role_code="OWNER")

    async def can(self, ctx, aksi, modul):
        return True


def req(tid, uid):
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tid, "user_id": uid, "role": "OWNER"}))


def j(v):
    return json.loads(json.dumps(v, default=str))


async def panggil(fn, *a, **kw):
    try:
        return 200, j(await fn(*a, **kw))
    except HTTPException as e:
        return e.status_code, j(e.detail)


def rute(spec, nama):
    r = {"bank_transaction": LI.router_mutasi_bank, "bank_transfer": LI.router_transfer_bank, "employee_advance": LI.router_kasbon}
    return next(x.endpoint for x in r[spec.entitas].routes if x.endpoint.__name__ == f"{nama}_lampiran_{spec.entitas}")


async def pemilik(conn, tid):
    return str(await conn.fetchval("""SELECT utr.user_id FROM user_tenant_roles utr JOIN roles r ON r.id = utr.role_id
                                      WHERE utr.tenant_id = $1 AND r.code = 'OWNER' AND lower(utr.status) = 'active'
                                      ORDER BY utr.user_id LIMIT 1""", tid))


async def dokumen_uji(conn, tid, jenis):
    s = SPEK[jenis]
    return [r["id"] for r in await conn.fetch(f"SELECT id FROM {s.tabel} WHERE tenant_id = $1 ORDER BY id LIMIT 3", tid)]


async def siapkan(conn):
    n = 0
    for tid in TENANT:
        uid = await pemilik(conn, tid)
        for jenis, s in SPEK.items():
            for i, did in enumerate(await dokumen_uji(conn, tid, jenis)):
                for k, (st, hapus) in enumerate((("s3", False), ("s3", False), ("local", False), ("s3", True))):
                    dok = await conn.fetchval(
                        """INSERT INTO documents (tenant_id, file_name, original_name, file_type, file_extension, file_size, storage_type,
                                                  file_path, category, source, uploaded_by, deleted_at)
                           VALUES ($1, $2, $2, 'image/png', '.png', $3, $4, $5, 'other', 'upload', $6::uuid,
                                   CASE WHEN $7 THEN now() END) RETURNING id""",
                        tid, f"UJI-{jenis}-{i}-{k}.png", 1000 + k, st, f"{tid}/uploads/lampiran/uji-{uuid.uuid4().hex}.png", uid, hapus)
                    await conn.execute("""INSERT INTO document_attachments (tenant_id, document_id, entity_type, entity_id, attachment_type,
                                                                             display_order, attached_by, display_name)
                                          VALUES ($1, $2, $3, $4, 'attachment', $5, $6::uuid, $7)""",
                                       tid, dok, s.entitas, did, k, uid, f"UJI nama tautan {k}" if k == 1 else None)
                    n += 1
    print(f"[siapkan] {n} tautan lampiran uji")
    peran = await conn.fetchval("SELECT id FROM roles WHERE code = 'HR_PAYROLL' AND tenant_id = '__SYSTEM__'")
    for tid in TENANT:
        pg_kasbon = {r["pay_group_id"] for r in await conn.fetch(
            """SELECT DISTINCT e.pay_group_id FROM employee_advances ea JOIN employees e ON e.id = ea.employee_id AND e.tenant_id = ea.tenant_id
               WHERE ea.tenant_id = $1 AND e.pay_group_id IS NOT NULL""", tid)}
        lain = [r["id"] for r in await conn.fetch("SELECT id FROM pay_groups WHERE tenant_id = $1 ORDER BY id", tid) if r["id"] not in pg_kasbon]
        if not pg_kasbon or not lain:
            continue
        for uid, akses in ((PG_DALAM, sorted(pg_kasbon) + lain[:1]), (PG_LUAR, lain[:1])):
            await conn.execute("""INSERT INTO "User" (id, email) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING""", uid, f"uji-pg-{uid[-2:]}@uji.local")
            await conn.execute("""INSERT INTO user_tenant_roles (user_id, tenant_id, role_id, status) VALUES ($1::uuid, $2, $3, 'ACTIVE')""",
                               uid, tid, peran)
            for pg in akses:
                await conn.execute("INSERT INTO user_pay_group_access (user_id, tenant_id, pay_group_id) VALUES ($1::uuid, $2, $3)", uid, tid, pg)
        print(f"[siapkan] {tid}: pengguna pay-group sempit DALAM ({len(pg_kasbon) + 1} pg) & LUAR (1 pg lain)")


def ringkas_pembaca(out):
    return [(x["id"], x["display_name"], x["mime"], x["ukuran"], x["tersedia"], x["url"])
            for g in out["data"] for x in g.get("lampiran", [])]


def ringkas_pabrik(out):  # konvensi pembaca keluarga: berkas tak tersedia -> url null (pabrik tetap mengirim url)
    return [(x["id"], x["nama"], x["mime"], x["ukuran"], x["tersedia"], x["url"] if x["tersedia"] else None) for x in out["data"]]


async def utama():
    pm.get_policy_engine = lambda: PESemua()
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if os.environ.get("SIAPKAN") == "1":
            async with conn.transaction():
                await siapkan(conn)
        owner = {tid: await pemilik(conn, tid) for tid in TENANT}
        sampel = {}
        for tid in TENANT:
            for jenis, (tabel, _) in {**LD.JENIS_DOKUMEN, **LD.JENIS_PEMBELIAN}.items():
                sampel[(tid, jenis)] = [r["id"] for r in await conn.fetch(
                    f"SELECT id FROM {tabel} WHERE tenant_id = $1 ORDER BY id LIMIT 8", tid)]
        uji = {(tid, jenis): await dokumen_uji(conn, tid, jenis) for tid in TENANT for jenis in SPEK}
        for tid in TENANT:
            PG_SIAP[tid] = bool(await conn.fetchval(
                "SELECT count(*) FROM user_pay_group_access WHERE tenant_id = $1 AND user_id = $2::uuid AND revoked_at IS NULL", tid, PG_LUAR))
        induk = {}
        for (tid, jenis), ids in uji.items():
            for did in ids:
                induk[str(did)] = (await conn.fetchrow(LI.sql_spec(SPEK[jenis])["induk"], did, tid))["nomor"]
    for (tid, jenis), ids in sampel.items():
        for did in ids:
            OUT["lama_jenis"][f"{tid}|{jenis}|{did}"] = await panggil(LD.lampiran_dokumen, req(tid, owner[tid]), jenis, str(did))
    if not BARU:
        return
    lain = {TENANT[0]: TENANT[-1], TENANT[-1]: TENANT[0]}
    for (tid, jenis), ids in uji.items():
        s = SPEK[jenis]
        cek(f"D {tid} {jenis} ada dokumen uji", ids or jenis == "employee_advance", "tak ada dokumen")
        for did in ids:
            sp, pb = await panggil(LD.lampiran_dokumen, req(tid, owner[tid]), jenis, str(did))
            sf, pf = await panggil(rute(s, "daftar"), req(tid, owner[tid]), doc_id=did)
            if sp != 200 or sf != 200:
                cek(f"D {tid} {jenis} {did} status", False, f"pembaca {sp} {pb} pabrik {sf} {pf}")
                continue
            a, b = ringkas_pembaca(pb), ringkas_pabrik(pf)
            cek(f"D {tid} {jenis} {did} daftar == pabrik ({len(b)})", sorted(map(tuple, a)) == sorted(map(tuple, b)) and len(b) == 3,
                f"pembaca {a} pabrik {b}")
            OUT["urutan"][f"{tid}|{jenis}|{did}"] = [x[0] for x in a] == [x[0] for x in b]  # info: pembaca urut waktu unggah+id
            cek(f"D {tid} {jenis} {did} nomor+mode", pb["sumber"]["nomor"] == induk[str(did)] and pb["sumber"]["mode"] == "dokumen"
                and pb["jumlah"] == 3, pb["sumber"])
            ot = lain[tid]
            if ot != tid:
                x1 = await panggil(LD.lampiran_dokumen, req(ot, owner[ot]), jenis, str(did))
                x2 = await panggil(rute(s, "daftar"), req(ot, owner[ot]), doc_id=did)
                cek(f"X {tid}->{ot} {jenis} {did} 404", x1[0] == 404 and x2[0] == 404, f"pembaca {x1} pabrik {x2}")
            if jenis == "employee_advance" and PG_SIAP.get(tid):
                d1 = await panggil(LD.lampiran_dokumen, req(tid, PG_DALAM), jenis, str(did))
                d2 = await panggil(rute(s, "daftar"), req(tid, PG_DALAM), doc_id=did)
                cek(f"PS {tid} kasbon {did} pay-group sempit DALAM terbaca & == pabrik",
                    d1[0] == 200 and d2[0] == 200 and sorted(map(tuple, ringkas_pembaca(d1[1]))) == sorted(map(tuple, ringkas_pabrik(d2[1]))),
                    f"pembaca {d1[0]} pabrik {d2[0]}")
                l1 = await panggil(LD.lampiran_dokumen, req(tid, PG_LUAR), jenis, str(did))
                l2 = await panggil(rute(s, "daftar"), req(tid, PG_LUAR), doc_id=did)
                cek(f"PS {tid} kasbon {did} pay-group sempit LUAR 404", l1[0] == 404 and l2[0] == 404, f"pembaca {l1} pabrik {l2}")
            if jenis == "employee_advance":
                p1 = await panggil(LD.lampiran_dokumen, req(tid, ORANG_LUAR), jenis, str(did))
                p2 = await panggil(rute(s, "daftar"), req(tid, ORANG_LUAR), doc_id=did)
                cek(f"P {tid} kasbon {did} luar pay-group 404", p1[0] == 404 and p2[0] == 404, f"pembaca {p1} pabrik {p2}")
    cek("P kasbon teruji", any(uji[(t, "employee_advance")] for t in TENANT), "tak ada kasbon di tenant uji")
    cek("PS pay-group sempit teruji di >= 1 tenant", any(PG_SIAP.values()), "tak ada tenant dengan kasbon ber-pay-group + pay-group lain")


asyncio.run(utama())
json.dump({"cek": CEK, **OUT}, open(os.environ["KELUAR"], "w"), default=str)
print(f"[{'baru' if BARU else 'lama'}] cek {sum(1 for c in CEK if c[1])}/{len(CEK)}, jenis lama {len(OUT['lama_jenis'])}")
