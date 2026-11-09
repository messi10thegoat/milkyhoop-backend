"""LAMPIRAN Kas & Bank + Kasbon + Penerimaan/Pembayaran keluar -- uji di SALINAN (klon milkydb yang SUDAH memuat V493), handler pabrik
pohon BARU. Penyimpanan objek = TIRUAN di memori (TIDAK menulis ke MinIO produksi). Tulis hanya di klon (trap).
 L1 per modul (kaos + nyawit, dokumen nyata pertama): unggah 2 berkas -> daftar memuat keduanya (bila rute daftar ada) -> lepas 1 -> kuota turun
 L2 kuota 10: unggah 11 berkas -> 10 ok + 1 kuota_penuh
 L3 'payment' terpisah: lampiran di penerimaan X -> lepas lewat rute pembayaran keluar dengan id X = 404; GET daftar LAMA penerimaan memuatnya
 L4 duplikat berkas yang sama di dokumen yang sama -> galat duplikat
 L5 dokumen tenant lain / tak ada -> 404 (induk bersaring tenant)
 L6 kasbon di luar pay-group (employee_in_scope = False) -> 404 di klon nyata; jalur positif memakai lingkup = True (peran pengguna
    tiruan tak ada di DB; pagar dua arah juga diuji unit)"""
import asyncio
import io
import json
import os
import sys
import uuid

from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import lampiran_induk as LI  # noqa: E402
from app.routers import receive_payments as RP  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
CEK = []
GUDANG = {}


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:400]])
    print(("OK   " if ok else "FAIL ") + nama + ("" if ok else f" -- {str(info)[:300]}"), flush=True)


async def simpan_tiruan(storage, kunci, isi):
    GUDANG[kunci] = isi


def req(tid):
    r = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "role": "OWNER"}
    return r


def berkas(i, isi=None):
    data = isi or (b"%PDF-1.4\n%uji-salinan-lampiran " + str(i).encode() + b"\n" + os.urandom(16) + b"\n%%EOF\n")
    return UploadFile(file=io.BytesIO(data), filename=f"bukti-{i}.pdf", headers=Headers({"content-type": "application/pdf"}))


def rute(router, dasar):
    return next((r.endpoint for r in router.routes if r.endpoint.__name__.startswith(dasar + "_")
                 or r.endpoint.__name__ == dasar), None)


SPEC = [(LI.router_mutasi_bank, LI.SPEC_MUTASI_BANK), (LI.router_transfer_bank, LI.SPEC_TRANSFER_BANK), (LI.router_kasbon, LI.SPEC_KASBON),
        (LI.router_penerimaan, LI.SPEC_PENERIMAAN), (LI.router_bayar_keluar, LI.SPEC_BAYAR_KELUAR)]


async def utama():
    LI.simpan_objek_unggahan = simpan_tiruan            # rute pabrik membaca lewat dep() saat dipanggil -> tiruan
    LI.get_storage_service = lambda: object()
    import app.services.pay_group_access as PGA
    LINGKUP = {"boleh": True}

    async def lingkup(*a, **k):
        return LINGKUP["boleh"]
    PGA.employee_in_scope = lingkup
    pool = await LI.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_lampiran_"):
            sys.exit(f"BUKAN salinan: {db}")
        kon = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint WHERE conname = 'chk_da_entity'")
        cek("klon memuat V493", all(f"'{v}'" in kon for v in ("bank_transaction", "bank_transfer", "employee_advance")), kon)
        for tid in TENANT:
            for router, s in SPEC:
                did = await conn.fetchval(f"SELECT id FROM {s.tabel} WHERE tenant_id = $1 ORDER BY created_at, id LIMIT 1", tid)
                nm = f"{tid} {s.modul_url}"
                if not did:
                    print(f"[info] {nm}: tak ada dokumen, dilewati", flush=True)
                    continue
                unggah, daftar, lepas = rute(router, "unggah"), rute(router, "daftar"), rute(router, "lepas")
                h = await unggah(req(tid), did, [berkas(1), berkas(2)])
                ok_ids = [x["id"] for x in h["hasil"] if x["ok"]]
                cek(f"{nm} L1 unggah 2", len(ok_ids) == 2, h)
                n_db = await conn.fetchval("SELECT count(*) FROM document_attachments WHERE tenant_id = $1 AND entity_type = $2 AND entity_id = $3",
                                           tid, s.entitas, did)
                cek(f"{nm} L1 tautan tersimpan entity {s.entitas}", n_db >= 2, n_db)
                if daftar:
                    d = await daftar(req(tid), did)
                    cek(f"{nm} L1 daftar memuat", {x["id"] for x in d["data"]} >= set(ok_ids), d)
                h2 = await unggah(req(tid), did, [berkas(1, b"%PDF-1.4\nsama\n%%EOF\n"), berkas(9, b"%PDF-1.4\nsama\n%%EOF\n")])
                cek(f"{nm} L4 duplikat ditolak", [x.get("galat") for x in h2["hasil"]] == [None, "duplikat"], h2)
                l = await lepas(req(tid), did, uuid.UUID(ok_ids[0]))
                cek(f"{nm} L1 lepas", l["success"] and l["kuota"]["terpakai"] == h2["kuota"]["terpakai"] - 1, (h2["kuota"], l))
                try:
                    await unggah(req(tid), uuid.uuid4(), [berkas(3)])
                    cek(f"{nm} L5 dokumen tak ada -> 404", False, "lolos")
                except HTTPException as e:
                    cek(f"{nm} L5 dokumen tak ada -> 404", e.status_code == 404, e.detail)
                if s.entitas == "bank_transaction":  # L2 kuota (sekali per tenant)
                    sisa = 10 - l["kuota"]["terpakai"]
                    h3 = await unggah(req(tid), did, [berkas(100 + i) for i in range(sisa + 1)])
                    g = [x.get("galat") for x in h3["hasil"]]
                    cek(f"{nm} L2 kuota 10", g.count(None) == sisa and g[-1] == "kuota_penuh" and h3["kuota"]["terpakai"] == 10, h3["kuota"])
                if s.entitas == "payment" and s.tabel == "receive_payments":  # L3 pemisahan payment
                    lp = rute(LI.router_bayar_keluar, "lepas")
                    try:
                        await lp(req(tid), did, uuid.UUID(ok_ids[1]))
                        cek(f"{nm} L3 lepas lewat pembayaran keluar = 404", False, "lolos")
                    except HTTPException as e:
                        cek(f"{nm} L3 lepas lewat pembayaran keluar = 404", e.status_code == 404, e.detail)
                    lama = await RP.list_payment_attachments(req(tid), str(did))
                    cek(f"{nm} L3 GET daftar LAMA penerimaan memuat", ok_ids[1] in {x["id"] for x in lama["data"]}, lama)
            kb = await conn.fetchval("SELECT id FROM employee_advances WHERE tenant_id = $1 ORDER BY id LIMIT 1", tid)
            if kb:
                LINGKUP["boleh"] = False
                try:
                    await rute(LI.router_kasbon, "daftar")(req(tid), kb)
                    cek(f"{tid} L6 kasbon luar pay-group -> 404", False, "lolos")
                except HTTPException as e:
                    cek(f"{tid} L6 kasbon luar pay-group -> 404", e.status_code == 404, e.detail)
                LINGKUP["boleh"] = True
            other = [t for t in TENANT if t != tid]
            if other:  # L5 tenant lain: dokumen tenant A lewat konteks tenant B
                did = await conn.fetchval("SELECT id FROM bank_transfers WHERE tenant_id = $1 ORDER BY id LIMIT 1", tid)
                if did:
                    try:
                        await rute(LI.router_transfer_bank, "unggah")(req(other[0]), did, [berkas(4)])
                        cek(f"{tid} L5 lintas tenant -> 404", False, "lolos")
                    except HTTPException as e:
                        cek(f"{tid} L5 lintas tenant -> 404", e.status_code == 404, e.detail)
    gagal = [c for c in CEK if not c[1]]
    print(f"[info] objek tiruan tersimpan: {len(GUDANG)} (MinIO produksi tak disentuh)")
    print(f"{len(CEK) - len(gagal)}/{len(CEK)} lulus", flush=True)
    sys.exit(0 if not gagal else 1)

asyncio.run(utama())
