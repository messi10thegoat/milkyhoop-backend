"""PEMBELIAN-SERAGAM / Uang Muka Pembelian (vendor deposit) — sisi BACA (22 Okt 2026, P-U3). Server menghitung, FE memformat.
Kembaran uang muka Penjualan (services/uang_muka_daftar); pola acuan: NetSuite Vendor Prepayment (Not Applied / Partially Applied / Fully Applied).

  sisa / terpakai / dikembalikan = TURUNAN JURNAL (Law 1/16/29), bukan kolom cache `remaining_amount`/`applied_amount` (diperbarui trigger):
        sisa        = Σ(debit - kredit) akun peran AP_PREPAID, jurnal efektif bersumber deposit (VENDOR_DEPOSIT + DEPOSIT_APPLICATION + DEPOSIT_REFUND)
        terpakai    = Σ(kredit - debit) jurnal DEPOSIT_APPLICATION ; dikembalikan = idem DEPOSIT_REFUND
  sisa_keadaan   = draf | utuh | sebagian | terpakai | dikembalikan | sebagian_dikembalikan | batal   (keadaan_vd; konsisten dgn sisa jurnal)
  status_text == position_text = Draf · Belum diterapkan · Diterapkan sebagian · Diterapkan penuh · Dikembalikan · Dikembalikan sebagian · Dibatalkan
  aksi (pill, PARTISI trivial: satu kunci) = belum_diterapkan : terbit (bukan draf/void) DAN sisa jurnal > 0  (baris, ?aksi=, summary: predikat SAMA)
  documents/doc_count = bundel /vendor-deposits/{id}/documents tanpa rekap (kelompok_untuk): um (deposit itu sendiri) · po (PO bertaut) · faktur (tagihan yang dikenai penerapan)
"""
from decimal import Decimal
from typing import Optional

from . import po_daftar as PD
from .uang_muka_vendor import SUMBER_JURNAL

NOL = Decimal("0")
AKSI_BELUM_DITERAPKAN = "belum_diterapkan"
AKSI_VD = (AKSI_BELUM_DITERAPKAN,)
KEADAAN_VD = ("draf", "utuh", "sebagian", "terpakai", "dikembalikan", "sebagian_dikembalikan", "batal")
TEKS = {"draf": "Draf", "utuh": "Belum diterapkan", "sebagian": "Diterapkan sebagian", "terpakai": "Diterapkan penuh",
        "dikembalikan": "Dikembalikan", "sebagian_dikembalikan": "Dikembalikan sebagian", "batal": "Dibatalkan"}

# Agregat jurnal per deposit (alias vd = vendor_deposits; {akun} = placeholder id akun AP_PREPAID, mis. $2)
SQL_AGREGAT_TPL = """
    LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(jl.debit - jl.credit), 0) AS sisa,
               COALESCE(SUM(jl.credit - jl.debit) FILTER (WHERE je.source_type = 'DEPOSIT_APPLICATION'), 0) AS terpakai,
               COALESCE(SUM(jl.credit - jl.debit) FILTER (WHERE je.source_type = 'DEPOSIT_REFUND'), 0) AS dikembalikan
        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id
        WHERE je.tenant_id = vd.tenant_id AND je.source_id = vd.id
          AND je.source_type IN ('VENDOR_DEPOSIT', 'DEPOSIT_APPLICATION', 'DEPOSIT_REFUND')
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL
          AND jl.account_id = {akun}
    ) ag ON true"""


def sql_agregat(akun: str) -> str:
    assert tuple(SUMBER_JURNAL) == ("VENDOR_DEPOSIT", "DEPOSIT_APPLICATION", "DEPOSIT_REFUND")  # teks SQL di atas = SUMBER_JURNAL
    return SQL_AGREGAT_TPL.replace("{akun}", akun)


def sql_aksi(aksi: str, vd: str = "vd", ag: str = "ag") -> str:
    if aksi != AKSI_BELUM_DITERAPKAN:
        raise ValueError(aksi)
    return f"({vd}.status NOT IN ('draft', 'void') AND {ag}.sisa > 0)"


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else NOL


def aksi_vd(status: Optional[str], sisa) -> Optional[str]:
    """Pure."""
    if status in ("draft", "void") or sisa is None:
        return None
    return AKSI_BELUM_DITERAPKAN if _d(sisa) > NOL else None


def keadaan_vd(status: Optional[str], amount, terpakai, dikembalikan, sisa) -> str:
    """Pure. Keadaan layar dari status + angka JURNAL."""
    if status == "void":
        return "batal"
    if status == "draft":
        return "draf"
    t, k, s = _d(terpakai), _d(dikembalikan), _d(sisa)
    if s > NOL:
        if t == NOL and k == NOL:
            return "utuh"
        if t == NOL and k > NOL:
            return "sebagian_dikembalikan"
        return "sebagian"
    if t > NOL:
        return "terpakai"
    if k > NOL:
        return "dikembalikan"
    return "utuh"


def position_text(keadaan: str) -> str:
    return TEKS.get(keadaan, keadaan)


def syarat_cari(search: Optional[str], mulai: int, vd: str = "vd") -> tuple:
    """(predikat, parameter) ?search= daftar uang muka vendor ($1 = tenant); semua kata wajib cocok; satu sumber untuk daftar DAN /summary."""
    if not isinstance(search, str):
        return None, []
    kata = search.strip().split()
    if not kata:
        return None, []
    syarat = [(f"({vd}.deposit_number ILIKE ${mulai + i} OR {vd}.reference ILIKE ${mulai + i} OR {vd}.notes ILIKE ${mulai + i} "
               f"OR {vd}.vendor_id IN (SELECT v.id FROM vendors v WHERE v.tenant_id = {vd}.tenant_id "
               f"AND (v.name ILIKE ${mulai + i} OR v.code ILIKE ${mulai + i} OR v.search_text ILIKE ${mulai + i})) "
               f"OR {vd}.purchase_order_id IN (SELECT p.id FROM purchase_orders p WHERE p.tenant_id = {vd}.tenant_id AND p.po_number ILIKE ${mulai + i}))")
              for i in range(len(kata))]
    return (syarat[0] if len(syarat) == 1 else f"({' AND '.join(syarat)})"), [f"%{w}%" for w in kata]


async def fakta_vd(conn, tenant_id: str, akun, ids: list) -> dict:
    """{deposit_id: {sisa, terpakai, dikembalikan, sisa_keadaan, status_text, position_text, aksi}} — fungsi YANG SAMA untuk baris daftar dan detail."""
    if not ids:
        return {}
    out = {}
    for r in await conn.fetch(
        f"""SELECT vd.id, vd.status, vd.amount, ag.sisa, ag.terpakai, ag.dikembalikan
            FROM vendor_deposits vd {sql_agregat('$2')}
            WHERE vd.tenant_id = $1 AND vd.id = ANY($3::uuid[])""", tenant_id, akun, list(ids)):
        draf_void = r["status"] in ("draft", "void")
        k = keadaan_vd(r["status"], r["amount"], r["terpakai"], r["dikembalikan"], r["sisa"])
        out[r["id"]] = {
            "sisa": None if draf_void else PD.r2(r["sisa"]), "terpakai": PD.r2(r["terpakai"]), "dikembalikan": PD.r2(r["dikembalikan"]),
            "sisa_keadaan": k, "status_text": position_text(k), "position_text": position_text(k),
            "aksi": aksi_vd(r["status"], None if draf_void else r["sisa"]),
        }
    return out


async def kelompok_untuk(conn, tenant_id: str, ids: list) -> dict:
    """{deposit_id: [kelompok]} bundel uang muka vendor: um (deposit ITU SENDIRI, void tampil sebagai dirinya -> batal) · po (PO bertaut) ·
    faktur (tagihan yang dikenai penerapan, urut tanggal). Pembentuk dokumen = po_daftar.dok_* (SATU tempat)."""
    if not ids:
        return {}
    ids = list(ids)
    vd = {r["id"]: r for r in await conn.fetch(
        "SELECT id, deposit_number, deposit_date, amount, status, purchase_order_id FROM vendor_deposits WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
        tenant_id, ids)}
    po_ids = list({r["purchase_order_id"] for r in vd.values() if r["purchase_order_id"]})
    po = {r["id"]: r for r in await conn.fetch(
        "SELECT id, po_number, po_date, total_amount, status, sent_at FROM purchase_orders WHERE tenant_id = $1 AND id = ANY($2::uuid[])",
        tenant_id, po_ids)} if po_ids else {}
    fak: dict = {}
    for b in await conn.fetch(
        """SELECT DISTINCT a.vendor_deposit_id, b.id, b.invoice_number, b.issue_date, b.grand_total, b.status_v2, b.status
           FROM vendor_deposit_applications a JOIN bills b ON b.id = a.bill_id
           WHERE b.tenant_id = $1 AND a.vendor_deposit_id = ANY($2::uuid[]) ORDER BY b.issue_date, b.invoice_number""", tenant_id, ids):
        fak.setdefault(b["vendor_deposit_id"], []).append(b)
    hasil = {}
    for i in ids:
        r = vd.get(i)
        if not r:
            continue
        kel = [{"key": "um", "label": "Uang Muka", "docs": [PD.dok_vendor_deposit(r)]}]
        if r["purchase_order_id"] in po:
            kel.append({"key": "po", "label": "Pesanan Pembelian", "docs": [PD.dok_purchase_order(po[r["purchase_order_id"]])]})
        if fak.get(i):
            kel.append({"key": "faktur", "label": "Faktur Pembelian", "docs": [PD.dok_bill(b) for b in fak[i]]})
        hasil[i] = kel
    return hasil


async def akun_baca(conn, tenant_id: str):
    """account_id AP_PREPAID untuk jalur BACA: peran belum dipetakan -> UUID nol (tak ada baris jurnal cocok -> semua nol), BUKAN galat 409 di layar daftar."""
    import uuid
    from fastapi import HTTPException
    from .role_resolver import AccountRole
    from . import uang_muka_vendor as UMV
    try:
        return await UMV.akun_peran(conn, tenant_id, AccountRole.AP_PREPAID)
    except HTTPException:
        return uuid.UUID(int=0)


# Detail: penerapan & pengembalian (kueri langsung; $1 = deposit, $2 = tenant). Bentuk = VendorDepositApplicationResponse / VendorDepositRefundResponse.
SQL_APLIKASI = """SELECT a.id, a.vendor_deposit_id, a.bill_id, b.invoice_number AS bill_number, b.issue_date AS bill_date, a.amount, a.applied_date,
                         a.journal_id, a.created_at, a.created_by
                  FROM vendor_deposit_applications a JOIN vendor_deposits d ON d.id = a.vendor_deposit_id
                  LEFT JOIN bills b ON b.id = a.bill_id AND b.tenant_id = d.tenant_id
                  WHERE a.vendor_deposit_id = $1 AND d.tenant_id = $2 ORDER BY a.applied_date, a.created_at, a.id"""
SQL_REFUND = """SELECT r.id, r.vendor_deposit_id, r.refund_date, r.amount, r.bank_account_id, ba.account_name AS bank_account_name, r.reference,
                       r.journal_id, r.notes, r.created_at, r.created_by
                FROM vendor_deposit_refunds r JOIN vendor_deposits d ON d.id = r.vendor_deposit_id
                LEFT JOIN bank_accounts ba ON ba.id = r.bank_account_id AND ba.tenant_id = d.tenant_id
                WHERE r.vendor_deposit_id = $1 AND d.tenant_id = $2 ORDER BY r.refund_date, r.created_at, r.id"""
