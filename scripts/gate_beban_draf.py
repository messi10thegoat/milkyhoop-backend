#!/usr/bin/env python3
"""Gerbang unit 'beban draf' — DUA SISI.

SISI=lama  (produksi)        : draf MUSTAHIL  -> [A] merah, [B] /post 404
SISI=baru  (kontainer kode baru): draf nyata, /post menerbitkan, penjaga hidup

BIAYA: sisi lama WAJIB menulis (draf mustahil -> beban terbit -> harus di-void
= 2 jurnal permanen). Sisi baru: draf DIHAPUS -> nol jejak; satu beban terbit
untuk menguji /post -> di-void = 2 jurnal. Total ~4, semuanya bersaldo nol.

Akun: MH_UJI_* (Collaborator __SYSTEM__, BUKAN Owner).
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASIS = os.environ.get("BASIS", "http://127.0.0.1:8001")
SISI = os.environ.get("SISI", "baru")
REK = os.environ["REK_ID"]
AKUN = os.environ["AKUN_BEBAN"]
UA = "milkyhoop-gate/1.0"
hasil = []


def panggil(metode, jalur, tok=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": UA}
    if data:
        h["Content-Type"] = "application/json"
    if tok:
        h["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(BASIS + jalur, data=data, headers=h, method=metode)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def masuk():
    _, d = panggil(
        "POST",
        "/api/auth/login",
        body={
            "email": os.environ["MH_UJI_EMAIL"],
            "password": os.environ["MH_UJI_SANDI"],
        },
    )
    return d.get("data", d)["access_token"]


def badan(status):
    return {
        "expense_date": "2026-09-12",
        "paid_through_id": REK,
        "account_id": AKUN,
        "amount": 1234,
        "is_itemized": False,
        "notes": "SENTINEL gerbang beban-draf (alat, bukan beban nyata)",
        "status": status,
    }


def catat(kode, label, ket=""):
    hasil.append((kode, label, ket))
    print("  [%s] %s %s" % (kode, label, ket))


def main():
    tok = masuk()

    if SISI == "lama":
        # [A] status=draft DIABAIKAN -> langsung terbit
        st, d = panggil("POST", "/api/expenses", tok, badan("draft"))
        if st != 201:
            print("GERBANG TAK SAH: create gagal (%s) %s" % (st, d))
            return 2
        row = d.get("data", {})
        eid = row.get("id")
        if row.get("status") == "posted" and row.get("journal_id"):
            catat("M", "A draf MUSTAHIL di kode lama", "status=posted + berjurnal")
        else:
            catat("!", "A premis keliru", str(row.get("status")))
        # bersihkan: WAJIB void (tak bisa dihapus) -> 2 jurnal permanen
        stv, _ = panggil(
            "POST", "/api/expenses/%s/void" % eid, tok, {"reason": "sentinel gerbang"}
        )
        print("     bersih-bersih void -> %s (2 jurnal permanen, net nol)" % stv)

        # [B] /post belum ada
        st2, _ = panggil("POST", "/api/expenses/%s/post" % eid, tok, {})
        catat("M" if st2 == 404 else "!", "B /post tak ada di kode lama", "HTTP %s" % st2)

    else:
        # [C] draf nyata: tanpa jurnal, tanpa transaksi bank
        st, d = panggil("POST", "/api/expenses", tok, badan("draft"))
        if st != 201:
            print("GERBANG TAK SAH: create draf gagal (%s) %s" % (st, d))
            return 2
        row = d.get("data", {})
        draf_id = row["id"]
        ok = (
            row.get("status") == "draft"
            and not row.get("journal_id")
            and row.get("operational_status") == "DRAFT"
            and row.get("accounting_status") == "UNPOSTED"
        )
        catat(
            "H" if ok else "X",
            "C draf lahir tanpa jurnal",
            "status=%s journal=%s op=%s acc=%s"
            % (
                row.get("status"),
                row.get("journal_id"),
                row.get("operational_status"),
                row.get("accounting_status"),
            ),
        )

        # [D] PATCH — endpoint yang BELUM PERNAH berhasil, kini hidup
        stp, dp = panggil(
            "PATCH", "/api/expenses/%s" % draf_id, tok, {"notes": "disunting gerbang"}
        )
        catat("H" if stp == 200 else "X", "D PATCH hidup untuk draf", "HTTP %s" % stp)

        # [E] /post menerbitkan
        ste, de = panggil("POST", "/api/expenses/%s/post" % draf_id, tok, {})
        pr = de.get("data", {}) if ste == 200 else {}
        oke = (
            ste == 200
            and pr.get("status") == "posted"
            and pr.get("journal_id")
            and pr.get("operational_status") == "PAID"
            and pr.get("accounting_status") == "POSTED"
        )
        catat(
            "H" if oke else "X",
            "E /post menerbitkan",
            "HTTP %s status=%s op=%s" % (ste, pr.get("status"), pr.get("operational_status")),
        )

        # [F] DELETE pada yang SUDAH terbit tetap ditolak
        stf, _ = panggil("DELETE", "/api/expenses/%s" % draf_id, tok)
        catat("H" if stf == 400 else "X", "F DELETE ditolak utk terbit", "HTTP %s" % stf)

        # [G] /post dua kali ditolak
        stg, _ = panggil("POST", "/api/expenses/%s/post" % draf_id, tok, {})
        catat("H" if stg == 400 else "X", "G /post kedua ditolak", "HTTP %s" % stg)

        # bersihkan yang terbit: void (2 jurnal permanen)
        stv, _ = panggil(
            "POST",
            "/api/expenses/%s/void" % draf_id,
            tok,
            {"reason": "sentinel gerbang"},
        )
        print("     bersih-bersih void -> %s" % stv)

        # [H] draf KEDUA: dihapus, NOL jejak
        st2, d2 = panggil("POST", "/api/expenses", tok, badan("draft"))
        id2 = d2.get("data", {}).get("id")
        std, _ = panggil("DELETE", "/api/expenses/%s" % id2, tok)
        catat(
            "H" if std in (200, 204) else "X",
            "H draf bisa DIHAPUS (nol jejak)",
            "HTTP %s" % std,
        )

        # [I] void pada draf ditolak
        st3, d3 = panggil("POST", "/api/expenses", tok, badan("draft"))
        id3 = d3.get("data", {}).get("id")
        sti, _ = panggil(
            "POST", "/api/expenses/%s/void" % id3, tok, {"reason": "harus ditolak"}
        )
        catat("H" if sti == 400 else "X", "I void draf ditolak", "HTTP %s" % sti)
        panggil("DELETE", "/api/expenses/%s" % id3, tok)

    buruk = [h for h in hasil if h[0] in ("X", "!")]
    print("\n%s: %d/%d" % (SISI.upper(), len(hasil) - len(buruk), len(hasil)))
    return 1 if buruk else 0


if __name__ == "__main__":
    sys.exit(main())
