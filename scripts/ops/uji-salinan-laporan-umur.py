"""Gelombang 4 L2 -- uji di SALINAN: SATU dump, DUA klon, handler ASLI pohon LAMA dan BARU: GET /api/reports/aging-receivable dan
/aging-payable untuk kaos + nyawit. Baca saja. Medan lama lama == baru PERSIS; baru: Σ ember == total_outstanding, ember == summary,
jatuh tempo == total − belum, per_tanggal == tanggal bisnis tenant, nomor_text/telat_text konsisten, persen ≈ 100 (bila total > 0)."""
import asyncio
import json
import os
import sys
from decimal import Decimal

from starlette.requests import Request

sys.path.insert(0, "/app/backend/api_gateway")
from app.routers import reports as RP  # noqa: E402

TENANT = tuple(os.environ.get("TENANT", "kaos-biru-konveksi,pt-kita-nyawit-bersama").split(","))
BARU = True
CEK = []
TAMBAHAN = {"per_tanggal", "per_text", "ember", "belum_jatuh_tempo_total", "jatuh_tempo_total", "jumlah_pihak", "jumlah_pihak_text"}


def cek(nama, ok, info=""):
    CEK.append([nama, bool(ok), "" if ok else str(info)[:500]])


def req(tid):
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    r.state.user = {"tenant_id": tid, "user_id": "00000000-0000-0000-0000-000000000001", "role": "OWNER"}
    return r


def j(v):
    return json.loads(json.dumps(v, default=str))


def lama_saja(h, kp, kd):
    h = {k: v for k, v in h.items() if k not in TAMBAHAN}
    h[kp] = [{k: v for k, v in p.items() if k != "jatuh_tempo_total"} | {kd: [{k: v for k, v in d.items() if k not in
             ("nomor_text", "jatuh_tempo_text", "telat_text")} for d in p[kd]]} for p in h[kp]]
    return h


async def main():
    from app.utils.tanggal_tenant import tanggal_dokumen
    pool = await RP.get_pool()
    out = {"lama": {}}
    baru = None
    for tid in TENANT:
        for nama, f, kp, kd, kn in (("piutang", RP.get_aging_receivable, "customers", "invoices", "invoice_number"),
                                    ("hutang", RP.get_aging_payable, "vendors", "bills", "bill_number")):
            h = j(await f(req(tid)))
            baru = "ember" in h
            out["lama"][f"{tid}|{nama}"] = lama_saja(h, kp, kd)
            if not baru:
                continue
            k = f"{tid}|{nama}"
            async with pool.acquire() as conn:
                hari = await tanggal_dokumen(conn, tid)
            tot = Decimal(str(h["total_outstanding"]))
            em = {e["kunci"]: Decimal(e["total"]) for e in h["ember"]}
            cek(f"{k} Σ ember == total_outstanding", sum(em.values()) == tot.quantize(Decimal("0.01")), (em, tot))
            cek(f"{k} ember == summary", all(em[x] == Decimal(str(h["summary"][x])).quantize(Decimal("0.01")) for x in em), (em, h["summary"]))
            cek(f"{k} jatuh tempo == total − belum", Decimal(h["jatuh_tempo_total"]) == (tot - Decimal(str(h["summary"]["current"]))).quantize(
                Decimal("0.01")), h["jatuh_tempo_total"])
            cek(f"{k} per_tanggal == tanggal bisnis tenant", h["per_tanggal"] == hari.isoformat(), (h["per_tanggal"], hari))
            if tot > 0:
                cek(f"{k} Σ persen ≈ 100", abs(sum(Decimal(e["persen"]) for e in h["ember"]) - 100) <= Decimal("0.5"), h["ember"])
            cek(f"{k} jumlah pihak", h["jumlah_pihak"] == len(h[kp]), h["jumlah_pihak"])
            for p in h[kp]:
                cek(f"{k} pihak jatuh tempo", Decimal(p["jatuh_tempo_total"]) == (Decimal(str(p["total"])) - Decimal(str(p["totals"]["current"])))
                    .quantize(Decimal("0.01")), p)
                for d in p[kd]:
                    cek(f"{k} nomor_text", d["nomor_text"] == (d[kn] or "Kredit tanpa dokumen"), d)
                    cek(f"{k} telat_text", (d["telat_text"] is not None) == ((d["days_overdue"] or 0) > 0), d)
    out["pohon"] = "baru" if baru else "lama"
    out["cek"] = CEK
    with open(os.environ["KELUAR"], "w") as fo:
        json.dump(out, fo, default=str)
    gagal = [c for c in CEK if not c[1]]
    for c in gagal[:30]:
        print("[GAGAL]", c[0], c[2], flush=True)
    print(f"[{out['pohon']}] cek {len(CEK) - len(gagal)}/{len(CEK)} lulus", flush=True)


asyncio.run(main())
