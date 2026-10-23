"""Uang + keadaan Faktur Pembelian (tagihan) dari JURNAL -- SATU sumber untuk daftar, filter/urut status, summary, detail v1/v2, mark_paid
(PEMBELIAN-SERAGAM unit (1), 22 Okt 2026, putusan MASTER; Law 1/16/29).

  sumber   = compute_ap_rincian(tenant) (V440): bill_total = kredit AP jurnal BILL efektif; paid = pembayaran (proporsional) + nota debit +
             uang muka vendor; outstanding = total - paid. Menggantikan BILL_JOURNAL_PAID_CTE (bills.amount CACHE - CTE terbayar; tanpa
             pembagian V245 / nota debit / DP vendor) dan LATERAL detail (pembayaran saja).
  keadaan  = void | draft | paid | overdue | partial | unpaid (enum LAMA, urutan CASE lama):
             - void/draft dibaca dari status_v2 (kolom yang dipakai compute_ap_outstanding); status_v2 NULL -> kolom status lama.
             - tagihan aktif TANPA kredit AP efektif (jurnal BILL dibalik / tak ada) -> 'void' (Batal), sisa 0 -- data TIDAK diubah.
  sisa     = draft -> bills.amount (belum di buku; perilaku lama) · void -> 0 · aktif -> outstanding jurnal.
  dibayar  = paid_amount rincian (0 bila tak ada baris rincian).
Kembaran Python `keadaan_tagihan` / `sisa_tagihan` diuji berpasangan + harness nyata (uji-nyata-tagihan-rincian).
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from .jatuh_tempo import UMUR_KUNCI, kelas_umur, sql_umur

KEADAAN = ("void", "draft", "paid", "overdue", "partial", "unpaid")
URUT_KEADAAN = {"overdue": 1, "unpaid": 2, "partial": 3, "paid": 4, "draft": 5, "void": 6}  # == urutan kolom status lama


def join_rincian(p_tenant: str = "$1", a: str = "b") -> str:
    """LEFT JOIN rincian AP; alias `apr`. p_tenant = placeholder tenant kueri pemanggil."""
    return f"LEFT JOIN compute_ap_rincian({p_tenant}) apr ON apr.bill_id = {a}.id"


def _sql_void(a: str) -> str:
    return f"({a}.status_v2 = 'void' OR ({a}.status_v2 IS NULL AND {a}.status = 'void'))"


def _sql_draft(a: str) -> str:
    return f"({a}.status_v2 = 'draft' OR ({a}.status_v2 IS NULL AND {a}.status = 'draft'))"


def _sql_tanpa_kredit() -> str:
    return "(apr.bill_id IS NULL OR apr.bill_total = 0)"


def sql_keadaan(hari: str, a: str = "b") -> str:
    """Ungkapan SQL keadaan (butuh join_rincian). `hari` = placeholder tanggal bisnis (mis. '$3::date')."""
    return (f"(CASE WHEN {_sql_void(a)} THEN 'void' WHEN {_sql_draft(a)} THEN 'draft' "
            f"WHEN {_sql_tanpa_kredit()} THEN 'void' "
            f"WHEN apr.outstanding <= 0 THEN 'paid' "
            f"WHEN apr.paid_amount > 0 AND {a}.due_date < {hari} THEN 'overdue' "
            f"WHEN apr.paid_amount > 0 THEN 'partial' "
            f"WHEN {a}.due_date < {hari} THEN 'overdue' ELSE 'unpaid' END)")


def sql_urut_keadaan(hari: str, a: str = "b") -> str:
    return "(CASE " + sql_keadaan(hari, a) + " " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in URUT_KEADAAN.items()) + " END)"


def sql_dibayar() -> str:
    return "COALESCE(apr.paid_amount, 0)"


def sql_sisa(a: str = "b") -> str:
    return (f"(CASE WHEN {_sql_void(a)} THEN 0 WHEN {_sql_draft(a)} THEN COALESCE({a}.amount, 0) "
            f"WHEN {_sql_tanpa_kredit()} THEN 0 ELSE apr.outstanding END)")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _void(status, status_v2) -> bool:
    return status_v2 == "void" or (status_v2 is None and status == "void")


def _draft(status, status_v2) -> bool:
    return status_v2 == "draft" or (status_v2 is None and status == "draft")


def keadaan_tagihan(status: Optional[str], status_v2: Optional[str], rincian: Optional[dict], due_date: Optional[date],
                    hari_ini: date) -> str:
    """Kembaran Python sql_keadaan. rincian = baris compute_ap_rincian (bill_total/paid_amount/outstanding) atau None."""
    if _void(status, status_v2):
        return "void"
    if _draft(status, status_v2):
        return "draft"
    if not rincian or _d(rincian.get("bill_total")) == 0:
        return "void"
    paid, sisa = _d(rincian.get("paid_amount")), _d(rincian.get("outstanding"))
    lewat = due_date is not None and due_date < hari_ini
    if sisa <= 0:
        return "paid"
    if paid > 0 and lewat:
        return "overdue"
    if paid > 0:
        return "partial"
    return "overdue" if lewat else "unpaid"


def sisa_tagihan(status: Optional[str], status_v2: Optional[str], rincian: Optional[dict], amount) -> Decimal:
    """Kembaran Python sql_sisa."""
    if _void(status, status_v2):
        return Decimal("0")
    if _draft(status, status_v2):
        return _d(amount)
    if not rincian or _d(rincian.get("bill_total")) == 0:
        return Decimal("0")
    return _d(rincian.get("outstanding"))


async def rincian_satu(conn, tenant_id: str, bill_id) -> Optional[dict]:
    r = await conn.fetchrow("SELECT bill_total, paid_amount, outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2",
                            tenant_id, bill_id)
    return dict(r) if r else None


async def sisa_tagihan_jurnal(conn, tenant_id: str, bill_id) -> Optional[Decimal]:
    """Sisa hutang satu tagihan dari JURNAL (compute_ap_rincian). None = tagihan tidak aktif / tanpa kredit AP efektif (jurnal dibalik).
    SATU sumber untuk pagar bayar (bill_payments), terapkan nota debit, dan daftar tagihan terbuka (U3a, 22 Okt 2026)."""
    r = await conn.fetchrow("SELECT bill_total, outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2", tenant_id, bill_id)
    if not r or Decimal(str(r["bill_total"] or 0)) == 0:
        return None
    return Decimal(str(r["outstanding"]))


# ==== KONTRAK DAFTAR seragam Faktur Pembelian (22 Okt 2026, celah WORKSPACE; acuan sales_invoices + PO P-U2) ==================
# Server menghitung, FE hanya memformat. Kunci aksi = PARTISI (saling eksklusif; pola NetSuite Bills: Overdue / Due this week / Pending Approval):
#   perlu_diposting        = keadaan draft
#   lewat_jatuh_tempo      = TERBUKA (aktif, ber-kredit AP, sisa jurnal > 0) ∧ jatuh tempo efektif < hari ini
#   segera_tempo           = TERBUKA ∧ hari ini <= jatuh tempo efektif <= hari ini + N  (N = pengingat.ambang_segera tenant, V450,
#                            bawaan 7; SATU definisi "segera jatuh tempo" dgn faktur/proforma/pengingat dashboard -- putusan pemilik 22 Okt,
#                            KONTRAK-dashboard-pengingat §7; dulu `jatuh_tempo_minggu_ini` = s.d. akhir minggu)
#   (terbuka di luar N hari / lunas / batal = bukan aksi). Jatuh tempo efektif = due_date, kosong -> issue_date (== faktur_aksi).
# "Hari ini" = tanggal BISNIS tenant. Python `aksi_tagihan` dan SQL `sql_aksi` WAJIB setara (uji berpasangan + harness nyata).
# TRANSISI (23 Okt 2026, aditif): `jatuh_tempo_minggu_ini` = ALIAS USANG `segera_tempo` -- diterima di ?aksi=, dikirim di /summary
# ({k}_count/_value == segera_tempo) dan TETAP nilai `aksi`/`aksi_semua` baris (FE r304 memetakan label dari kunci lama) sampai rilis
# FE yang memakai `segera_tempo` TERVERIFIKASI; lalu alias dicabut dan baris memakai `segera_tempo`.
AKSI_TAGIHAN = ("lewat_jatuh_tempo", "segera_tempo", "perlu_diposting")
AKSI_USANG = {"jatuh_tempo_minggu_ini": "segera_tempo"}       # masukan ?aksi= / kunci summary lama -> kunci kanonik
AKSI_BARIS_USANG = {"segera_tempo": "jatuh_tempo_minggu_ini"}  # nilai `aksi` baris selama transisi FE
TERBUKA = ("unpaid", "partial", "overdue")
# SARING umur (KONTRAK-dashboard-T2 §5, 23 Okt 2026): umur_current|umur_1_30|umur_31_60|umur_61_90|umur_90_plus = TERBUKA ∧ umur jatuh tempo
# efektif (jatuh_tempo.kelas_umur / sql_umur -- SATU rumus dgn faktur & dashboard aging). Kunci SARING ?aksi= + /summary {k}_count/_value
# (nilai = sisa jurnal), BUKAN pill: partisi AKSI_TAGIHAN dan nilai `aksi` baris tak berubah. Σ umur_* == tagihan_terbuka.
SARING_UMUR = UMUR_KUNCI
POSISI_TAGIHAN = ("Draf", "Batal", "Lunas", "Sebagian", "Belum lunas")  # == kata posisi_faktur Penjualan


def _bulan():
    from .so_posisi import BULAN
    return BULAN


def sql_terbuka(a: str = "b") -> str:
    """Setara keadaan_tagihan(...) IN TERBUKA (butuh join_rincian): aktif, ber-kredit AP efektif, sisa jurnal > 0."""
    return f"(NOT {_sql_void(a)} AND NOT {_sql_draft(a)} AND NOT {_sql_tanpa_kredit()} AND apr.outstanding > 0)"


def sql_jt_efektif(a: str = "b") -> str:
    return f"COALESCE({a}.due_date, {a}.issue_date)"


def sql_aksi(aksi: str, p_hari: str, p_ambang: str, a: str = "b") -> str:
    """Predikat SQL setara `aksi_tagihan(...) == aksi` (butuh join_rincian). p_hari/p_ambang = placeholder (hari ini, N hari segera)."""
    eff = sql_jt_efektif(a)
    if aksi in SARING_UMUR:
        return f"({sql_terbuka(a)} AND {sql_umur(aksi, eff, p_hari)})"
    return {
        "perlu_diposting": _sql_draft(a),
        "lewat_jatuh_tempo": f"({sql_terbuka(a)} AND {eff} < {p_hari}::date)",
        "segera_tempo": f"({sql_terbuka(a)} AND {eff} >= {p_hari}::date AND {eff} <= {p_hari}::date + {p_ambang}::int)",
    }[AKSI_USANG.get(aksi, aksi)]


def sql_aksi_berparam(aksi: str, mulai: int, hari_ini: date, ambang: Optional[int] = None) -> tuple:
    """(predikat, parameter) mulai $mulai -- hanya parameter yang dirujuk (asyncpg menolak $N tak terpakai).
    `segera_tempo` (atau alias usang) WAJIB menerima ambang N tenant (pengingat.ambang_segera) -- tanpa itu = galat, bukan tebakan."""
    aksi = AKSI_USANG.get(aksi, aksi)
    if aksi == "segera_tempo" and ambang is None:
        raise ValueError("segera_tempo butuh ambang (pengingat.ambang_segera)")
    pakai = (hari_ini,) if aksi in SARING_UMUR else {"perlu_diposting": (), "lewat_jatuh_tempo": (hari_ini,),
                                                     "segera_tempo": (hari_ini, ambang)}[aksi]
    return sql_aksi(aksi, f"${mulai}", f"${mulai + 1}"), list(pakai)


def sql_hutang(a: str = "b") -> str:
    """Sisa hutang kolom `outstanding_amount` (== sisa_baris Penjualan): draf/batal/tanpa kredit AP = NULL, lainnya sisa jurnal."""
    return f"(CASE WHEN {_sql_void(a)} OR {_sql_draft(a)} OR {_sql_tanpa_kredit()} THEN NULL ELSE apr.outstanding END)"


def aksi_tagihan(keadaan: str, due_date: Optional[date], issue_date: Optional[date], hari_ini: date, ambang: int) -> Optional[str]:
    """Kunci aksi KANONIK satu tagihan (atau None). keadaan = keadaan_tagihan / kolom status daftar; ambang = pengingat.ambang_segera."""
    if keadaan == "draft":
        return "perlu_diposting"
    if keadaan not in TERBUKA:
        return None
    eff = due_date if due_date is not None else issue_date
    if eff is None:
        return None
    if eff < hari_ini:
        return "lewat_jatuh_tempo"
    if eff <= hari_ini + timedelta(days=ambang):
        return "segera_tempo"
    return None


def umur_tagihan(keadaan: str, due_date: Optional[date], issue_date: Optional[date], hari_ini: date) -> Optional[str]:
    """Kembaran Python sql_aksi(umur_*): kunci umur tagihan TERBUKA (sisa jurnal > 0) dari jatuh tempo efektif, atau None."""
    if keadaan not in TERBUKA:
        return None
    return kelas_umur(due_date if due_date is not None else issue_date, hari_ini)


def posisi_tagihan(keadaan: str, dibayar) -> str:
    """Teks Posisi (baris, detail, jangkar bundel): Draf · Batal · Lunas · Sebagian (terbayar > 0, sisa > 0) · Belum lunas.
    Telat BUKAN posisi (ada di jatuh_tempo_keadaan; pola NetSuite Status vs Days Overdue, == posisi_faktur Penjualan)."""
    if keadaan == "draft":
        return "Draf"
    if keadaan == "void":
        return "Batal"
    if keadaan == "paid":
        return "Lunas"
    return "Sebagian" if _d(dibayar) > 0 else "Belum lunas"


def jatuh_tempo_fakta(keadaan: str, due_date: Optional[date], hari_ini: date, ambang: int) -> dict:
    """jatuh_tempo_terbuka/_hari/_keadaan/_teks/_minggu_ini (bentuk == proforma_daftar.jatuh_tempo_fakta, kata == faktur Penjualan).
    terbuka = keadaan TERBUKA ∧ bertanggal; keadaan: telat | hari_ini | akan | selesai (lunas bertanggal; tanggal redup) | tanpa."""
    tgl = f"{due_date.day} {_bulan()[due_date.month - 1]}" if due_date is not None else None
    terbuka = keadaan in TERBUKA and due_date is not None
    hari = (due_date - hari_ini).days if terbuka else None
    if terbuka:
        kd = "telat" if hari < 0 else "hari_ini" if hari == 0 else "akan"
        teks = f"{tgl}, lewat tempo" if hari < 0 else "Hari ini" if hari == 0 else "Besok" if hari == 1 else tgl
    elif keadaan == "paid" and due_date is not None:
        kd, teks = "selesai", tgl
    else:
        kd, teks = "tanpa", "—"
    return {"jatuh_tempo_terbuka": terbuka, "jatuh_tempo_hari": hari, "jatuh_tempo_keadaan": kd, "jatuh_tempo_teks": teks,
            # USANG: alias segera_tempo (N hari tenant), == aksi baris segera_tempo
            "jatuh_tempo_minggu_ini": terbuka and hari_ini <= due_date <= hari_ini + timedelta(days=ambang)}


def fakta_baris(keadaan: str, dibayar, hutang, due_date: Optional[date], issue_date: Optional[date], hari_ini: date, ambang: int) -> dict:
    """SEMUA medan turunan kontrak untuk satu tagihan -- SATU fungsi untuk baris daftar DAN detail v1/v2 (paritas).
    `aksi` baris = kunci kanonik, kecuali segera_tempo -> alias usang selama transisi FE (AKSI_BARIS_USANG)."""
    ak = aksi_tagihan(keadaan, due_date, issue_date, hari_ini, ambang)
    ak = AKSI_BARIS_USANG.get(ak, ak)
    return {"aksi": ak, "aksi_semua": [ak] if ak else [],
            "position_text": posisi_tagihan(keadaan, dibayar),
            "outstanding_amount": None if hutang is None else f"{_d(hutang):.2f}",
            **jatuh_tempo_fakta(keadaan, due_date, hari_ini, ambang)}


def syarat_cari(search: str, mulai: int, a: str = "b") -> tuple:
    """Predikat ?search= (SATU sumber daftar & /summary?search=). Tiap kata wajib cocok salah satu: nomor, nama vendor, ref_no, catatan,
    nama/kode vendor master. -> (predikat, parameter); hanya spasi = TRUE."""
    kata = (search or "").strip().split()
    if not kata:
        return "TRUE", []
    syarat = [f"({a}.invoice_number ILIKE ${mulai + i} OR {a}.vendor_name ILIKE ${mulai + i} OR {a}.ref_no ILIKE ${mulai + i} "
              f"OR {a}.notes ILIKE ${mulai + i} OR {a}.vendor_id IN (SELECT v.id FROM vendors v WHERE v.tenant_id = {a}.tenant_id "
              f"AND (v.name ILIKE ${mulai + i} OR v.code ILIKE ${mulai + i})))" for i in range(len(kata))]
    return (syarat[0] if len(syarat) == 1 else f"({' AND '.join(syarat)})"), [f"%{w}%" for w in kata]


# ---- Bundel dokumen berjangkar TAGIHAN (GET /bills/{id}/documents) + documents[] baris = turunannya ---------------------------------
# Kelompok (urutan bundel PO, kind K3 kontrak PO): po (PO asal: bills.purchase_order_id ATAU baris bertaut) · faktur (tagihan ITU
# SENDIRI; draf/batal tampil sebagai dirinya) · uang_muka (UM vendor tak void yang DITERAPKAN ke tagihan ini; amount = bagian diterapkan) ·
# bayar (Pembayaran Keluar posted beralokasi ke tagihan ini; amount = Σ alokasi ke tagihan ini) · nota_debit (ND bertaut
# original_bill_id, bukan draf/void). Filter tenant EKSPLISIT tiap kueri.
def _tgl(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, date) else (str(v)[:10] if v else None)


def _uang(v) -> str:
    return f"{_d(v):.2f}"


def _belum_kirim() -> dict:
    return {"state": "none", "sent_at": None, "viewed_at": None}


def sql_keadaan_dok(a: str = "b") -> str:
    """Keadaan dokumen bundel TANPA tanggal (telat tetap 'diposting'): void | draft | paid | terbuka. Cabang SAMA dengan sql_keadaan."""
    return (f"(CASE WHEN {_sql_void(a)} THEN 'void' WHEN {_sql_draft(a)} THEN 'draft' WHEN {_sql_tanpa_kredit()} THEN 'void' "
            f"WHEN apr.outstanding <= 0 THEN 'paid' ELSE 'terbuka' END)")


def dok_tagihan(b, keadaan: str) -> dict:
    """Dokumen tagihan: status draf | batal | lunas | diposting (== kata faktur Penjualan); paid = keadaan paid."""
    st = {"draft": "draf", "void": "batal", "paid": "lunas"}.get(keadaan, "diposting")
    return {"kind": "bill", "id": str(b["id"]), "number": b["invoice_number"], "date": _tgl(b["issue_date"]), "amount": _uang(b["amount"]),
            "paid": keadaan == "paid", "paper": "A4", "sent": _belum_kirim(), "batal": keadaan == "void", "status": st}


async def kelompok_tagihan(conn, tenant_id: str, bill_ids: list) -> dict:
    """{bill_id: [kelompok]} -- pembentuk SATU tempat untuk bundel dan documents[] baris (<= 100 id, kueri ber-batch)."""
    from .po_daftar import dok_purchase_order, dok_vendor_deposit
    if not bill_ids:
        return {}
    ids = list(bill_ids)
    bills = {r["id"]: r for r in await conn.fetch(
        f"""SELECT b.id, b.invoice_number, b.issue_date, b.amount, b.purchase_order_id, {sql_keadaan_dok()} AS keadaan
            FROM bills b {join_rincian()} WHERE b.tenant_id = $1 AND b.id = ANY($2::uuid[])""", tenant_id, ids)}
    po_bill: dict = {}
    for r in await conn.fetch(
        """SELECT DISTINCT b.id AS bill_id, po.id, po.po_number, po.po_date, po.total_amount, po.status, po.sent_at
           FROM bills b
           JOIN purchase_orders po ON po.tenant_id = $1 AND (po.id = b.purchase_order_id OR po.id IN (
                SELECT i.po_id FROM bill_items bi JOIN purchase_order_items i ON i.id = bi.purchase_order_item_id WHERE bi.bill_id = b.id))
           WHERE b.tenant_id = $1 AND b.id = ANY($2::uuid[]) ORDER BY po.po_date, po.po_number""", tenant_id, ids):
        po_bill.setdefault(r["bill_id"], []).append(r)
    um: dict = {}
    for r in await conn.fetch(
        """SELECT vda.bill_id, vd.id, vd.deposit_number, vd.deposit_date, vd.status, SUM(vda.amount) AS amount
           FROM vendor_deposit_applications vda JOIN vendor_deposits vd ON vd.id = vda.vendor_deposit_id AND vd.tenant_id = $1
           WHERE vda.bill_id = ANY($2::uuid[]) AND vd.status <> 'void' AND vda.status = 'active'  -- V437: dilepas = 'reversed' (BACKEND2)
           GROUP BY vda.bill_id, vd.id, vd.deposit_number, vd.deposit_date, vd.status ORDER BY vd.deposit_date, vd.deposit_number""",
            tenant_id, ids):
        um.setdefault(r["bill_id"], []).append(dok_vendor_deposit(r))
    bayar: dict = {}
    for r in await conn.fetch(
        """SELECT bpa.bill_id, bp.id, bp.payment_number, bp.payment_date, SUM(bpa.amount_applied) AS jumlah
           FROM bill_payment_allocations bpa JOIN bill_payments_v2 bp ON bp.id = bpa.payment_id AND bp.tenant_id = $1 AND bp.status = 'posted'
           WHERE bpa.bill_id = ANY($2::uuid[])
           GROUP BY bpa.bill_id, bp.id, bp.payment_number, bp.payment_date ORDER BY bp.payment_date, bp.payment_number""", tenant_id, ids):
        bayar.setdefault(r["bill_id"], []).append(
            {"kind": "payment_out", "id": str(r["id"]), "number": r["payment_number"], "date": _tgl(r["payment_date"]),
             "amount": _uang(r["jumlah"]), "paper": "A5-landscape", "sent": _belum_kirim(), "batal": False, "status": "dibayar"})
    nd: dict = {}
    for r in await conn.fetch(
        """SELECT original_bill_id AS bill_id, id, credit_number, credit_date, total_amount FROM vendor_credits
           WHERE tenant_id = $1 AND original_bill_id = ANY($2::uuid[]) AND status NOT IN ('draft', 'void')
           ORDER BY credit_date, credit_number""", tenant_id, ids):
        nd.setdefault(r["bill_id"], []).append(
            {"kind": "debit_note", "id": str(r["id"]), "number": r["credit_number"], "date": _tgl(r["credit_date"]),
             "amount": _uang(r["total_amount"]), "paper": "A4", "sent": _belum_kirim(), "batal": False, "status": "diposting"})
    hasil = {}
    for bid in ids:
        b = bills.get(bid)
        if not b:
            continue
        kel = []
        if po_bill.get(bid):
            kel.append({"key": "po", "label": "Pesanan Pembelian", "docs": [dok_purchase_order(p) for p in po_bill[bid]]})
        kel.append({"key": "faktur", "label": "Faktur Pembelian", "docs": [dok_tagihan(b, b["keadaan"])]})
        if um.get(bid):
            kel.append({"key": "uang_muka", "label": "Uang Muka", "docs": um[bid]})
        if bayar.get(bid):
            kel.append({"key": "bayar", "label": "Pembayaran", "docs": bayar[bid]})
        if nd.get(bid):
            kel.append({"key": "nota_debit", "label": "Nota Debit", "docs": nd[bid]})
        hasil[bid] = kel
    return hasil


def documents_baris(kelompok: list) -> list:
    """documents[{type}] baris daftar = dokumen bundel (urut kelompok); type = kind."""
    return [{"type": d["kind"]} for g in kelompok for d in g["docs"]]
