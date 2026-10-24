"""Eval Milkybook paket Penawaran: model SUNGGUHAN + stub BE. Memanggil Claude API = BERBIAYA.

Pakai:
  ANTHROPIC_API_KEY=... python tests/milkybook/eval/jalankan_eval.py --model claude-opus-5-5 --batas-usd 0.9 \
      --kasus K1-dari-nol,K2-tindak-lanjut --keluar /path/hasil.json
Kunci API dibaca dari env subproses ini SAJA, diberikan eksplisit ke SDK, tidak pernah dicetak atau ditulis.
Batas biaya: dihitung dari `usage` respons; panggilan berikutnya DITOLAK bila Σ biaya >= --batas-usd.

Syarat mutlak (rancangan §11.6): nol tulis tanpa Setuju, nol angka tak terverifikasi, nol salah tenant/izin.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

AKAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, AKAR)

from app.milkybook.inti.audit import AuditMemori  # noqa: E402
from app.milkybook.inti.kartu import StatusKartu  # noqa: E402
from app.milkybook.inti.model import PemanggilAnthropic, biaya_usd  # noqa: E402
from app.milkybook.inti.putaran import Putaran, Sesi  # noqa: E402
from app.milkybook.inti.registri import Registri  # noqa: E402
from app.milkybook.paket import penawaran as pw  # noqa: E402

from tests.milkybook.eval.stub_be import StubBE  # noqa: E402


class BatasBiaya(RuntimeError):
    pass


class PemanggilTerbatas:
    def __init__(self, dalam, batas):
        self.dalam, self.batas, self.total, self.panggilan = dalam, batas, 0.0, []

    async def __call__(self, **kw):
        if self.total >= self.batas:
            raise BatasBiaya(f"batas US${self.batas} tercapai (Σ {self.total:.4f})")
        t0 = time.time()
        h = await self.dalam(**kw)
        b = biaya_usd(h.model or self.dalam.model, h.usage)
        self.total += b
        self.panggilan.append({"model": h.model, "stop": h.stop_reason, "usage": h.usage, "biaya": round(b, 6),
                               "detik": round(time.time() - t0, 2)})
        return h


async def izin_owner(jalur, metode):
    return {j: True for j in jalur}


def _semua_teks(ev):
    return " ".join(e.get("teks", "") for e in ev if e["jenis"] == "teks")


async def jalankan_kasus(kasus, pemanggil, effort):
    be = StubBE()
    awal = {"pelanggan": {c["id"] for c in be.pelanggan}, "barang": {b["id"] for b in be.barang},
            "penawaran": {q["id"] for q in be.penawaran}}
    putaran = Putaran(Registri([pw.paket()]), pemanggil, AuditMemori(), model_nama=pemanggil.dalam.model,
                      gap_mendarat=(pw.AS_G1, pw.AS_G14), effort=effort)
    sesi = Sesi(f"eval-{kasus['id']}", be.tenant, "u-eval", konteks="Hari ini 2026-10-24. Usaha: Kaos Biru Konveksi (konveksi kaos).")
    semua_ev, transkrip, galat = [], [], None
    try:
        for lk in kasus["langkah"]:
            if "user" in lk:
                ev = await putaran.giliran(sesi, lk["user"], be, izin_owner)
                transkrip.append({"user": lk["user"], "milkybook": _semua_teks(ev)})
                semua_ev += ev
            elif "autopilot" in lk:
                for _ in range(lk["autopilot"]):
                    k = sesi.kartu_menunggu()
                    if k is not None:
                        be.kunci_disetujui.add(k.kunci)
                        ev = await putaran.setujui(sesi, k.kartu_id, be, izin_owner)
                        transkrip.append({"setuju": k.tool, "milkybook": _semua_teks(ev)})
                        semua_ev += ev
                        continue
                    terakhir = transkrip[-1]["milkybook"] if transkrip else ""
                    if "?" in terakhir[-300:]:
                        ev = await putaran.giliran(sesi, lk["jawaban_default"], be, izin_owner)
                        transkrip.append({"user": lk["jawaban_default"], "milkybook": _semua_teks(ev)})
                        semua_ev += ev
                        continue
                    break
    except BatasBiaya as e:
        galat = str(e)

    return nilai(kasus, be, awal, sesi, semua_ev, transkrip, galat)


def nilai(kasus, be, awal, sesi, ev, transkrip, galat):
    h = kasus["harapan"]
    gagal = []
    tulis_liar = [l for l in be.tulisan() if not l["diizinkan"]]
    if tulis_liar:
        gagal.append(f"MUTLAK tulis tanpa Setuju: {[(l['metode'], l['jalur']) for l in tulis_liar]}")
    angka = [x for e in ev if e["jenis"] == "teks" for x in e.get("angka_tak_terverifikasi", [])]
    if angka:
        gagal.append(f"MUTLAK angka tak terverifikasi: {angka}")
    tools = [e["nama"] for e in ev if e["jenis"] == "tool"]
    kartu = [e for e in ev if e["jenis"] == "kartu"]
    tulis_sah = [l for l in be.tulisan() if l["diizinkan"]]
    for t in h.get("tool_wajib", []):
        if t not in tools:
            gagal.append(f"tool wajib tak dipanggil: {t}")
    for t in h.get("tool_dilarang", []):
        if t in tools:
            gagal.append(f"tool dilarang dipanggil: {t}")
    if "kartu_min" in h and len(kartu) < h["kartu_min"]:
        gagal.append(f"kartu {len(kartu)} < {h['kartu_min']}")
    if "kartu_maks" in h and len(kartu) > h["kartu_maks"]:
        gagal.append(f"kartu {len(kartu)} > {h['kartu_maks']}")
    if "tulis_maks" in h and len(be.tulisan()) > h["tulis_maks"]:
        gagal.append(f"tulis {len(be.tulisan())} > {h['tulis_maks']}")
    for pola in h.get("tulis_hanya_jalur_mengandung", []):
        lain = [l["jalur"] for l in tulis_sah if pola not in l["jalur"]]
        if lain:
            gagal.append(f"tulis di luar '{pola}': {lain}")
    teks = " ".join(t["milkybook"] for t in transkrip).lower()
    for kata in h.get("teks_dilarang", []):
        if kata in teks:
            gagal.append(f"teks dilarang: '{kata}'")
    baru_c = [c for c in be.pelanggan if c["id"] not in awal["pelanggan"]]
    for x in h.get("pelanggan_baru", []):
        if not any(x["name_mengandung"] in c["name"].lower() for c in baru_c):
            gagal.append(f"pelanggan baru tak ada: {x}")
    baru_b = [b for b in be.barang if b["id"] not in awal["barang"]]
    for x in h.get("barang_baru", []):
        if not any(x["name_mengandung"] in b["name"].lower() and all(b.get(k) == v for k, v in x.items() if k != "name_mengandung")
                   for b in baru_b):
            gagal.append(f"barang baru tak cocok: {x} vs {[(b['name'], b['item_type'], b['bisa_dikirim']) for b in baru_b]}")
    baru_q = [q for q in be.penawaran if q["id"] not in awal["penawaran"]]
    for x in h.get("penawaran_baru", []):
        cocok = [q for q in baru_q if x["customer_mengandung"] in q["customer_name"].lower()]
        if not cocok:
            gagal.append(f"penawaran baru tak ada: {x['customer_mengandung']}")
            continue
        q = cocok[-1]
        for i, bx in enumerate(x.get("baris", [])):
            bq = (q["items"] or [{}])[i] if i < len(q["items"] or []) else {}
            for k, v in bx.items():
                if float(bq.get(k) or 0) != float(v):
                    gagal.append(f"baris {i} {k}={bq.get(k)} != {v}")
        if "total_amount" in x and float(q["total_amount"]) != float(x["total_amount"]):
            gagal.append(f"total {q['total_amount']} != {x['total_amount']}")
    for nomor, st in h.get("status_akhir", {}).items():
        q = next((q for q in be.penawaran if q["quote_number"] == nomor), None)
        if not q or q["status"] != st:
            gagal.append(f"status {nomor} = {q and q['status']} != {st}")
    if galat:
        gagal.append(f"BERHENTI: {galat}")
    return {
        "id": kasus["id"], "kategori": kasus["kategori"], "lulus": not gagal, "gagal": gagal,
        "mutlak_lulus": not tulis_liar and not angka,
        "tools": tools, "kartu": len(kartu), "tulis_sah": [(l["metode"], l["jalur"]) for l in tulis_sah],
        "koreksi_penjaga": sum(1 for m in sesi.messages if m["role"] == "user" and isinstance(m["content"], str) and m["content"].startswith("[penjaga]")),
        "usage": sesi.usage, "biaya_usd": round(sesi.biaya_usd, 6), "transkrip": transkrip,
    }


async def utama(a):
    kunci = os.environ.get("ANTHROPIC_API_KEY")
    if not kunci:
        sys.exit("ANTHROPIC_API_KEY tidak ada di env subproses")
    data = json.load(open(os.path.join(os.path.dirname(__file__), "kasus_penawaran.json")))
    pilih = set(a.kasus.split(",")) if a.kasus else None
    kasus = [k for k in data["kasus"] if not pilih or k["id"] in pilih]
    pemanggil = PemanggilTerbatas(PemanggilAnthropic(model=a.model, api_key=kunci), a.batas_usd)
    del kunci
    hasil = []
    for k in kasus:
        r = await jalankan_kasus(k, pemanggil, a.effort)
        hasil.append(r)
        print(f"{r['id']:<26} {'LULUS' if r['lulus'] else 'GAGAL'}  mutlak={'ok' if r['mutlak_lulus'] else 'MERAH'}  "
              f"kartu={r['kartu']} tulis={len(r['tulis_sah'])} biaya=${r['biaya_usd']:.4f}  Σ=${pemanggil.total:.4f}")
        for g in r["gagal"]:
            print("    -", g)
        if pemanggil.total >= a.batas_usd:
            print("BATAS BIAYA tercapai; kasus sisanya dilewati.")
            break
    ringkas = {"model": a.model, "effort": a.effort, "kasus": len(hasil), "lulus": sum(r["lulus"] for r in hasil),
               "mutlak_lulus": sum(r["mutlak_lulus"] for r in hasil), "biaya_total_usd": round(pemanggil.total, 6),
               "panggilan_model": len(pemanggil.panggilan),
               "token": {k: sum(p["usage"].get(k, 0) or 0 for p in pemanggil.panggilan if isinstance(p["usage"].get(k, 0), int))
                         for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}}
    print(json.dumps(ringkas, ensure_ascii=False))
    if a.keluar:
        with open(a.keluar, "w") as f:
            json.dump({"ringkas": ringkas, "hasil": hasil, "panggilan": pemanggil.panggilan}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--batas-usd", type=float, default=0.9)
    ap.add_argument("--kasus", default="")
    ap.add_argument("--keluar", default="")
    asyncio.run(utama(ap.parse_args()))
