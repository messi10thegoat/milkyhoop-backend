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

from .report_engine.ledger import compute_balance, compute_balance_detail  # noqa: F401 (dipakai pemanggil/tes lama)
from . import dashboard_angka as DA  # T1-BE 22 Okt: penentu angka SATU dgn /api/dashboard/all (jurnal EFEKTIF)
from . import faktur_cetak, so_kirim

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


# ── T2 (23 Okt 2026, backend/docs/KONTRAK-dashboard-T2-20261023.md): periode baru, pembanding, tren 12 bulan, umur ──
SUMMARY_VERSI = 2
PERIODE_T2 = ("bulan_ini", "bulan_lalu", "kuartal_ini", "tahun_ini", "kustom")
MAKS_HARI_KUSTOM = 366
# arus kas (bagan masa lalu + perkiraan) tetap memakai satuan lama: periode baru dipetakan ke kunci lama terdekat
PERIODE_ARUS = {"bulan_ini": "month", "bulan_lalu": "month", "kuartal_ini": "month", "tahun_ini": "year", "kustom": "month"}
# satuan geser pembanding "periode sebelumnya": bulan (n) atau hari (n); kustom = DA.periode_sebelumnya (panjang sama)
GESER_PERIODE = {"day": ("hari", 1), "week": ("hari", 7), "month": ("bulan", 1), "year": ("bulan", 12),
                 "bulan_ini": ("bulan", 1), "bulan_lalu": ("bulan", 1), "kuartal_ini": ("bulan", 3), "tahun_ini": ("bulan", 12)}


def _akhir_bulan(t: date) -> date:
    return date(t.year, t.month, calendar.monthrange(t.year, t.month)[1])


def geser_bulan(t: date, n: int, akhir_ke_akhir: bool = False) -> date:
    """t digeser n bulan; hari dijepit ke akhir bulan tujuan (31 Mar -1 -> 28/29 Feb). akhir_ke_akhir: hari terakhir bulan -> hari
    terakhir bulan tujuan (30 Sep +1 -> 31 Okt), supaya bulan PENUH dibanding bulan PENUH."""
    m = t.year * 12 + (t.month - 1) + n
    th, bl = divmod(m, 12)
    bl += 1
    akhir = calendar.monthrange(th, bl)[1]
    if akhir_ke_akhir and t == _akhir_bulan(t):
        return date(th, bl, akhir)
    return date(th, bl, min(t.day, akhir))


def _awal_fiskal(t: date, bulan_awal: int) -> date:
    th = t.year if t.month >= bulan_awal else t.year - 1
    return date(th, bulan_awal, 1)


class PeriodeTakSah(ValueError):
    pass


def rentang_periode_t2(periode: str, hari_ini: date, bulan_fiskal: int = 1,
                       dari: Optional[date] = None, sampai: Optional[date] = None) -> tuple:
    """(mulai, akhir) periode PENUH. Kunci lama -> rentang_periode (makna lama, byte-identik). Kuartal/tahun = FISKAL
    (accounting_settings.fiscal_year_start_month). kustom: dari <= sampai, <= MAKS_HARI_KUSTOM hari, selain itu PeriodeTakSah."""
    if periode in PERIODE:
        return rentang_periode(periode, hari_ini)
    if periode == "bulan_ini":
        return date(hari_ini.year, hari_ini.month, 1), _akhir_bulan(hari_ini)
    if periode == "bulan_lalu":
        a = geser_bulan(date(hari_ini.year, hari_ini.month, 1), -1)
        return a, _akhir_bulan(a)
    if periode in ("kuartal_ini", "tahun_ini"):
        awal_fy = _awal_fiskal(hari_ini, bulan_fiskal)
        if periode == "tahun_ini":
            return awal_fy, geser_bulan(awal_fy, 12) - timedelta(days=1)
        ke = ((hari_ini.year * 12 + hari_ini.month) - (awal_fy.year * 12 + awal_fy.month)) // 3
        a = geser_bulan(awal_fy, 3 * ke)
        return a, geser_bulan(a, 3) - timedelta(days=1)
    if periode == "kustom":
        if dari is None or sampai is None or dari > sampai or hari_periode_inklusif(dari, sampai) > MAKS_HARI_KUSTOM:
            raise PeriodeTakSah("kustom butuh from <= to dan paling lama 366 hari")
        return dari, sampai
    raise PeriodeTakSah(f"periode tak dikenal: {periode}")


def hari_periode_inklusif(mulai: date, akhir: date) -> int:
    return (akhir - mulai).days + 1


def label_rentang(a: date, b: date) -> str:
    """'Oktober 2026' (bulan penuh), '1–23 Okt 2026', '28 Sep – 3 Okt 2026', '28 Des 2025 – 3 Jan 2026'."""
    if a.day == 1 and b == _akhir_bulan(a) and a.month == b.month and a.year == b.year:
        return f"{BULAN_PANJANG[a.month - 1]} {a.year}"
    if a == b:
        return f"{a.day} {BULAN[a.month - 1]} {a.year}"
    if a.year == b.year and a.month == b.month:
        return f"{a.day}–{b.day} {BULAN[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{a.day} {BULAN[a.month - 1]} – {b.day} {BULAN[b.month - 1]} {a.year}"
    return f"{a.day} {BULAN[a.month - 1]} {a.year} – {b.day} {BULAN[b.month - 1]} {b.year}"


def label_periode_t2(periode: str, mulai: date, akhir: date, bulan_fiskal: int = 1) -> str:
    if periode in PERIODE:
        return LABEL_PERIODE[periode]
    if periode == "kuartal_ini":
        awal_fy = _awal_fiskal(mulai, bulan_fiskal)
        ke = ((mulai.year * 12 + mulai.month) - (awal_fy.year * 12 + awal_fy.month)) // 3 + 1
        return f"Kuartal {ke} {akhir.year}"
    if periode == "tahun_ini":
        return f"Tahun {mulai.year}" if mulai.month == 1 else f"Tahun buku {mulai.year}/{akhir.year}"
    return label_rentang(mulai, akhir)


def rentang_pembanding(periode: str, mulai: date, akhir: date, hari_ini: date) -> tuple:
    """-> (range, prev_period, prev_year), masing-masing (mulai, akhir). Pola QBO 'period to date': range = [mulai, min(akhir, hari ini)];
    prev_period = range digeser satu unit periode (bulan/kuartal/tahun: per bulan, hari dijepit, akhir-bulan -> akhir-bulan; hari/minggu:
    per hari; kustom: panjang sama sebelumnya); prev_year = range digeser 12 bulan."""
    r = (mulai, min(akhir, hari_ini))
    if periode == "kustom":
        pp = DA.periode_sebelumnya(*r)
    else:
        satuan, n = GESER_PERIODE[periode]
        if satuan == "hari":
            pp = (r[0] - timedelta(days=n), r[1] - timedelta(days=n))
        else:
            pp = (geser_bulan(r[0], -n), geser_bulan(r[1], -n, akhir_ke_akhir=True))
    py = (geser_bulan(r[0], -12), geser_bulan(r[1], -12, akhir_ke_akhir=True))
    return r, pp, py


def delta_pct(kini: Decimal, banding: Decimal) -> Optional[float]:
    if banding == NOL:
        return None
    return float(((kini - banding) * 100 / abs(banding)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _objek_banding(kini: Decimal, banding: Optional[Decimal]) -> Optional[dict]:
    if banding is None:
        return None
    return {"value": uang(banding), "delta": uang(kini - banding), "delta_pct": delta_pct(kini, banding)}


async def _metrik_rentang_banyak(conn, tenant_id: str, rentang: list) -> list:
    """Semua rentang pembanding dalam 2 kueri (DA.laba_rugi_rentang + DA.arus_kas_neto_rentang), setara DA.laba_rugi + DA.arus_kas_neto per rentang."""
    lr = await DA.laba_rugi_rentang(conn, tenant_id, rentang)
    kas = await DA.arus_kas_neto_rentang(conn, tenant_id, rentang)
    return [{"revenue": x["pendapatan"], "expense": x["beban"], "profit": x["laba"], "net_cash": k} for x, k in zip(lr, kas)]


async def pembanding(conn, tenant_id: str, periode: str, mulai: date, akhir: date, hari_ini: date,
                     pertama: Optional[date]) -> dict:
    """Blok `comparison` (KONTRAK T2 §3). Pembanding yang berakhir sebelum jurnal efektif pertama -> null. needs_action HANYA net_cash."""
    r, pp, py = rentang_pembanding(periode, mulai, akhir, hari_ini)
    ada = lambda rr: pertama is not None and rr[1] >= pertama and rr[0] <= rr[1]
    perlu = [r] + [rr for rr in (pp, py) if ada(rr)]
    hasil = dict(zip(perlu, await _metrik_rentang_banyak(conn, tenant_id, perlu)))
    kini = hasil[r]
    m_pp = hasil[pp] if ada(pp) else None
    m_py = hasil[py] if ada(py) else None
    metrik = {}
    for k in ("revenue", "expense", "profit", "net_cash"):
        metrik[k] = {"current": uang(kini[k]),
                     "prev_period": _objek_banding(kini[k], m_pp[k] if m_pp else None),
                     "prev_year": _objek_banding(kini[k], m_py[k] if m_py else None)}
    metrik["net_cash"]["needs_action"] = kini["net_cash"] < NOL
    rng = lambda rr: {"label": label_rentang(*rr), "start": rr[0].isoformat(), "end": rr[1].isoformat()}
    return {"available": r[0] <= r[1] and (m_pp is not None or m_py is not None),
            "range": rng(r), "prev_period": rng(pp), "prev_year": rng(py), "metrics": metrik}


async def tren_12_bulan(conn, tenant_id: str, hari_ini: date, pertama: Optional[date]) -> dict:
    """Blok `trend_12m` (KONTRAK T2 §4): 12 bulan kalender berakhir bulan hari ini, SATU kueri DA.laba_rugi_bulanan (== DA.laba_rugi per
    bulan). Bulan yang berakhir sebelum jurnal efektif pertama -> null; bulan berjalan partial."""
    awal_ini = date(hari_ini.year, hari_ini.month, 1)
    awal = geser_bulan(awal_ini, -11)
    data = await DA.laba_rugi_bulanan(conn, tenant_id, awal, _akhir_bulan(hari_ini))
    titik = []
    for i in range(12):
        b = geser_bulan(awal, i)
        x = data.get(b)
        kosong = pertama is None or _akhir_bulan(b) < pertama
        nilai = (lambda k: None if kosong else uang(x[k] if x else NOL))
        titik.append({"month": f"{b.year}-{b.month:02d}", "label": BULAN[b.month - 1],
                      "revenue": nilai("pendapatan"), "expense": nilai("beban"), "profit": nilai("laba"),
                      "partial": b == awal_ini})
    return {"basis": "accrual", "points": titik}


def umur_blok(rows, hari_ini: date, modul: str, kolom_id: str, kolom_tgl: str, list_ada: bool) -> dict:
    """Satu sisi `aging` (KONTRAK T2 §5) dari baris compute_ar/ap_outstanding yang SUDAH diambil. Bucket per dokumen sisa > 0 menurut
    jatuh tempo EFEKTIF (due ?? tanggal dokumen = daftar seragam) lewat rumus TUNGGAL jatuh_tempo.kelas_umur. Baris tanpa dokumen
    (kredit pelanggan / nota debit bebas) + dokumen bersisa <= 0 -> unapplied_credits. Σ bucket + unapplied_credits == total."""
    from .jatuh_tempo import UMUR_KUNCI, UMUR_KE_BUCKET, kelas_umur
    jumlah = {k: [0, NOL] for k in UMUR_KUNCI}
    total, kredit = NOL, NOL
    for r in rows:
        sisa = d(r["outstanding"])
        total += sisa
        if r[kolom_id] is None or sisa <= NOL:
            kredit += sisa
            continue
        k = kelas_umur(r["due_date"] or r.get(kolom_tgl), hari_ini)
        if k is None:  # tanpa tanggal sama sekali: tak bisa diumurkan -> paling tua (pola laporan umur lama), tetap terhitung
            k = "umur_90_plus"
        jumlah[k][0] += 1
        jumlah[k][1] += sisa
    return {"total": uang(total), "unapplied_credits": uang(kredit),
            "buckets": [{"key": UMUR_KE_BUCKET[k], "count": jumlah[k][0], "amount": uang(jumlah[k][1]),
                         "list": {"modul": modul, "aksi": k} if list_ada else None} for k in UMUR_KUNCI]}


# Faktur Pembelian: kunci umur_* tagihan_daftar (BACKEND UMUR-TAGIHAN) LIVE 0cc3ba5c 23 Okt -> list {modul: bills, aksi: umur_*}
UMUR_TAGIHAN_LIVE = True


def umur(ar_rows, ap_rows, hari_ini: date) -> dict:
    return {"as_of": hari_ini.isoformat(),
            "receivables": umur_blok(ar_rows, hari_ini, "sales_invoices", "invoice_id", "invoice_date", True),
            "payables": umur_blok(ap_rows, hari_ini, "bills", "bill_id", "bill_date", UMUR_TAGIHAN_LIVE)}


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
    # T1-BE: jurnal EFEKTIF (DA.kas_per_akun == kas /all); dulu POSTED-saja -> kaos +65.218 (void beban lama status VOID)
    total, rinci = await DA.kas_per_akun(conn, tenant_id, hari_ini)
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
    # T2 §5.4 (aditif): disamakan dengan piutang() -- aturan lewat YANG SAMA (_jatuh_tempo_lewat), medan v1 tak berubah
    n_telat, vendor, due7 = 0, set(), []
    for r in rows:
        sisa = d(r["outstanding"])
        if sisa > NOL:
            if _jatuh_tempo_lewat(r["due_date"], hari_ini):
                n_telat += 1
            vendor.add(str(r["vendor_id"]))
            if r["due_date"] is None or r["due_date"] <= hari_ini + timedelta(days=7):
                due7.append(r)
    due7.sort(key=lambda r: (r["due_date"] or date.min, r["bill_number"] or ""))
    return {"total": uang(total), "overdue_total": uang(telat),
            "not_due": uang(total - telat), "overdue_count": n_telat, "vendor_count": len(vendor),
            "due_7d": [{"bill_id": str(r["bill_id"]) if r["bill_id"] else None, "number": r["bill_number"],
                        "vendor": r["vendor_name"], "due_date": r["due_date"].isoformat() if r["due_date"] else None,
                        "balance": uang(r["outstanding"])} for r in due7[:50]]}


async def laba_rugi_dan_beban(conn, tenant_id: str, mulai: date, akhir: date) -> tuple:
    """Rumus = report_engine.income_statement (akrual): laba_bersih = REV − COGS − EXP + OI − OE.
    revenue = pendapatan + pendapatan lain; expense = hpp + beban usaha + beban lain."""
    # T1-BE: DA.laba_rugi (jurnal EFEKTIF; SATU fungsi dgn /all)
    _lr = await DA.laba_rugi(conn, tenant_id, mulai, akhir)
    pendapatan, rinci, beban, laba = _lr["pendapatan"], _lr["rinci_beban"], _lr["beban"], _lr["laba"]
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
    WHERE je.tenant_id = $1 AND je.status = 'POSTED' AND is_effective_journal(je.id)
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
        # T1-BE: DA.arus_kas_harian (jurnal EFEKTIF, definisi /all); dulu neto per jurnal atas SEMUA POSTED -> transaksi batal
        # (asli + pembalik) menggelembungkan masuk & keluar (grapgrap YTD +18,5 jt di dua sisi).
        per_hari = await DA.arus_kas_harian(conn, tenant_id, lalu[0][1], lalu[-1][2])
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


async def ringkasan(conn, tenant_id: str, periode: str, hari_ini: date, tz_nama: str, as_of: str,
                    dari: Optional[date] = None, sampai: Optional[date] = None) -> dict:
    """summary_versi 2 (T2): medan v1 byte-identik untuk kunci periode lama; blok aditif comparison / trend_12m / aging."""
    ar_rows = await conn.fetch("SELECT * FROM compute_ar_outstanding($1)", tenant_id)
    ap_rows = await conn.fetch("SELECT * FROM compute_ap_outstanding($1)", tenant_id)
    bulan_fiskal = await DA.bulan_awal_fiskal(conn, tenant_id)
    mulai, akhir = rentang_periode_t2(periode, hari_ini, bulan_fiskal, dari, sampai)
    pnl, expenses = await laba_rugi_dan_beban(conn, tenant_id, mulai, akhir)
    pertama = await DA.jurnal_efektif_pertama(conn, tenant_id)
    return {
        "as_of": as_of,
        "period": periode,
        "period_label": label_periode_t2(periode, mulai, akhir, bulan_fiskal),
        "period_start": mulai.isoformat(),
        "period_end": akhir.isoformat(),
        "tenant_tz": tz_nama,
        "cash": await kas_bank(conn, tenant_id, hari_ini),
        "receivables": await piutang(conn, tenant_id, hari_ini, ar_rows),
        "payables": await hutang(conn, tenant_id, hari_ini, ap_rows),
        "pnl": pnl,
        "cashflow": await arus_kas(conn, tenant_id, PERIODE_ARUS.get(periode, periode), hari_ini, ar_rows, ap_rows),
        "expenses": expenses,
        "summary_versi": SUMMARY_VERSI,
        "comparison": await pembanding(conn, tenant_id, periode, mulai, akhir, hari_ini, pertama),
        "trend_12m": await tren_12_bulan(conn, tenant_id, hari_ini, pertama),
        "aging": umur(ar_rows, ap_rows, hari_ini),
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
    # T2: pembanding memuat kas bersih -> laporan + kas; tren = laporan. aging per sisi -> BAGIAN_AGING (null per sisi).
    "comparison": ("report", "kas_bank"),
    "trend_12m": ("report",),
}
BAGIAN_AGING = {"receivables": ("sales_invoice",), "payables": ("purchase_invoice",)}


# ─────────────────────────── tugas (tasks) ───────────────────────────

TUGAS_MODUL = {
    "ar_due_today": ("sales_invoice",), "ar_overdue": ("sales_invoice",), "ar_due_soon": ("sales_invoice",),
    "ap_due": ("purchase_invoice",), "so_to_ship": ("sales_order",), "so_dp_pending": ("sales_order",),
    "bank_recon": ("kas_bank",),
}
JENIS_TUGAS = tuple(TUGAS_MODUL)
# 22 Okt 2026: kunci pengingat (services/pengingat, "{modul}:{kunci_aksi}:{count}:{amount}") juga bisa di-dismiss
from .pengingat import MODUL_DAFTAR as _MODUL_PENGINGAT  # noqa: E402
POLA_KUNCI = re.compile(r"^(%s):[^\s]{1,280}$" % "|".join(JENIS_TUGAS + _MODUL_PENGINGAT))


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


def teks_rekening(bank, nomor, pemilik, rek=None) -> Optional[str]:
    """'BCA 123 a.n. Anthonius …' — pemilik lewat faktur_cetak.pemilik_rekening (SAMA dengan blok bayar PDF faktur:
    awalan nama bank dibuang). Tanpa bank & nomor -> None."""
    inti = " ".join(x.strip() for x in (bank, nomor) if x and x.strip())
    if not inti:
        return None
    an = faktur_cetak.pemilik_dari(rek, bank, nomor, pemilik) if rek else faktur_cetak.pemilik_rekening(bank, pemilik)
    return f"{inti} a.n. {an}" if an else inti


def _wa_targets(faktur: list, kontak: dict, hari_ini, rekening, usaha, rek_faktur=None) -> list:
    """Satu target per pelanggan; beberapa faktur digabung dalam satu pesan.
    Rekening = rekening yang TERTULIS di faktur (seperti PDF-nya); cadangan = rekening bank tenant."""
    per_pel = {}
    for f in faktur:
        per_pel.setdefault(str(f["customer_id"]), []).append(f)
    out = []
    for cid, fs in per_pel.items():
        k = kontak.get(cid, {})
        nama = k.get("nama") or fs[0]["customer_name"]
        rek = next(((rek_faktur or {}).get(str(f["invoice_id"])) for f in fs
                    if (rek_faktur or {}).get(str(f["invoice_id"]))), None) or rekening
        if len(fs) == 1:
            f = fs[0]
            pesan = _pesan_wa(nama, f["invoice_number"], f["outstanding"], f["due_date"], hari_ini, rek, usaha)
        else:
            total = sum((d(f["outstanding"]) for f in fs), NOL)
            nomor = ", ".join(f["invoice_number"] for f in fs)
            pesan = _pesan_wa(nama, nomor, total, min((f["due_date"] for f in fs if f["due_date"]), default=None),
                              hari_ini, rek, usaha)
        out.append({"customer_id": cid, "name": nama, "phone": k.get("wa"), "message": pesan})
    return out


def _ref_faktur(f) -> dict:
    return {"kind": "sales_invoice", "id": str(f["invoice_id"]), "number": f["invoice_number"]}


def _str_atau_none(v) -> Optional[str]:
    return str(v) if v is not None else None


# ─── SATU SUMBER kartu "Perlu dikerjakan" ↔ daftar tujuan (MASTER 5 Okt 2026) ───
# Kartu dan filter daftar ?tugas= memanggil PEMILIH YANG SAMA (bukan salinan predikat): angka kartu == baris daftar.
TUGAS_FAKTUR = ("telat", "jatuh_tempo_hari_ini", "belum_lunas")
TUGAS_SO = ("harus_kirim", "dp_belum_diterima", "menunggu_tagih", "selesai")


def akhir_minggu(hari_ini: date) -> date:
    return hari_ini + timedelta(days=6 - hari_ini.weekday())


def pilih_faktur_telat(ar_rows, hari_ini: date) -> list:
    """Kartu ar_overdue: sisa > 0 (compute_ar_outstanding) DAN jatuh tempo lewat (due NULL = telat, ember 90+)."""
    return [r for r in ar_rows if d(r["outstanding"]) > NOL and _jatuh_tempo_lewat(r["due_date"], hari_ini)]


def pilih_belum_lunas(ar_rows) -> list:
    """Faktur BELUM LUNAS (5 Okt 2026, temuan pemilik grapgrap): baris FAKTUR (invoice_id ada) dengan sisa > 0.
    compute_ar_outstanding juga memuat baris NOTA KREDIT (invoice_id NULL, sisa negatif) -- bukan faktur.
    SATU aturan untuk ?tugas=belum_lunas DAN hitungan kartu outstanding-summary (overdue_count + current_count)."""
    return [r for r in ar_rows if r["invoice_id"] is not None and d(r["outstanding"]) > NOL]


def pilih_jatuh_tempo_hari_ini(ar_rows, hari_ini: date) -> list:
    """Kartu ar_due_today: sisa > 0 DAN jatuh tempo = hari ini."""
    return [r for r in ar_rows if d(r["outstanding"]) > NOL and r["due_date"] == hari_ini]


async def id_tugas(conn, tenant_id: str, tugas: str, hari_ini: date) -> list:
    """id dokumen di balik kartu `tugas` -- dipakai filter daftar ?tugas= (faktur / pesanan)."""
    if tugas in TUGAS_FAKTUR:
        ar = await conn.fetch("SELECT * FROM compute_ar_outstanding($1)", tenant_id)
        if tugas == "belum_lunas":
            return [r["invoice_id"] for r in pilih_belum_lunas(ar)]
        pilih = pilih_faktur_telat if tugas == "telat" else pilih_jatuh_tempo_hari_ini
        return [r["invoice_id"] for r in pilih(ar, hari_ini)]
    if tugas == "harus_kirim":
        return [r["id"] for r in await so_harus_kirim(conn, tenant_id, hari_ini)]
    if tugas == "dp_belum_diterima":
        return [r["id"] for r in await so_dp_belum(conn, tenant_id, hari_ini)]
    if tugas == "tersedia":  # 7 Okt 2026: SO fase "Tersedia" (so_agregat.PREDIKAT_TERSEDIA = kartu summary = baris)
        from . import so_agregat as _sa
        return await _sa.id_tersedia(conn, tenant_id)
    if tugas == "menunggu_tagih":  # DEFINISI-TAGIH (14 Okt 2026, MASTER): kartu = pill perlu_ditagih, SATU sumber so_aksi
        from . import so_aksi
        return so_aksi.id_aksi(await so_aksi.fakta(conn, tenant_id, hari_ini), "perlu_ditagih")
    if tugas == "selesai":  # 5 Okt 2026: predikat PERSIS kartu daftar SO (so_agregat)
        from . import so_agregat as _sa
        return await _sa.id_selesai(conn, tenant_id)
    raise ValueError(f"tugas tak dikenal: {tugas!r}")


def susun_tugas(*, hari_ini: date, ar_rows, ap_rows, so_kirim_rows, so_dp_rows, rekon_rows,
                kontak: dict, rekening: Optional[str], usaha: str, rek_faktur: Optional[dict] = None) -> list:
    """Murni (tanpa DB) — diuji per jenis tugas dengan fixture."""
    tugas = []
    ar = [r for r in ar_rows if d(r["outstanding"]) > NOL]

    # ar_due_today — satu kartu per faktur
    for f in sorted(pilih_jatuh_tempo_hari_ini(ar, hari_ini), key=lambda r: r["invoice_number"]):
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
            "wa_targets": _wa_targets([f], kontak, hari_ini, rekening, usaha, rek_faktur),
        })

    # ar_overdue — digabung satu kartu
    telat = sorted(pilih_faktur_telat(ar, hari_ini), key=lambda r: (r["due_date"] or date.min, r["invoice_number"]))
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
                 "payload": {"list": "sales_invoices", "filter": "overdue", "tugas": "telat"}},
            ],
            "wa_targets": _wa_targets(telat, kontak, hari_ini, rekening, usaha, rek_faktur),
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
            "wa_targets": _wa_targets([f], kontak, hari_ini, rekening, usaha, rek_faktur),
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
            "wa_targets": _wa_targets(grup, kontak, hari_ini, rekening, usaha, rek_faktur),
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
        # DASBOR-SATU-SUMBER (15 Okt 2026): tanggal kirim kosong = siap dipenuhi sekarang (urut sebagai hari ini);
        # "lewat tanggal kirim" = kunci telat_kirim dari so_aksi (bukan hitung ulang tanggal)
        so = sorted(so_kirim_rows, key=lambda r: (r["expected_ship_date"] or hari_ini, r["order_number"]))
        nomor = [r["order_number"] for r in so]
        n_telat = sum(1 for r in so if r["telat"])
        sub = ", ".join(nomor[:3]) + (f" +{len(nomor) - 3} lainnya" if len(nomor) > 3 else "")
        if n_telat:
            sub += f" · {n_telat} lewat tanggal kirim"
        tuju = next((r for r in so if r.get("pending_invoice_id")), {})
        tugas.append({
            "key": f"so_to_ship:{len(so)}:{_sidik(*sorted(nomor))}",
            "type": "so_to_ship", "lane": "week",
            "title_html": f"<b>{len(so)} pesanan</b> harus dikirim", "subtitle": sub, "amount": None,
            "refs": [{"kind": "sales_order", "id": str(r["id"]), "number": r["order_number"],
                      "pending_invoice_id": _str_atau_none(r.get("pending_invoice_id")),
                      "pending_invoice_number": r.get("pending_invoice_number")} for r in so],
            "actions": [{"kind": "prepare_shipment", "label": "Siapkan kirim", "primary": True,
                         "payload": {"sales_order_ids": [str(r["id"]) for r in so],
                                     # D5: faktur tujuan "Buat Pengiriman" = milik SO PERTAMA (urutan kartu) yang punya
                                     "pending_invoice_id": _str_atau_none(tuju.get("pending_invoice_id")),
                                     "pending_invoice_number": tuju.get("pending_invoice_number")}}],
        })

    # so_dp_pending — satu kartu
    if so_dp_rows:
        so = sorted(so_dp_rows, key=lambda r: r["order_number"])
        total = sum((d(r["dp_wajib"]) for r in so), NOL)
        nomor = [r["order_number"] for r in so]
        tugas.append({
            "key": f"so_dp_pending:{len(so)}:{uang(total)}:{_sidik(*sorted(nomor))}",
            "type": "so_dp_pending", "lane": "any",
            "title_html": f"<b>{len(so)} pesanan</b> menunggu bayar",  # 23 Okt: menunggu_pembayaran U perlu_minta_dp (dulu "N DP belum diterima")
            "subtitle": ", ".join(nomor[:3]) + (f" +{len(nomor) - 3} lainnya" if len(nomor) > 3 else ""),
            "amount": uang(total),
            "refs": [{"kind": "sales_order", "id": str(r["id"]), "number": r["order_number"]} for r in so],
            "actions": [{"kind": "open_list", "label": "Lihat", "primary": True,
                         "payload": {"list": "sales_orders", "filter": "dp_pending", "tugas": "dp_belum_diterima",
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


# Kartu "N pesanan harus dikirim" + ?tugas=harus_kirim (DASBOR-SATU-SUMBER, 15 Okt 2026, putusan MASTER (b); pola NetSuite
# reminder = saved search yang sama + satu kriteria): SATU sumber = so_aksi.fakta, kunci telat_kirim | perlu_dikirim |
# siap_diserahkan (partisi SO bersisa kirim; status = so_agregat.AKTIF_TIDAK yang sama dengan pill), lalu JENDELA: tanggal
# kirim <= akhir minggu ATAU kosong (ship date kosong = siap dipenuhi sekarang). Kueri sendiri (daftar putih status,
# tanggal wajib ada) DICABUT: ia melewatkan SO tanpa tanggal kirim (kaos 10, grapgrap 2) dan status di luar daftarnya.
KUNCI_HARUS_KIRIM = ("telat_kirim", "perlu_dikirim", "siap_diserahkan")


def pilih_harus_kirim(f: dict, hari_ini: date) -> list:
    """Murni. f = so_aksi.fakta -> [{id, order_number, expected_ship_date, telat}] SO kartu harus_kirim."""
    batas = akhir_minggu(hari_ini)
    out = []
    for i, v in f.items():
        tgl = v.get("tanggal_kirim")
        if any(k in v["aksi"] for k in KUNCI_HARUS_KIRIM) and (tgl is None or tgl <= batas):
            out.append({"id": i, "order_number": v["nomor"], "expected_ship_date": tgl,
                        "telat": "telat_kirim" in v["aksi"]})
    return out


async def so_harus_kirim(conn, tenant_id: str, hari_ini: date) -> list:
    from . import so_aksi
    out = {r["id"]: r for r in pilih_harus_kirim(await so_aksi.fakta(conn, tenant_id, hari_ini), hari_ini)}
    # D5: faktur TERBIT tertua per SO dengan pengiriman terbuka (gerbang fulfill yang sama) -> tujuan "Siapkan kirim"
    tertunda = await so_kirim.faktur_tertunda_per_so(conn, tenant_id, sorted(out))
    for so_id, v in out.items():
        f = tertunda.get(so_id)
        v["pending_invoice_id"] = f["id"] if f else None
        v["pending_invoice_number"] = f["invoice_number"] if f else None
    return list(out.values())


async def so_dp_belum(conn, tenant_id: str, hari_ini: date) -> list:
    """Kartu "Menunggu bayar" (PERLU-MINTA-DP 14 Okt, MENUNGGU-PEMBAYARAN 23 Okt, putusan MASTER): SATU sumber = so_aksi (pill
    menunggu_pembayaran U perlu_minta_dp). Kueri sendiri (SO confirmed bersyarat DP tanpa uang muka) DICABUT: ia memasukkan SO
    yang sudah ditagih PELUNASAN dan melewatkan proforma DP tanpa syarat (kaos 16 vs pill 1). -> [{id, order_number, dp_wajib}]."""
    from . import so_aksi
    return await so_aksi.dp_belum_diterima(conn, tenant_id, hari_ini)


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


async def rekening_per_faktur(conn, tenant_id: str, invoice_ids) -> dict:
    """{invoice_id: 'BCA 123 a.n. …'} dari medan bayar faktur (payment_bank_name/_account_number/_account_holder)."""
    ids = sorted({str(i) for i in invoice_ids if i})
    if not ids:
        return {}
    rows = await conn.fetch(
        """SELECT id, payment_bank_name, payment_account_number, payment_account_holder FROM sales_invoices
           WHERE tenant_id = $1 AND id::text = ANY($2::text[])""",
        tenant_id, ids,
    )
    out = {}
    rek = await faktur_cetak.muat_rekening(conn, tenant_id) if rows else None
    for r in rows:
        t = teks_rekening(r["payment_bank_name"], r["payment_account_number"], r["payment_account_holder"], rek)
        if t:
            out[str(r["id"])] = t
    return out


async def rekening_tagih(conn, tenant_id: str) -> Optional[str]:
    """Cadangan bila faktur tak menulis rekening: rekening bank tenant (bawaan dulu). Pemilik = nama akun
    (sama dengan yang disalin ke faktur) lewat pemilik_rekening."""
    r = await conn.fetchrow(
        """SELECT bank_name, account_number, account_name, account_holder_name FROM bank_accounts
           WHERE tenant_id = $1 AND is_active = true AND account_type = 'bank'
           ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1""",
        tenant_id,
    )
    if not r:
        return None
    an = faktur_cetak.pemilik_rekening(r["bank_name"], None, r["account_holder_name"])   # nama akun BUKAN pemilik
    inti = " ".join(x.strip() for x in (r["bank_name"], r["account_number"]) if x and x.strip())
    if not inti:
        return None
    return f"{inti} a.n. {an}" if an else inti


async def tugas_tenant(conn, tenant_id: str, hari_ini: date) -> list:
    """Semua tugas tenant (belum disaring izin / dismiss) — ini yang di-cache."""
    ar_rows = await conn.fetch("SELECT * FROM compute_ar_outstanding($1)", tenant_id)
    ap_rows = await conn.fetch("SELECT * FROM compute_ap_outstanding($1)", tenant_id)
    usaha = await conn.fetchval('SELECT display_name FROM "Tenant" WHERE id = $1', tenant_id) or ""
    return susun_tugas(
        hari_ini=hari_ini, ar_rows=ar_rows, ap_rows=ap_rows,
        so_kirim_rows=await so_harus_kirim(conn, tenant_id, hari_ini),
        so_dp_rows=await so_dp_belum(conn, tenant_id, hari_ini),
        rekon_rows=await rekening_belum_rekon(conn, tenant_id, hari_ini),
        kontak=await kontak_pelanggan(conn, tenant_id, (r["customer_id"] for r in ar_rows)),
        rekening=await rekening_tagih(conn, tenant_id), usaha=usaha,
        rek_faktur=await rekening_per_faktur(conn, tenant_id, (r["invoice_id"] for r in ar_rows
                                                               if d(r["outstanding"]) > NOL)),
    )


def rangkum_tugas(tugas: List[dict], dismissed: set, done_today: int, as_of: str, pengingat: Optional[List[dict]] = None) -> dict:
    """tasks + sum_now = USANG (KONTRAK-dashboard-pengingat §3/§7, dipertahankan selama transisi FE); reminders + sum_in/sum_out =
    SATU sumber (services/pengingat, predikat ?aksi daftar). reminders/sum_in/sum_out/pengingat_versi SELALU ada."""
    from . import pengingat as P
    aktif = [t for t in tugas if t["key"] not in dismissed]
    sum_now = sum((d(t["amount"]) for t in aktif if t["lane"] == "now" and t["amount"]), NOL)
    return {"as_of": as_of, "sum_now": uang(sum_now), "done_today": done_today,
            "total_today": done_today + len(aktif), "tasks": aktif,
            **P.rangkum([x for x in (pengingat or []) if x["key"] not in dismissed])}
