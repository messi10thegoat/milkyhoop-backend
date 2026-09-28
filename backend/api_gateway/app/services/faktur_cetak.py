"""Keadaan CETAK faktur penjualan per status pembayaran (pemilik 27 Sep 2026; spek pdf-faktur-status).

HANYA tampilan: nol tulisan. Angka datang dari server (router PDF): total, amount_paid/amount_due
(compute_ar_outstanding, Law 1/16) dan riwayat pembayaran di bawah.

Riwayat = cabang-cabang compute_ar_outstanding yang SAMA (supaya Σ riwayat == "Dibayar"):
  1. penerimaan: alokasi receive_payment_allocations AKTIF dari penerimaan berjurnal POSTED tak dibalik; jumlah =
     bagian PROPORSIONAL kredit Piutang jurnal penerimaan (rumus V245 persis, sisa pembulatan ke alokasi id terakhir);
  2. nota kredit dengan original_invoice_id: kredit Piutang jurnal CREDIT_NOTE POSTED tak dibalik;
  3. uang muka dipotongkan: customer_deposit_applications status 'active', kredit Piutang jurnal DEPOSIT_APPLICATION
     POSTED tak dibalik.
Alokasi yang DILEPAS (status reversed; cabang 4 fungsi AR) tidak ditampilkan — ledger menetralkannya.
Tanggal cetak = tanggal bisnis TENANT (utils.tanggal_tenant), bukan jam server UTC.
"""
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

NOL = Decimal("0")
TOLERANSI = Decimal("0.005")

SQL_RIWAYAT = """
WITH rp_ar AS (
    SELECT rp.id AS payment_id, SUM(jl.credit) AS ar_credit
    FROM receive_payments rp
    JOIN journal_entries je ON je.id = rp.journal_id AND je.tenant_id = rp.tenant_id
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE rp.tenant_id = $1
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
      AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
      AND rp.id IN (SELECT payment_id FROM receive_payment_allocations WHERE tenant_id = $1 AND invoice_id = $2)
    GROUP BY rp.id
),
alok AS (
    SELECT rpa.id, rpa.payment_id, rpa.invoice_id, rpa.status, a.ar_credit,
           CASE WHEN SUM(rpa.amount_applied) OVER w > 0
                THEN ROUND(a.ar_credit * rpa.amount_applied / SUM(rpa.amount_applied) OVER w, 2)
                ELSE 0 END AS bagian,
           ROW_NUMBER() OVER (PARTITION BY rpa.payment_id ORDER BY rpa.id DESC) AS urut_akhir
    FROM receive_payment_allocations rpa
    JOIN rp_ar a ON a.payment_id = rpa.payment_id
    WHERE rpa.tenant_id = $1
    WINDOW w AS (PARTITION BY rpa.payment_id)
),
alok_final AS (
    SELECT x.*, x.bagian + CASE WHEN x.urut_akhir = 1 AND SUM(x.bagian) OVER (PARTITION BY x.payment_id) > 0
                                THEN x.ar_credit - SUM(x.bagian) OVER (PARTITION BY x.payment_id) ELSE 0 END AS jumlah
    FROM alok x
)
SELECT 'penerimaan' AS sumber, rp.payment_date AS tanggal, rp.payment_number AS nomor,
       rp.payment_method AS metode, ba.bank_name AS bank, af.jumlah
FROM alok_final af
JOIN receive_payments rp ON rp.id = af.payment_id AND rp.tenant_id = $1
-- bank_account_id penerimaan kadang id bank_accounts, kadang id CoA (terukur prod 27 Sep: 32/13 dari 45) -> satu baris
LEFT JOIN LATERAL (
    SELECT b.bank_name FROM bank_accounts b
    WHERE b.tenant_id = rp.tenant_id AND (b.id = rp.bank_account_id OR b.coa_id = rp.bank_account_id)
    ORDER BY (b.id = rp.bank_account_id) DESC, b.is_default DESC LIMIT 1
) ba ON true
WHERE af.invoice_id = $2 AND COALESCE(af.status, 'active') = 'active'

UNION ALL

SELECT 'nota_kredit', cn.credit_note_date, cn.credit_note_number, NULL, NULL, SUM(jl.credit)
FROM credit_notes cn
JOIN journal_entries je ON je.source_id = cn.id AND je.source_type = 'CREDIT_NOTE' AND je.tenant_id = cn.tenant_id
JOIN journal_lines jl ON jl.journal_id = je.id
JOIN chart_of_accounts coa ON coa.id = jl.account_id
WHERE cn.tenant_id = $1 AND cn.original_invoice_id = $2
  AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
  AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
GROUP BY cn.id, cn.credit_note_date, cn.credit_note_number

UNION ALL

SELECT 'uang_muka', d.deposit_date, d.deposit_number, d.payment_method, ba.bank_name, SUM(jl.credit)
FROM customer_deposit_applications cda
JOIN customer_deposits d ON d.id = cda.deposit_id AND d.tenant_id = cda.tenant_id
JOIN journal_entries je ON je.id = cda.journal_id AND je.source_type = 'DEPOSIT_APPLICATION' AND je.tenant_id = cda.tenant_id
JOIN journal_lines jl ON jl.journal_id = je.id
JOIN chart_of_accounts coa ON coa.id = jl.account_id
LEFT JOIN LATERAL (
    SELECT b.bank_name FROM bank_accounts b
    WHERE b.tenant_id = d.tenant_id AND (b.id = d.bank_account_id OR b.coa_id = d.account_id)
    ORDER BY (b.id = d.bank_account_id) DESC, b.is_default DESC LIMIT 1
) ba ON true
WHERE cda.tenant_id = $1 AND cda.invoice_id = $2 AND cda.status = 'active'
  AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
  AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
GROUP BY cda.id, d.deposit_date, d.deposit_number, d.payment_method, ba.bank_name
"""

JENIS = {"penerimaan": "Pembayaran", "nota_kredit": "Nota kredit", "uang_muka": "Uang muka"}


def nama_bank(bank: Optional[str]) -> str:
    """'Bank BCA' dan 'BCA' = bank yang sama; awalan 'Bank ' dibuang untuk cetak."""
    b = (bank or "").strip()
    return b[5:].strip() if b.lower().startswith("bank ") else b


def label_metode(metode: Optional[str], bank: Optional[str]) -> str:
    m = (metode or "").strip().lower()
    if m in ("cash", "tunai"):
        return "Tunai"
    if m in ("bank_transfer", "transfer"):
        return f"Transfer {nama_bank(bank)}".strip()
    return (metode or "").replace("_", " ").strip().capitalize()


def _rapat(t: Optional[str]) -> str:
    return " ".join((t or "").split()).lower()


def nomor_rekening(n: Optional[str]) -> str:
    """Kunci pencocokan faktur -> bank_accounts: digit saja ('123-456 7' == '1234567')."""
    return re.sub(r"\D", "", n or "")


def tampak_nama_akun(pemilik: Optional[str], bank: Optional[str], nama_akun=()) -> bool:
    """Snapshot pemilik yang sebenarnya NAMA AKUN internal Kas & Bank: sama dengan salah satu account_name tenant,
    atau diawali nama bank (konvensi penamaan akun: 'BCA Pemasukan', 'Bank BCA Operasional')."""
    p = _rapat(pemilik)
    if not p:
        return False
    if p in {_rapat(n) for n in nama_akun if n}:
        return True
    for awalan in {_rapat(nama_bank(bank)), _rapat(bank)}:
        if awalan and (p == awalan or p.startswith(awalan + " ")):
            return True
    return False


def pemilik_rekening(bank: Optional[str], pemilik: Optional[str], pemilik_akun: Optional[str] = None,
                     nama_akun=()) -> Optional[str]:
    """SATU penentu "a.n." untuk PDF faktur, PDF proforma/penawaran, dan pesan WA (28 Sep 2026, pemilik).

    Prioritas: (1) bank_accounts.account_holder_name rekening itu (diisi pemilik di Kas & Bank) -> (2) snapshot
    dokumen bila TIDAK tampak nama akun internal -> (3) None = "a.n." tidak dicetak (tak mengarang).
    Dulu snapshot = account_name ('BCA Pemasukan') -> tercetak "a.n. Pemasukan"."""
    a = " ".join((pemilik_akun or "").split())
    if a:
        return a
    p = " ".join((pemilik or "").split())
    if not p or tampak_nama_akun(p, bank, nama_akun):
        return None
    return p


SQL_REKENING_TENANT = """
SELECT account_number, account_name, account_holder_name, is_active
FROM bank_accounts WHERE tenant_id = $1
"""


async def muat_rekening(conn, tenant_id: str) -> Dict[str, Any]:
    """{'pemilik': {nomor digit: account_holder_name}, 'nama_akun': [account_name...]} — rekening aktif menang bila
    nomornya kembar; nama akun NONAKTIF ikut (snapshot lama tetap dikenali sebagai nama internal)."""
    rows = await conn.fetch(SQL_REKENING_TENANT, tenant_id)
    pemilik: Dict[str, str] = {}
    for r in sorted(rows, key=lambda r: bool(r["is_active"])):
        n, h = nomor_rekening(r["account_number"]), (r["account_holder_name"] or "").strip()
        if n and h:
            pemilik[n] = h
    return {"pemilik": pemilik, "nama_akun": [r["account_name"] for r in rows if r["account_name"]]}


def pemilik_dari(rek: Dict[str, Any], bank: Optional[str], nomor: Optional[str], pemilik: Optional[str]) -> Optional[str]:
    return pemilik_rekening(bank, pemilik, rek["pemilik"].get(nomor_rekening(nomor)), rek["nama_akun"])


async def pemilik_cetak(conn, tenant_id: str, bank: Optional[str], nomor: Optional[str],
                        pemilik: Optional[str]) -> Optional[str]:
    """pemilik_rekening dengan data bank_accounts tenant; dipakai juga sebagai SNAPSHOT saat dokumen dibuat.
    Tanpa nomor DAN tanpa pemilik -> None tanpa kueri (dokumen tanpa rekening tak menyentuh bank_accounts)."""
    if not nomor_rekening(nomor) and not (pemilik or "").strip():
        return None
    return pemilik_dari(await muat_rekening(conn, tenant_id), bank, nomor, pemilik)


async def riwayat_pembayaran(conn, tenant_id: str, invoice_id) -> List[Dict[str, Any]]:
    rows = await conn.fetch(SQL_RIWAYAT, tenant_id, invoice_id)
    hasil = []
    for r in rows:
        jumlah = Decimal(str(r["jumlah"] or 0))
        if jumlah <= NOL:
            continue
        hasil.append({
            "sumber": r["sumber"], "tanggal": r["tanggal"], "nomor": r["nomor"] or "-",
            "jenis": JENIS[r["sumber"]],
            "metode": label_metode(r["metode"], r["bank"]) if r["sumber"] != "nota_kredit" else "",
            "jumlah": jumlah,
        })
    hasil.sort(key=lambda h: (h["tanggal"] or date.min, h["nomor"]))
    return hasil


def _tgl(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        return None


STATUS_BATAL = frozenset({"void", "voided", "cancelled"})


def _tanggal_zona(v, zona) -> Optional[date]:
    """voided_at (timestamptz, UTC) -> tanggal di zona TENANT."""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(v, datetime):
        return (v.astimezone(zona) if (zona is not None and v.tzinfo is not None) else v).date()
    return _tgl(v)


def keadaan_cetak(invoice: Dict[str, Any], tanggal_cetak: date, riwayat: List[Dict[str, Any]],
                  zona=None) -> Dict[str, Any]:
    """Keadaan tampilan dari angka server. Urutan: batal > draf > lunas > terlambat > sebagian > belum.
    `jenis` riwayat penerimaan terakhir menjadi 'Pelunasan' bila lunas."""
    status = str(invoice.get("status") or "").lower()
    total = Decimal(str(invoice.get("total_amount") or 0))
    dibayar = Decimal(str(invoice.get("amount_paid") or 0))
    sisa = Decimal(str(invoice.get("amount_due") or 0))
    jatuh_tempo = _tgl(invoice.get("due_date"))
    riwayat = [dict(h) for h in riwayat]

    if status in STATUS_BATAL:
        k = "batal"
    elif status == "draft":
        k = "draf"
    elif total > NOL and sisa <= TOLERANSI:
        k = "lunas"
    elif jatuh_tempo and tanggal_cetak > jatuh_tempo:
        k = "terlambat"
    elif dibayar > TOLERANSI:
        k = "sebagian"
    else:
        k = "belum"

    hari = (tanggal_cetak - jatuh_tempo).days if jatuh_tempo else None
    tanggal_lunas = metode_lunas = None
    if k == "lunas" and riwayat:
        akhir = riwayat[-1]
        tanggal_lunas, metode_lunas = akhir["tanggal"], akhir["metode"]
        penerimaan = [h for h in riwayat if h["sumber"] == "penerimaan"]
        if penerimaan:
            penerimaan[-1]["jenis"] = "Pelunasan"

    return {
        "jenis": k,
        "tanggal_cetak": tanggal_cetak,
        "hari_terlambat": hari if k == "terlambat" else None,
        "hari_lagi": -hari if (k in ("belum", "sebagian", "draf") and hari is not None and hari <= 0) else None,
        "tanggal_lunas": tanggal_lunas,
        "tanggal_batal": _tanggal_zona(invoice.get("voided_at"), zona) if k == "batal" else None,
        "metode_lunas": metode_lunas,
        "riwayat": riwayat if k in ("terlambat", "sebagian", "lunas") else [],
        "tampil_rekening": k not in ("lunas", "batal"),
        "stempel": {"lunas": "LUNAS", "terlambat": "TERLAMBAT", "batal": "BATAL"}.get(k),
        "selisih_riwayat": (sum((h["jumlah"] for h in riwayat), NOL) - dibayar) if k != "batal" else NOL,
    }
