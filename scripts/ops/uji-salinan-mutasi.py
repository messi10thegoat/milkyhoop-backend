"""MUTASI kontak -- uji di SALINAN (klon milkydb): untuk SETIAP pelanggan + vendor kaos/nyawit, handler ASLI GET /{id}/mutasi:
 P1 paritas: saldo_akhir (tanpa dari/sampai) == saldo detail kontak (kontak_daftar.fakta_kontak, penentu daftar/detail) -- tanpa_batal true DAN false
 P2 batal berpasangan neto nol: Σ baris batal == 0; baris hidup tanpa_batal=false == baris tanpa_batal=true
 P3 saldo berjalan: baris terakhir.saldo == saldo_akhir; saldo_awal + Σ tanda*(debit-kredit) == saldo_akhir (dengan `dari` tengah)
 P4 halaman: gabungan halaman limit 7 == satu halaman limit 200
 P5 (temuan B TESTER 9 Nov) urutan dalam tanggal kronologis: contoh nyawit -- dokumen sumber SEBELUM pelunasannya dan saldo berjalan
    kontak itu tidak pernah negatif (PASANGAN; nomor = awalan + akhiran angka). Semua pasangan wajib ditemukan.
Baca saja (salinan dihapus trap)."""
import asyncio
import json
import os
import sys
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import customers as cu, vendors as ve  # noqa: E402
from app.services import kontak_daftar as KD, kontak_mutasi as KM  # noqa: E402
from app.utils.tanggal_tenant import tanggal_dokumen  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
hasil, n = [], {"kontak": 0, "baris": 0, "batal": 0}
PASANGAN = {"pt-kita-nyawit-bersama": [(("BILL", "0003"), ("BP", "0003")), (("BILL", "0016"), ("BP", "0013")),
                                        (("INV", "0005"), ("CN", "0001")), (("INV", "0014"), ("DEP", "0003"))]}
temu = []


def cocok(nomor, kunci):
    return bool(nomor) and nomor.startswith(kunci[0]) and nomor.endswith("-" + kunci[1])


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "username": "uji-salinan-mutasi"}
    return r


def cek(nama, ok, info=""):
    hasil.append(bool(ok))
    if not ok:
        print(f"FAIL {nama} -- {info}", flush=True)


D = lambda v: Decimal(str(v))  # noqa: E731


async def _boleh(*a, **k):
    return True


async def utama():
    import app.services.dashboard_izin as DI
    DI.boleh_baca = _boleh  # P5 membaca nomor dokumen: PolicyEngine tak hidup di kontainer harness (nomor = null bila tak boleh); angka tak terpengaruh
    pool = await cu.get_pool()
    async with pool.acquire() as conn:
        db = await conn.fetchval("SELECT current_database()")
        if not db.startswith("scratch_mutasi_"):
            sys.exit(f"BUKAN salinan: {db}")
        for tid in TENANT:
            hari = await tanggal_dokumen(conn, tid)
            for jenis, mod, tabel, kol in (("customer", cu, "customers", "nama"), ("vendor", ve, "vendors", "name")):
                rute = mod.get_customer_mutasi if jenis == "customer" else mod.get_vendor_mutasi
                pihak = await conn.fetch(f"SELECT id, {kol} AS nama FROM {tabel} WHERE tenant_id = $1 ORDER BY {kol}, id", tid)
                fakta = await KD.fakta_kontak(conn, tid, jenis, [str(p["id"]) for p in pihak], hari)
                for p in pihak:
                    nm = f"{tid} {jenis} {p['nama']}"
                    saldo = fakta.get(str(p["id"]), KD.fakta_kosong())["saldo"]
                    a = (await rute(req(tid), p["id"], dari=None, sampai=None, tanpa_batal=True, skip=0, limit=200))["data"]
                    b = (await rute(req(tid), p["id"], dari=None, sampai=None, tanpa_batal=False, skip=0, limit=200))["data"]
                    n["kontak"] += 1
                    n["baris"] += a["total"]
                    n["batal"] += sum(x["batal"] for x in b["baris"])
                    cek(f"{nm} P1 saldo_akhir == saldo", D(a["saldo_akhir"]) == saldo, f"{a['saldo_akhir']} vs {saldo}")
                    cek(f"{nm} P1 tanpa_batal=false", D(b["saldo_akhir"]) == saldo, f"{b['saldo_akhir']} vs {saldo}")
                    tanda = KM.JENIS[jenis]["tanda"]
                    bt = sum((tanda * (D(x["debit"]) - D(x["kredit"])) for x in b["baris"] if x["batal"]), Decimal(0))
                    cek(f"{nm} P2 batal neto nol", bt == 0, str(bt))
                    cek(f"{nm} P2 hidup sama", [x for x in b["baris"] if not x["batal"]] == [x | {"saldo": x["saldo"]} for x in a["baris"]]
                        or [x["id"] for x in b["baris"] if not x["batal"]] == [x["id"] for x in a["baris"]], "")
                    nomor = [x["dokumen"]["nomor"] for x in a["baris"]]
                    for sumber, lunas in PASANGAN.get(tid, []):
                        i = next((k for k, x in enumerate(nomor) if cocok(x, sumber)), None)
                        j = next((k for k, x in enumerate(nomor) if cocok(x, lunas)), None)
                        if i is not None and j is not None:
                            temu.append((sumber, lunas))
                            cek(f"{nm} P5 {sumber} sebelum {lunas}", i < j, f"{i} vs {j}: {nomor}")
                            cek(f"{nm} P5 saldo berjalan tak negatif", min(D(x["saldo"]) for x in a["baris"]) >= 0,
                                [(x["dokumen"]["nomor"], x["saldo"]) for x in a["baris"]])
                    if a["baris"]:
                        cek(f"{nm} P3 baris akhir == saldo_akhir", D(a["baris"][-1]["saldo"]) == D(a["saldo_akhir"]))
                        tgl = sorted({x["tanggal"] for x in a["baris"]})
                        from datetime import date
                        dari = date.fromisoformat(tgl[len(tgl) // 2])
                        c = (await rute(req(tid), p["id"], dari=dari, sampai=None, tanpa_batal=True, skip=0, limit=200))["data"]
                        s = D(c["saldo_awal"]) + sum((tanda * (D(x["debit"]) - D(x["kredit"])) for x in c["baris"]), Decimal(0))
                        cek(f"{nm} P3 saldo_awal + Σ == saldo_akhir (dari {dari})", s == D(c["saldo_akhir"]) == D(a["saldo_akhir"]), f"{s}")
                        hal, k = [], 0
                        while True:
                            h = (await rute(req(tid), p["id"], dari=None, sampai=None, tanpa_batal=True, skip=k, limit=7))["data"]
                            hal += h["baris"]
                            k += 7
                            if k >= h["total"]:
                                break
                        cek(f"{nm} P4 halaman", hal == a["baris"], f"{len(hal)} vs {len(a['baris'])}")
    for tid, ps in PASANGAN.items():
        if tid in TENANT:
            cek(f"{tid} P5 semua pasangan ditemukan", all(p in temu for p in ps), f"temu {temu}")
    print(f"[info] {n} P5 pasangan {len(temu)}", flush=True)
    print(f"{sum(hasil)}/{len(hasil)} lulus", flush=True)
    sys.exit(0 if all(hasil) else 1)

asyncio.run(utama())
