"""Dashboard v2 (D3, 28 Sep 2026) — ringkasan + tugas "Perlu dikerjakan".

Spek: docs/dashboard-sidebar/03-DASHBOARD.md + 04-DATA-DAN-API.md (+ D0-AUDIT, putusan pemilik).
Kontrak: contracts/dashboard-summary.schema.json, contracts/dashboard-tasks.schema.json.

SATU SUMBER KEBENARAN (Law 1/16, syarat D0):
  - Laba/pendapatan/beban + "Ke mana uang pergi" = report_engine.ledger.compute_balance/_detail dengan
    filter YANG SAMA dengan report_engine.income_statement (laporan Laba Rugi PSAK), basis akrual.
    BUKAN get_*_by_basis (dashboard lama: tak menghitung OTHER_INCOME).
  - Uang tersedia = compute_balance({"is_cash": True}, end_date=hari ini) — fungsi YANG SAMA dengan
    kas & setara kas Neraca. Kas/Bank dipecah dari bank_accounts.account_type (via coa_id), BUKAN
    chart_of_accounts.category (D0: "Toko Manado" category NULL -> salah masuk bank).
  - Piutang/hutang = compute_ar_outstanding / compute_ap_outstanding (sama dengan Umur Piutang/Hutang),
    ember "belum jatuh tempo" = definisi 'current' laporan umur (hari ini <= due_date).
  - Arus kas masa lalu = neto kas PER JURNAL (definisi report_engine.cash_flow): transfer antar-akun
    kas/bank sendiri bernilai neto 0 -> otomatis tak dihitung. masuk - keluar = perubahan kas Neraca.
  - Perkiraan = sisa compute_ar/ap_outstanding menurut due_date + rata-rata beban kas mingguan.
"Hari ini" = tanggal bisnis tenant (tanggal_dokumen; putusan pemilik: WIB dulu = nilai "Tenant".timezone).
Uang = Decimal, dikirim sebagai string "123.45" (Law 9/25). Nol tulis ke buku.
"""
from __future__ import annotations

import calendar
import hashlib
import html
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, List, Optional

from .report_engine.ledger import compute_balance, compute_balance_detail
from . import so_kirim

NOL = Decimal("0")
SEN = Decimal("0.01")
SERATUS_RIBU = Decimal("100000")

PERIODE = ("day", "week", "month", "year")
LABEL_PERIODE = {"day": "Hari ini", "week": "Minggu ini", "month": "Bulan ini", "year": "Tahun ini"}
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
BULAN_PANJANG = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
                 "September", "Oktober", "November", "Desember"]
HARI = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
HARI_PANJANG = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

# Jenis beban di Laba Rugi (income_statement: hpp + beban_usaha + beban_lain).
TIPE_BEBAN = ("COGS", "EXPENSE", "OTHER_EXPENSE")
# bank_accounts.account_type yang berarti KAS (lainnya = bank / e-wallet / kartu).
TIPE_KAS = ("cash", "petty_cash", "kas")
MAKS_KATEGORI = 6
LAJUR_URUT = {"now": 0, "week": 1, "any": 2}


# ─────────────────────────── utilitas murni ───────────────────────────

def d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def uang(v) -> str:
    return str(d(v).quantize(SEN, rounding=ROUND_HALF_UP))


def rp(v) -> str:
    """Rp 1.234.567 (sen hanya bila ada) — untuk teks pesan WA, bukan angka kontrak."""
    x = d(v).quantize(SEN, rounding=ROUND_HALF_UP)
    bulat = x == x.to_integral_value()
    s = f"{abs(x):,.0f}" if bulat else f"{abs(x):,.2f}"
    s = s.replace(",", "#").replace(".", ",").replace("#", ".")
    return ("-" if x < 0 else "") + "Rp " + s


def per_100rb(bagian: Decimal, dasar: Decimal) -> int:
    if dasar <= NOL:
        return 0
    return int((bagian * SERATUS_RIBU / dasar).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def persen(bagian: Decimal, dasar: Decimal) -> float:
    if dasar == NOL:
        return 0.0
    return float((bagian * 100 / dasar).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def rentang_periode(periode: str, hari_ini: date) -> tuple:
    """Periode kalender PENUH (sama dengan Laba Rugi periode itu). Minggu = Senin–Minggu."""
    if periode == "day":
        return hari_ini, hari_ini
    if periode == "week":
        senin = hari_ini - timedelta(days=hari_ini.weekday())
        return senin, senin + timedelta(days=6)
    if periode == "month":
        akhir = calendar.monthrange(hari_ini.year, hari_ini.month)[1]
        return date(hari_ini.year, hari_ini.month, 1), date(hari_ini.year, hari_ini.month, akhir)
    if periode == "year":
        return date(hari_ini.year, 1, 1), date(hari_ini.year, 12, 31)
    raise ValueError(f"periode tak dikenal: {periode}")


def normalisasi_wa(*kandidat) -> Optional[str]:
    """Nomor pertama yang terisi -> format 62xxxxxxxxx (tanpa +). Tak sah -> None."""
    for k in kandidat:
        if not k or not str(k).strip():
            continue
        digit = re.sub(r"\D", "", str(k))
        if digit.startswith("0"):
            digit = "62" + digit[1:]
        elif digit.startswith("8"):
            digit = "62" + digit
        if digit.startswith("62") and 10 <= len(digit) <= 15:
            return digit
        return None  # nomor pertama yang terisi menang; bila rusak, jujur None (bukan loncat ke nomor lain)
    return None


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def _sidik(*bagian) -> str:
    return hashlib.sha1("|".join(str(b) for b in bagian).encode()).hexdigest()[:10]


def _tgl_pendek(t: date) -> str:
    return f"{t.day} {BULAN[t.month - 1]}"


def _nama_hari(t: date, hari_ini: date) -> str:
    if t == hari_ini:
        return "hari ini"
    if t == hari_ini + timedelta(days=1):
        return "besok"
    return f"{HARI_PANJANG[t.weekday()]}, {_tgl_pendek(t)}"


# ─────────────────────────── ringkasan (summary) ───────────────────────────

async def kas_bank(conn, tenant_id: str, hari_ini: date) -> dict:
    total = await compute_balance(conn, tenant_id, {"is_cash": True}, end_date=hari_ini)
    rinci = await compute_balance_detail(conn, tenant_id, {"is_cash": True}, end_date=hari_ini)
    meta = await conn.fetch(
        """SELECT DISTINCT ON (c.account_code) c.id, c.account_code, c.category, ba.account_type AS jenis_rek
           FROM chart_of_accounts c
           LEFT JOIN bank_accounts ba ON ba.coa_id = c.id AND ba.tenant_id = c.tenant_id
           WHERE c.tenant_id = $1 AND c.is_cash = true
           ORDER BY c.account_code, ba.is_active DESC NULLS LAST""",
        tenant_id,
    )
    per_kode = {m["account_code"]: m for m in meta}
    akun, bank_total, cash_total = [], NOL, NOL
    for r in rinci:
        m = per_kode.get(r["account_code"])
        jenis_rek = (m["jenis_rek"] if m else None) or ""
        if jenis_rek:
            jenis = "cash" if jenis_rek.lower() in TIPE_KAS else "bank"
        else:  # akun kas tanpa rekening tertaut: kategori CoA sebagai cadangan
            jenis = "cash" if ((m["category"] if m else "") or "").lower() in TIPE_KAS else "bank"
        saldo = d(r["balance"])
        if jenis == "cash":
            cash_total += saldo
        else:
            bank_total += saldo
        akun.append({"id": str(m["id"]) if m else r["account_code"], "name": r["account_name"],
                     "type": jenis, "balance": uang(saldo)})
    # Σ rinci == total (fungsi sama, filter sama); bila tidak, lebih baik gagal keras daripada angka palsu.
    if (bank_total + cash_total).quantize(SEN) != d(total).quantize(SEN):
        raise RuntimeError("kas_bank: rincian akun != total Neraca")
    return {"total": uang(total), "bank_total": uang(bank_total), "cash_total": uang(cash_total),
            "accounts": akun}


def _jatuh_tempo_lewat(due: Optional[date], hari_ini: date) -> bool:
    # Sama dengan ember laporan Umur Piutang: bukan 'current' (hari_ini <= due) -> telat.
    # due NULL: laporan jatuh ke ELSE '90+' -> telat juga.
    return due is None or hari_ini > due


async def piutang(conn, tenant_id: str, hari_ini: date, baris=None) -> dict:
    rows = baris if baris is not None else await conn.fetch(
        "SELECT * FROM compute_ar_outstanding($1)", tenant_id)
    total, belum, telat, n_telat, pelanggan, due7 = NOL, NOL, NOL, 0, set(), []
    for r in rows:
        sisa = d(r["outstanding"])
        total += sisa
        if _jatuh_tempo_lewat(r["due_date"], hari_ini):
            telat += sisa
            if sisa > NOL:
                n_telat += 1
        else:
            belum += sisa
        if sisa > NOL:
            pelanggan.add(str(r["customer_id"]))
            if r["due_date"] is None or r["due_date"] <= hari_ini + timedelta(days=7):
                due7.append(r)
    due7.sort(key=lambda r: (r["due_date"] or date.min, r["invoice_number"] or ""))
    return {
        "total": uang(total), "not_due": uang(belum), "overdue_total": uang(telat),
        "overdue_count": n_telat, "customer_count": len(pelanggan),
        "due_7d": [{"invoice_id": str(r["invoice_id"]), "number": r["invoice_number"],
                    "customer": r["customer_name"],
                    "due_date": r["due_date"].isoformat() if r["due_date"] else None,
                    "balance": uang(r["outstanding"])} for r in due7[:50]],
    }


async def hutang(conn, tenant_id: str, hari_ini: date, baris=None) -> dict:
    rows = baris if baris is not None else await conn.fetch(
        "SELECT * FROM compute_ap_outstanding($1)", tenant_id)
    total = sum((d(r["outstanding"]) for r in rows), NOL)
    telat = sum((d(r["outstanding"]) for r in rows if _jatuh_tempo_lewat(r["due_date"], hari_ini)), NOL)
    return {"total": uang(total), "overdue_total": uang(telat)}


async def laba_rugi_dan_beban(conn, tenant_id: str, mulai: date, akhir: date) -> tuple:
    """Rumus = report_engine.income_statement (akrual): laba_bersih = REV − COGS − EXP + OI − OE.
    revenue = pendapatan + pendapatan lain; expense = hpp + beban usaha + beban lain."""
    async def b(tipe):
        return await compute_balance(conn, tenant_id, {"account_type": tipe}, start_date=mulai, end_date=akhir)

    pendapatan = -(await b("REVENUE")) - (await b("OTHER_INCOME"))
    rinci = []
    for tipe in TIPE_BEBAN:
        rinci += await compute_balance_detail(conn, tenant_id, {"account_type": tipe},
                                              start_date=mulai, end_date=akhir)
    beban = sum((d(r["balance"]) for r in rinci), NOL)
    laba = pendapatan - beban
    pnl = {"revenue": uang(pendapatan), "expense": uang(beban), "profit": uang(laba),
           "margin_pct": persen(laba, pendapatan) if pendapatan > NOL else 0.0}

    kode = [r["account_code"] for r in rinci]
    ids = {}
    if kode:
        for m in await conn.fetch(
                "SELECT id, account_code FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = ANY($2::text[])",
                tenant_id, kode):
            ids[m["account_code"]] = str(m["id"])
    urut = sorted(rinci, key=lambda r: (-d(r["balance"]), r["account_code"]))
    teratas = [r for r in urut if d(r["balance"]) > NOL][:MAKS_KATEGORI]
    sisa = [r for r in urut if r not in teratas]
    kategori = [{"account_id": ids.get(r["account_code"]), "name": r["account_name"], "amount": d(r["balance"])}
                for r in teratas]
    lainnya = sum((d(r["balance"]) for r in sisa), NOL)
    if sisa and lainnya != NOL:
        kategori.append({"account_id": None, "name": "Lainnya", "amount": lainnya})
    beban_100 = per_100rb(beban, pendapatan)
    expenses = {
        "total": uang(beban),
        "per_100k_expense": beban_100,
        "per_100k_profit": (100000 - beban_100) if pendapatan > NOL else 0,
        "categories": [{"account_id": k["account_id"], "name": k["name"], "amount": uang(k["amount"]),
                        "per_100k": per_100rb(k["amount"], pendapatan),
                        "pct_of_expense": persen(k["amount"], beban)} for k in kategori],
    }
    return pnl, expenses


# ── arus kas ──

SQL_KAS_PER_HARI = """
    WITH neto AS (
        SELECT je.journal_date AS tgl, SUM(jl.debit) - SUM(jl.credit) AS neto
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = $1 AND je.status = 'POSTED'
          AND je.journal_date >= $2 AND je.journal_date <= $3
          AND coa.is_cash = TRUE
        GROUP BY je.id, je.journal_date
    )
    SELECT tgl,
           COALESCE(SUM(neto) FILTER (WHERE neto > 0), 0) AS masuk,
           COALESCE(-SUM(neto) FILTER (WHERE neto < 0), 0) AS keluar
    FROM neto GROUP BY tgl
"""

SQL_BEBAN_KAS_28H = """
    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = $1 AND je.status = 'POSTED'
      AND je.journal_date > $2::date - 28 AND je.journal_date <= $2::date
      AND coa.account_type = ANY($3::text[])
      AND EXISTS (SELECT 1 FROM journal_lines k JOIN chart_of_accounts kc ON kc.id = k.account_id
                  WHERE k.journal_id = je.id AND kc.is_cash = TRUE)
"""


def _ember_minggu_bulan(awal: date, akhir: date) -> list:
    """Bulan dipotong 1–7, 8–14, 15–21, 22–28, 29–akhir."""
    out, t = [], awal
    while t <= akhir:
        u = min(t + timedelta(days=6), akhir)
        if t.day >= 29:
            u = akhir
        out.append((t, u))
        t = u + timedelta(days=1)
    return out


def ember_masa_lalu(periode: str, hari_ini: date, data_pertama: Optional[date]) -> tuple:
    """-> (unit, [(label, mulai, akhir)]) — hanya sampai hari ini (masa depan = perkiraan)."""
    if periode == "day":
        hari = [hari_ini - timedelta(days=i) for i in range(6, -1, -1)]
        return "day", [(f"{HARI[h.weekday()]} {h.day}", h, h) for h in hari]
    if periode == "week":
        senin = hari_ini - timedelta(days=hari_ini.weekday())
        hari = [senin + timedelta(days=i) for i in range((hari_ini - senin).days + 1)]
        return "day", [(f"{HARI[h.weekday()]} {h.day}", h, h) for h in hari]
    if periode == "month":
        awal = date(hari_ini.year, hari_ini.month, 1)
        akhir = date(hari_ini.year, hari_ini.month, calendar.monthrange(hari_ini.year, hari_ini.month)[1])
        return "week", [(f"{a.day}–{min(b, hari_ini).day} {BULAN[a.month - 1]}", a, min(b, hari_ini))
                        for a, b in _ember_minggu_bulan(awal, akhir) if a <= hari_ini]
    # year
    awal_tahun = date(hari_ini.year, 1, 1)
    batas3 = hari_ini - timedelta(days=91)
    if data_pertama is not None and data_pertama > batas3:
        # data < 3 bulan -> per minggu (Senin–Minggu) sejak data pertama; tanpa Jan–Des kosong
        t = max(data_pertama, awal_tahun)
        t = t - timedelta(days=t.weekday())
        out = []
        while t <= hari_ini:
            u = min(t + timedelta(days=6), hari_ini)
            out.append((f"{_tgl_pendek(t)}", t, u))
            t = t + timedelta(days=7)
        return "week", out
    out = []
    for m in range(1, hari_ini.month + 1):
        a = date(hari_ini.year, m, 1)
        b = date(hari_ini.year, m, calendar.monthrange(hari_ini.year, m)[1])
        out.append((BULAN[m - 1], a, min(b, hari_ini)))
    return "month", out


def ember_perkiraan(unit: str, hari_ini: date) -> list:
    """4 ember sesudah hari ini, satuan sama dengan masa lalu."""
    out, t = [], hari_ini + timedelta(days=1)
    for _ in range(4):
        if unit == "day":
            u = t
            label = f"{HARI[t.weekday()]} {t.day}"
        elif unit == "week":
            u = t + timedelta(days=6)
            label = f"{_tgl_pendek(t)}–{_tgl_pendek(u)}"
        else:
            u = date(t.year, t.month, calendar.monthrange(t.year, t.month)[1])
            label = BULAN[t.month - 1]
        out.append((label, t, u))
        t = u + timedelta(days=1)
    return out


def _masukkan_ember(ember: list, tanggal: Optional[date], nilai: Decimal, kunci: str):
    """Jatuh tempo lewat/NULL -> ember pertama; di luar jangkauan -> diabaikan."""
    if tanggal is None or tanggal < ember[0]["_mulai"]:
        ember[0][kunci] += nilai
        return
    for e in ember:
        if e["_mulai"] <= tanggal <= e["_akhir"]:
            e[kunci] += nilai
            return


async def arus_kas(conn, tenant_id: str, periode: str, hari_ini: date, ar_rows, ap_rows) -> dict:
    data_pertama = await conn.fetchval(
        "SELECT MIN(journal_date) FROM journal_entries WHERE tenant_id = $1 AND status = 'POSTED'", tenant_id)
    unit, lalu = ember_masa_lalu(periode, hari_ini, data_pertama)
    per_hari = {}
    if lalu:
        for r in await conn.fetch(SQL_KAS_PER_HARI, tenant_id, lalu[0][1], lalu[-1][2]):
            per_hari[r["tgl"]] = (d(r["masuk"]), d(r["keluar"]))
    ember, total_in, total_out = [], NOL, NOL
    for label, a, b in lalu:
        masuk = sum((v[0] for t, v in per_hari.items() if a <= t <= b), NOL)
        keluar = sum((v[1] for t, v in per_hari.items() if a <= t <= b), NOL)
        total_in += masuk
        total_out += keluar
        ember.append({"label": label, "start": a.isoformat(), "end": b.isoformat(),
                      "in": masuk, "out": keluar, "is_projection": False})
    puncak = max((e for e in ember if e["in"] > NOL), key=lambda e: e["in"], default=None)

    depan = [{"label": l, "_mulai": a, "_akhir": b, "in": NOL, "out": NOL}
             for l, a, b in ember_perkiraan(unit, hari_ini)]
    for r in ar_rows:
        if d(r["outstanding"]) > NOL:
            _masukkan_ember(depan, r["due_date"], d(r["outstanding"]), "in")
    for r in ap_rows:
        if d(r["outstanding"]) > NOL:
            _masukkan_ember(depan, r["due_date"], d(r["outstanding"]), "out")
    beban28 = d(await conn.fetchval(SQL_BEBAN_KAS_28H, tenant_id, hari_ini, list(TIPE_BEBAN)))
    per_hari_beban = beban28 / 28 if beban28 > NOL else NOL
    for e in depan:
        e["out"] += per_hari_beban * ((e["_akhir"] - e["_mulai"]).days + 1)
    proj_net = sum((e["in"] - e["out"] for e in depan), NOL)
    for e in depan:
        ember.append({"label": e["label"], "start": e["_mulai"].isoformat(), "end": e["_akhir"].isoformat(),
                      "in": e["in"], "out": e["out"], "is_projection": True})
    satuan = {"day": "hari", "week": "minggu", "month": "bulan"}[unit]
    return {
        "unit": unit,
        "buckets": [{**e, "in": uang(e["in"]), "out": uang(e["out"])} for e in ember],
        "total_in": uang(total_in), "total_out": uang(total_out), "projection_net": uang(proj_net),
        "peak_label": puncak["label"] if puncak else None,
        "note": (f"Per {satuan}; perkiraan {4} {satuan} ke depan = sisa piutang & hutang menurut jatuh tempo "
                 f"+ rata-rata beban kas 4 minggu terakhir."),
    }


async def ringkasan(conn, tenant_id: str, periode: str, hari_ini: date, tz_nama: str, as_of: str) -> dict:
    ar_rows = await conn.fetch("SELECT * FROM compute_ar_outstanding($1)", tenant_id)
    ap_rows = await conn.fetch("SELECT * FROM compute_ap_outstanding($1)", tenant_id)
    mulai, akhir = rentang_periode(periode, hari_ini)
    pnl, expenses = await laba_rugi_dan_beban(conn, tenant_id, mulai, akhir)
    return {
        "as_of": as_of,
        "period": periode,
        "period_label": LABEL_PERIODE[periode],
        "period_start": mulai.isoformat(),
        "period_end": akhir.isoformat(),
        "tenant_tz": tz_nama,
        "cash": await kas_bank(conn, tenant_id, hari_ini),
        "receivables": await piutang(conn, tenant_id, hari_ini, ar_rows),
        "payables": await hutang(conn, tenant_id, hari_ini, ap_rows),
        "pnl": pnl,
        "cashflow": await arus_kas(conn, tenant_id, periode, hari_ini, ar_rows, ap_rows),
        "expenses": expenses,
    }


# Bagian ringkasan -> modul yang SEMUANYA wajib dibaca (pola dashboard_izin; gagal tertutup).
BAGIAN_MODUL = {
    "cash": ("kas_bank",),
    "receivables": ("sales_invoice",),
    "payables": ("purchase_invoice",),
    "pnl": ("report",),
    "expenses": ("report",),
    # perkiraan memakai piutang + hutang -> ketiganya (sama dengan /cash-flow-projection lama)
    "cashflow": ("kas_bank", "sales_invoice", "purchase_invoice"),
}


# ─────────────────────────── tugas (tasks) ───────────────────────────

TUGAS_MODUL = {
    "ar_due_today": ("sales_invoice",), "ar_overdue": ("sales_invoice",), "ar_due_soon": ("sales_invoice",),
    "ap_due": ("purchase_invoice",), "so_to_ship": ("sales_order",), "so_dp_pending": ("sales_order",),
    "bank_recon": ("kas_bank",),
}
JENIS_TUGAS = tuple(TUGAS_MODUL)
POLA_KUNCI = re.compile(r"^(%s):[^\s]{1,280}$" % "|".join(JENIS_TUGAS))


def _pesan_wa(nama, nomor_faktur, sisa, due, hari_ini, rekening, usaha) -> str:
    if due is not None and due < hari_ini:
        inti = f"sudah lewat jatuh tempo {(hari_ini - due).days} hari"
    else:
        inti = "jatuh tempo " + ("hari ini" if due == hari_ini else "besok" if due == hari_ini + timedelta(days=1)
                                 else _tgl_pendek(due) if due else "-")
    baris = [f"Halo {nama}, mengingatkan faktur {nomor_faktur} sebesar {rp(sisa)} {inti}."]
    if rekening:
        baris.append(f"Pembayaran ke {rekening}.")
    baris += ["Terima kasih 🙏", f"— {usaha}"]
    return "\n".join(baris)


def _wa_targets(faktur: list, kontak: dict, hari_ini, rekening, usaha) -> list:
    """Satu target per pelanggan; beberapa faktur digabung dalam satu pesan."""
    per_pel = {}
    for f in faktur:
        per_pel.setdefault(str(f["customer_id"]), []).append(f)
    out = []
    for cid, fs in per_pel.items():
        k = kontak.get(cid, {})
        nama = k.get("nama") or fs[0]["customer_name"]
        if len(fs) == 1:
            f = fs[0]
            pesan = _pesan_wa(nama, f["invoice_number"], f["outstanding"], f["due_date"], hari_ini, rekening, usaha)
        else:
            total = sum((d(f["outstanding"]) for f in fs), NOL)
            nomor = ", ".join(f["invoice_number"] for f in fs)
            pesan = _pesan_wa(nama, nomor, total, min((f["due_date"] for f in fs if f["due_date"]), default=None),
                              hari_ini, rekening, usaha)
        out.append({"customer_id": cid, "name": nama, "phone": k.get("wa"), "message": pesan})
    return out


def _ref_faktur(f) -> dict:
    return {"kind": "sales_invoice", "id": str(f["invoice_id"]), "number": f["invoice_number"]}


def susun_tugas(*, hari_ini: date, ar_rows, ap_rows, so_kirim_rows, so_dp_rows, rekon_rows,
                kontak: dict, rekening: Optional[str], usaha: str) -> list:
    """Murni (tanpa DB) — diuji per jenis tugas dengan fixture."""
    tugas = []
    ar = [r for r in ar_rows if d(r["outstanding"]) > NOL]

    # ar_due_today — satu kartu per faktur
    for f in sorted((r for r in ar if r["due_date"] == hari_ini), key=lambda r: r["invoice_number"]):
        tugas.append({
            "key": f"ar_due_today:{f['invoice_number']}:{hari_ini.isoformat()}:{uang(f['outstanding'])}",
            "type": "ar_due_today", "lane": "now",
            "title_html": f"<b>{_esc(f['invoice_number'])}</b> · {_esc(f['customer_name'])}",
            "subtitle": "Jatuh tempo hari ini", "amount": uang(f["outstanding"]), "refs": [_ref_faktur(f)],
            "actions": [
                {"kind": "wa_remind", "label": "Ingatkan via WA", "primary": True,
                 "payload": {"invoice_ids": [str(f["invoice_id"])]}},
                {"kind": "record_payment", "label": "Catat bayar", "primary": False,
                 "payload": {"invoice_id": str(f["invoice_id"]), "customer_id": str(f["customer_id"]),
                             "amount": uang(f["outstanding"])}},
            ],
            "wa_targets": _wa_targets([f], kontak, hari_ini, rekening, usaha),
        })

    # ar_overdue — digabung satu kartu
    telat = sorted((r for r in ar if _jatuh_tempo_lewat(r["due_date"], hari_ini)),
                   key=lambda r: (r["due_date"] or date.min, r["invoice_number"]))
    if telat:
        total = sum((d(r["outstanding"]) for r in telat), NOL)
        nama = []
        for r in telat:
            if r["customer_name"] not in nama:
                nama.append(r["customer_name"])
        sub = ", ".join(nama[:3]) + (f" +{len(nama) - 3} lainnya" if len(nama) > 3 else "")
        tugas.append({
            "key": f"ar_overdue:{len(telat)}:{uang(total)}:{_sidik(*sorted((r['invoice_number'], uang(r['outstanding'])) for r in telat))}",
            "type": "ar_overdue", "lane": "now",
            "title_html": f"<b>{len(telat)} faktur telat</b>", "subtitle": sub, "amount": uang(total),
            "refs": [_ref_faktur(r) for r in telat],
            "actions": [
                {"kind": "wa_remind", "label": "Ingatkan semua", "primary": True,
                 "payload": {"invoice_ids": [str(r["invoice_id"]) for r in telat]}},
                {"kind": "open_list", "label": "Lihat", "primary": False,
                 "payload": {"list": "sales_invoices", "filter": "overdue"}},
            ],
            "wa_targets": _wa_targets(telat, kontak, hari_ini, rekening, usaha),
        })

    # ar_due_soon — besok per faktur; 2–7 hari digabung per hari
    besok = hari_ini + timedelta(days=1)
    for f in sorted((r for r in ar if r["due_date"] == besok), key=lambda r: r["invoice_number"]):
        tugas.append({
            "key": f"ar_due_soon:{f['invoice_number']}:{besok.isoformat()}:{uang(f['outstanding'])}",
            "type": "ar_due_soon", "lane": "week",
            "title_html": f"<b>{_esc(f['invoice_number'])}</b> · {_esc(f['customer_name'])}",
            "subtitle": "Jatuh tempo besok", "amount": uang(f["outstanding"]), "refs": [_ref_faktur(f)],
            "actions": [
                {"kind": "wa_remind", "label": "Ingatkan via WA", "primary": True,
                 "payload": {"invoice_ids": [str(f["invoice_id"])]}},
                {"kind": "record_payment", "label": "Catat bayar", "primary": False,
                 "payload": {"invoice_id": str(f["invoice_id"]), "customer_id": str(f["customer_id"]),
                             "amount": uang(f["outstanding"])}},
            ],
            "wa_targets": _wa_targets([f], kontak, hari_ini, rekening, usaha),
        })
    for selisih in range(2, 8):
        tgl = hari_ini + timedelta(days=selisih)
        grup = sorted((r for r in ar if r["due_date"] == tgl), key=lambda r: r["invoice_number"])
        if not grup:
            continue
        total = sum((d(r["outstanding"]) for r in grup), NOL)
        tugas.append({
            "key": f"ar_due_soon:{tgl.isoformat()}:{len(grup)}:{uang(total)}",
            "type": "ar_due_soon", "lane": "week",
            "title_html": (f"<b>{len(grup)} faktur</b> jatuh tempo {_esc(_nama_hari(tgl, hari_ini))}" if len(grup) > 1
                           else f"<b>{_esc(grup[0]['invoice_number'])}</b> · {_esc(grup[0]['customer_name'])}"),
            "subtitle": (", ".join(dict.fromkeys(r["customer_name"] for r in grup)) if len(grup) > 1
                         else f"Jatuh tempo {_nama_hari(tgl, hari_ini)}"),
            "amount": uang(total), "refs": [_ref_faktur(r) for r in grup],
            "actions": [
                {"kind": "wa_remind", "label": "Ingatkan via WA", "primary": True,
                 "payload": {"invoice_ids": [str(r["invoice_id"]) for r in grup]}},
                {"kind": "open_list", "label": "Lihat", "primary": False,
                 "payload": {"list": "sales_invoices", "filter": "due_date", "due_date": tgl.isoformat()}},
            ],
            "wa_targets": _wa_targets(grup, kontak, hari_ini, rekening, usaha),
        })

    # ap_due — tagihan ≤ 7 hari (now bila hari ini/telat)
    for b in sorted((r for r in ap_rows if d(r["outstanding"]) > NOL
                     and (r["due_date"] is None or r["due_date"] <= hari_ini + timedelta(days=7))),
                    key=lambda r: (r["due_date"] or date.min, r["bill_number"] or "")):
        due = b["due_date"]
        lewat = _jatuh_tempo_lewat(due, hari_ini) or due == hari_ini
        sub = ("Jatuh tempo hari ini" if due == hari_ini
               else f"Telat {(hari_ini - due).days} hari" if due is not None and due < hari_ini
               else "Tanpa jatuh tempo" if due is None else f"Jatuh tempo {_nama_hari(due, hari_ini)}")
        tugas.append({
            "key": f"ap_due:{b['bill_number']}:{due.isoformat() if due else '-'}:{uang(b['outstanding'])}",
            "type": "ap_due", "lane": "now" if lewat else "week",
            "title_html": f"<b>Tagihan {_esc(b['bill_number'])}</b> · {_esc(b['vendor_name'])}",
            "subtitle": sub, "amount": uang(b["outstanding"]),
            "refs": [{"kind": "bill", "id": str(b["bill_id"]), "number": b["bill_number"]}],
            "actions": [
                {"kind": "record_bill_payment", "label": "Catat pembayaran", "primary": True,
                 "payload": {"bill_id": str(b["bill_id"]), "vendor_id": str(b["vendor_id"]),
                             "amount": uang(b["outstanding"])}},
                {"kind": "open_list", "label": "Lihat", "primary": False,
                 "payload": {"list": "bills", "bill_id": str(b["bill_id"])}},
            ],
        })

    # so_to_ship — satu kartu
    if so_kirim_rows:
        so = sorted(so_kirim_rows, key=lambda r: (r["expected_ship_date"], r["order_number"]))
        nomor = [r["order_number"] for r in so]
        n_telat = sum(1 for r in so if r["expected_ship_date"] < hari_ini)
        sub = ", ".join(nomor[:3]) + (f" +{len(nomor) - 3} lainnya" if len(nomor) > 3 else "")
        if n_telat:
            sub += f" · {n_telat} lewat tanggal kirim"
        tugas.append({
            "key": f"so_to_ship:{len(so)}:{_sidik(*sorted(nomor))}",
            "type": "so_to_ship", "lane": "week",
            "title_html": f"<b>{len(so)} pesanan</b> harus dikirim", "subtitle": sub, "amount": None,
            "refs": [{"kind": "sales_order", "id": str(r["id"]), "number": r["order_number"]} for r in so],
            "actions": [{"kind": "prepare_shipment", "label": "Siapkan kirim", "primary": True,
                         "payload": {"sales_order_ids": [str(r["id"]) for r in so]}}],
        })

    # so_dp_pending — satu kartu
    if so_dp_rows:
        so = sorted(so_dp_rows, key=lambda r: r["order_number"])
        total = sum((d(r["dp_wajib"]) for r in so), NOL)
        nomor = [r["order_number"] for r in so]
        tugas.append({
            "key": f"so_dp_pending:{len(so)}:{uang(total)}:{_sidik(*sorted(nomor))}",
            "type": "so_dp_pending", "lane": "any",
            "title_html": f"<b>{len(so)} DP</b> belum diterima",
            "subtitle": ", ".join(nomor[:3]) + (f" +{len(nomor) - 3} lainnya" if len(nomor) > 3 else ""),
            "amount": uang(total),
            "refs": [{"kind": "sales_order", "id": str(r["id"]), "number": r["order_number"]} for r in so],
            "actions": [{"kind": "open_list", "label": "Lihat", "primary": True,
                         "payload": {"list": "sales_orders", "filter": "dp_pending",
                                     "ids": [str(r["id"]) for r in so]}}],
        })

    # bank_recon — per rekening
    bulan_lalu = date(hari_ini.year, hari_ini.month, 1) - timedelta(days=1)
    for r in sorted(rekon_rows, key=lambda r: r["account_name"]):
        tugas.append({
            "key": f"bank_recon:{r['id']}:{bulan_lalu.strftime('%Y-%m')}",
            "type": "bank_recon", "lane": "any",
            "title_html": f"<b>Rekonsiliasi bank</b> {BULAN_PANJANG[bulan_lalu.month - 1]} {bulan_lalu.year}",
            "subtitle": r["account_name"], "amount": None,
            "refs": [{"kind": "bank_account", "id": str(r["id"]), "number": r["account_name"]}],
            "actions": [{"kind": "start_recon", "label": "Mulai", "primary": True,
                         "payload": {"bank_account_id": str(r["id"]),
                                     "period_end": bulan_lalu.isoformat()}}],
        })

    tugas.sort(key=lambda t: (LAJUR_URUT[t["lane"]], -d(t["amount"]) if t["amount"] else NOL, t["key"]))
    return tugas


# SO yang HARUS DIKIRIM: tanggal kirim ≤ akhir minggu ini, punya baris PERLU DIKIRIM
# (COALESCE(perlu_kirim, track_inventory, false) — ekspresi so_kirim.ringkasan_menunggu_kirim / V318)
# dengan sisa > 0 menurut Surat Jalan AKTIF (so_kirim.terkirim_per_baris) — BUKAN quantity_shipped (mati sejak V264).
SO_STATUS_TERBUKA = ("confirmed", "partial_invoiced", "invoiced")


async def so_harus_kirim(conn, tenant_id: str, akhir_minggu: date) -> list:
    baris = await conn.fetch(
        """SELECT so.id AS so_id, so.order_number, so.expected_ship_date, soi.id AS soi_id, soi.quantity
           FROM sales_orders so
           JOIN sales_order_items soi ON soi.sales_order_id = so.id
           LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id
           WHERE so.tenant_id = $1 AND so.status = ANY($2::text[])
             AND so.expected_ship_date IS NOT NULL AND so.expected_ship_date <= $3
             AND COALESCE(soi.perlu_kirim, p.track_inventory, false)""",
        tenant_id, list(SO_STATUS_TERBUKA), akhir_minggu,
    )
    per_baris, _ = await so_kirim.terkirim_per_baris(conn, tenant_id, sorted({r["so_id"] for r in baris}))
    out = {}
    for r in baris:
        if so_kirim.belum_dikirim(r["quantity"], per_baris.get(r["soi_id"])) > NOL:
            out[r["so_id"]] = {"id": r["so_id"], "order_number": r["order_number"],
                               "expected_ship_date": r["expected_ship_date"]}
    return list(out.values())


async def so_dp_belum(conn, tenant_id: str) -> list:
    """SO Dikonfirmasi bersyarat DP tanpa SATU pun uang muka tertaut yang hidup.
    Tautan = resolusi yang sama dengan customer_deposits.resolve_order_id_for_deposit /
    linked_so_deposits: sales_order_id langsung, bila kosong lewat proforma.sales_order_id.
    Status hidup = selain void/draft/refunded (linked_so_deposits hanya posted/partial, sehingga DP yang
    SUDAH diterapkan ke faktur ('applied') akan terbaca 'belum diterima' — karena itu tak dipakai apa adanya)."""
    return await conn.fetch(
        """SELECT so.id, so.order_number,
                  CASE WHEN COALESCE(so.dp_amount, 0) > 0 THEN so.dp_amount
                       ELSE ROUND(COALESCE(so.total_amount, 0) * so.dp_percent / 100, 2) END AS dp_wajib
           FROM sales_orders so
           WHERE so.tenant_id = $1 AND so.status = 'confirmed'
             AND (COALESCE(so.dp_percent, 0) > 0 OR COALESCE(so.dp_amount, 0) > 0)
             AND NOT EXISTS (
                 SELECT 1 FROM customer_deposits cd
                 LEFT JOIN proformas pf ON pf.id = cd.proforma_id AND pf.tenant_id = cd.tenant_id
                 WHERE cd.tenant_id = so.tenant_id
                   AND COALESCE(cd.sales_order_id, pf.sales_order_id) = so.id
                   AND cd.status NOT IN ('void', 'draft', 'refunded'))""",
        tenant_id,
    )


async def rekening_belum_rekon(conn, tenant_id: str, hari_ini: date) -> list:
    """Putusan pemilik: tugas rekonsiliasi HANYA bila tenant PERNAH merekonsiliasi.
    Lalu: rekening bank aktif yang rekonsiliasi selesai terakhirnya < akhir bulan lalu."""
    pernah = await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM reconciliation_sessions WHERE tenant_id = $1 AND status = 'completed')
               OR EXISTS (SELECT 1 FROM bank_reconciliations WHERE tenant_id = $1 AND status = 'completed')""",
        tenant_id,
    )
    if not pernah:
        return []
    akhir_bulan_lalu = date(hari_ini.year, hari_ini.month, 1) - timedelta(days=1)
    return await conn.fetch(
        """SELECT ba.id, ba.account_name
           FROM bank_accounts ba
           WHERE ba.tenant_id = $1 AND ba.is_active = true AND ba.account_type = 'bank'
             AND COALESCE(GREATEST(
                   (SELECT MAX(rs.statement_end_date) FROM reconciliation_sessions rs
                     WHERE rs.tenant_id = $1 AND rs.account_id = ba.id AND rs.status = 'completed'),
                   (SELECT MAX(br.statement_end_date) FROM bank_reconciliations br
                     WHERE br.tenant_id = $1 AND br.bank_account_id = ba.id AND br.status = 'completed')),
                 DATE '1900-01-01') < $2""",
        tenant_id, akhir_bulan_lalu,
    )


async def kontak_pelanggan(conn, tenant_id: str, customer_ids: Iterable[str]) -> dict:
    ids = sorted({str(c) for c in customer_ids if c})
    if not ids:
        return {}
    rows = await conn.fetch(
        """SELECT id, nama, mobile_phone, telepon, phone, phone2 FROM customers
           WHERE tenant_id = $1 AND id::text = ANY($2::text[])""",
        tenant_id, ids,
    )
    return {str(r["id"]): {"nama": r["nama"],
                           "wa": normalisasi_wa(r["mobile_phone"], r["telepon"], r["phone"], r["phone2"])}
            for r in rows}


async def rekening_tagih(conn, tenant_id: str) -> Optional[str]:
    r = await conn.fetchrow(
        """SELECT bank_name, account_number, account_name FROM bank_accounts
           WHERE tenant_id = $1 AND is_active = true AND account_type = 'bank'
           ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1""",
        tenant_id,
    )
    if not r:
        return None
    teks = " ".join(x for x in (r["bank_name"], r["account_number"]) if x)
    return teks or r["account_name"]


async def tugas_tenant(conn, tenant_id: str, hari_ini: date) -> list:
    """Semua tugas tenant (belum disaring izin / dismiss) — ini yang di-cache."""
    ar_rows = await conn.fetch("SELECT * FROM compute_ar_outstanding($1)", tenant_id)
    ap_rows = await conn.fetch("SELECT * FROM compute_ap_outstanding($1)", tenant_id)
    akhir_minggu = hari_ini + timedelta(days=6 - hari_ini.weekday())
    usaha = await conn.fetchval('SELECT display_name FROM "Tenant" WHERE id = $1', tenant_id) or ""
    return susun_tugas(
        hari_ini=hari_ini, ar_rows=ar_rows, ap_rows=ap_rows,
        so_kirim_rows=await so_harus_kirim(conn, tenant_id, akhir_minggu),
        so_dp_rows=await so_dp_belum(conn, tenant_id),
        rekon_rows=await rekening_belum_rekon(conn, tenant_id, hari_ini),
        kontak=await kontak_pelanggan(conn, tenant_id, (r["customer_id"] for r in ar_rows)),
        rekening=await rekening_tagih(conn, tenant_id), usaha=usaha,
    )


def rangkum_tugas(tugas: List[dict], dismissed: set, done_today: int, as_of: str) -> dict:
    aktif = [t for t in tugas if t["key"] not in dismissed]
    sum_now = sum((d(t["amount"]) for t in aktif if t["lane"] == "now" and t["amount"]), NOL)
    return {"as_of": as_of, "sum_now": uang(sum_now), "done_today": done_today,
            "total_today": done_today + len(aktif), "tasks": aktif}
