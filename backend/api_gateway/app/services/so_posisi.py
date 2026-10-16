"""Posisi, Kirim, dan jumlah dokumen per Pesanan Penjualan untuk DAFTAR (P1 SO-dokumen, 1 Okt 2026).

Spek: docs/so-dokumen/02-DATA-DAN-API.md §Posisi/§Kirim + 01-PERUBAHAN.md §A, dengan PUTUSAN PEMILIK 1 Okt
(P0-AUDIT.md): zona WIB (zona tenant), Kirim "—" bila tak ada baris perlu-kirim, "Terkirim" bila semua terkirim,
proforma PELUNASAN tak memicu aturan 4 bila faktur sudah ada (-> "Menunggu pelunasan"), cicilan = fase 2.

SATU tempat aturannya: `posisi()` dan `kirim()` MURNI (diuji per aturan); `fakta_daftar()` mengumpulkan faktanya
dalam kueri ber-batch (daftar <= 100 SO) dari sumber yang SUDAH ADA -- tanpa rumus uang baru (Iron Law 1/16/29):
  sisa SO = total − ringkasan_pesanan.tertutup (journal-derived; sama dengan payment_summary/P0)
  proforma terbayar = proforma_terbayar.terbayar_proforma (atribusi kanonik, sama dengan detail proforma & Rekap)
  perlu-kirim = COALESCE(soi.perlu_kirim, products.track_inventory, false) (= ringkasan_menunggu_kirim, V318)
  terkirim = Surat Jalan AKTIF per baris (so_kirim.terkirim_per_baris)
Filter tenant EKSPLISIT di tiap kueri (set_config bukan pagar).
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .proforma_terbayar import ringkasan_pesanan, terbayar_proforma
from .so_fase_lokasi import STATUS_TERTUTUP as _FASE_TUTUP, teks_fase
from .so_kirim import _AKTIF, belum_dikirim, terkirim_per_baris

NOL = Decimal("0")
SELESAI_KIRIM = ("completed", "cancelled")  # putusan pemilik #8: Kirim "—"
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def tagihan_pertama(belum_bayar: list, ada_faktur: bool):
    """Proforma issued belum lunas yang PERTAMA berlaku untuk Posisi (urut issued_at), atau None.
    Putusan pemilik #3: proforma PELUNASAN dilewati bila faktur sudah ada (tagihan faktur yang sama -> aturan 5)."""
    for p in belum_bayar:
        if p["purpose"] == "PELUNASAN" and ada_faktur:
            continue
        return p
    return None


def menunggu_dp(status: str, sisa: Decimal, belum_bayar: list, ada_faktur: bool) -> bool:
    """AKSI-SO-2 (14 Okt 2026): fakta di balik Posisi "Menunggu DP" TANPA fase lokasi (fase hanya mengganti TEKS
    kolom). SATU sumber untuk posisi() DAN pill so_aksi `menunggu_dp` (pola NetSuite Pending Deposit)."""
    if status == "cancelled" or sisa <= NOL:
        return False
    p = tagihan_pertama(belum_bayar, ada_faktur)
    return p is not None and p["purpose"] == "DP"


def posisi(status: str, sisa: Decimal, belum_bayar: list, ada_faktur: bool, sisa_faktur: Decimal,
           ada_surat_jalan: bool, dp_diterima: Decimal, fase_teks=None) -> tuple:
    """-> (teks, muted). Aturan 02 §Posisi, yang PERTAMA cocok.

    belum_bayar: proforma issued yang belum lunas, urut issued_at: [{"purpose", "termin_ke"}].
    Aturan 6b (putusan pemilik 1 Okt): ada faktur, faktur lunas, sisa SO > 0, tanpa uang muka -> "Sebagian ditagih".
    Aturan 3 (cicilan) = fase 2, tidak aktif.
    """
    if status == "cancelled":
        return "Batal", True
    if sisa <= NOL:
        return "Lunas", True
    if fase_teks:  # 7 Okt 2026: fase lokasi ("Tersedia di Toko Manado") di depan aturan pembayaran; Batal/Lunas tetap menang
        return fase_teks, False
    if menunggu_dp(status, sisa, belum_bayar, ada_faktur):
        return "Menunggu DP", False
    p = tagihan_pertama(belum_bayar, ada_faktur)
    if p is not None:
        if p["purpose"] == "TERMIN":
            return f"Menunggu termin {p['termin_ke']}", False
        return "Menunggu tagihan", False
    if ada_faktur and sisa_faktur > NOL:
        return "Menunggu pelunasan", False
    if ada_surat_jalan and not ada_faktur:
        return "Perlu faktur", False
    if ada_faktur and dp_diterima <= NOL:
        # #6b putusan pemilik 1 Okt 2026 (lewat MASTER, "Ok ikut rekomendasi"): SO sebagian difaktur, faktur LUNAS
        # (aturan 5 tak cocok), sisa SO belum ditagih, TANPA uang muka. Dulu jatuh ke "Belum ditagih" (aturan 8)
        # padahal sudah ada tagihan dibayar (kaos SO-2609-0333). Dengan uang muka -> tetap "Diproses" (aturan 7).
        return "Sebagian ditagih", False
    if dp_diterima > NOL:
        return "Diproses", False  # 3 Okt putusan pemilik: generik (juga reseller), dulu "Produksi"
    return "Belum ditagih", False


def kirim(status: str, tanggal_kirim, perlu_kirim: bool, semua_terkirim: bool, hari: date) -> tuple:
    """-> (teks, gaya 'normal'|'muted'|'strong'). 01 §A + putusan pemilik #2 dan #8; `hari` = tanggal usaha zona tenant.

    #8 (1 Okt 2026): SO SELESAI (completed -- juga hasil "Tutup pesanan") atau batal -> "—". SO yang masih TERBUKA
    (termasuk Lunas tapi belum selesai) dengan baris perlu-kirim belum terkirim tetap "telat" tebal = sinyal nyata.
    """
    if status in SELESAI_KIRIM or not perlu_kirim:
        return "—", "muted"
    if semua_terkirim:
        return "Terkirim", "muted"
    if tanggal_kirim is None:
        return "—", "muted"
    teks = f"{tanggal_kirim.day} {BULAN[tanggal_kirim.month - 1]}"
    if tanggal_kirim == hari:
        return "Hari ini", "strong"
    if tanggal_kirim == hari + timedelta(days=1):
        return "Besok", "normal"
    if tanggal_kirim < hari:
        return f"{teks}, telat", "strong"
    return teks, "normal"


def kirim_hari(status: str, tanggal_kirim, perlu_kirim: bool, semua_terkirim: bool, hari: date) -> tuple:
    """LISTPAGE-DATA (16 Okt 2026): -> (kirim_hari, kirim_terbuka) dari fakta YANG SAMA dengan kirim().
    terbuka = kirim() masih menampilkan tanggal (bukan "—"/"Terkirim" karena selesai/batal/tanpa baris kirim/semua
    terkirim). kirim_hari = tanggal_kirim - hari (hari kalender, `hari` = tanggal usaha zona tenant); None bila tertutup
    atau tanpa tanggal. <0 == teks ", telat"; 0 == "Hari ini"; 1 == "Besok". FE hanya memformat."""
    terbuka = status not in SELESAI_KIRIM and perlu_kirim and not semua_terkirim
    if not terbuka or tanggal_kirim is None:
        return None, terbuka
    return (tanggal_kirim - hari).days, True


# Urutan tampil tooltip Dok. (paket listpage-polish 03 §3): Penawaran · Proforma · Kwitansi · Surat jalan · Faktur.
# Kunci = `kind` panel /documents (routers/dokumen.susun_dokumen).
URUT_DOK_SO = ("quotation", "proforma", "receipt", "delivery", "invoice")


def dokumen_so(ada_penawaran: bool, n_proforma: int, n_kwitansi: int, n_sj: int, n_faktur: int) -> list:
    """-> [{type}] satu per dokumen, urut URUT_DOK_SO. doc_count = len(dokumen_so(...)) (satu sumber)."""
    n = {"quotation": 1 if ada_penawaran else 0, "proforma": n_proforma, "receipt": n_kwitansi,
         "delivery": n_sj, "invoice": n_faktur}
    return [{"type": k} for k in URUT_DOK_SO for _ in range(n[k])]


SQL_PROFORMA_SO = """SELECT id, sales_order_id, proforma_number, purpose, amount, status, issued_at
           FROM proformas WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[])"""
SQL_JUMLAH_FAKTUR = """SELECT sales_order_id AS so_id, count(*) AS n FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = ANY($2::uuid[]) AND status NOT IN ('draft', 'void')
           GROUP BY 1"""


def _urut_terbit(p):
    return (p["issued_at"] is None, p["issued_at"], p["proforma_number"] or "")


def belum_bayar_so(daftar_pf: list, terbayar: dict) -> list:
    """Proforma issued yang belum lunas (terbayar_proforma kanonik), urut issued_at: [{"purpose", "termin_ke"}]."""
    issued = sorted((p for p in daftar_pf if p["status"] == "issued"), key=_urut_terbit)
    termin = [p["id"] for p in sorted((p for p in daftar_pf if p["status"] != "cancelled" and p["purpose"] == "TERMIN"),
                                      key=_urut_terbit)]
    return [{"purpose": p["purpose"], "termin_ke": (termin.index(p["id"]) + 1) if p["id"] in termin else None}
            for p in issued if terbayar.get(p["id"], {}).get("paid", NOL) < Decimal(str(p["amount"]))]


DEPOSIT_TAK_HIDUP = ("void", "draft", "refunded")   # = dashboard so_dp_belum lama: uang muka "hidup" = selain ini
PROFORMA_BELUM_TERBIT = ("draft", "cancelled")       # proforma selain ini = tagihan SUDAH dikirim ke pelanggan
SO_TAK_BERJALAN = ("draft", "cancelled", "completed")  # = so_agregat.AKTIF_TIDAK (tanpa impor: so_agregat -> proforma_terbayar)
SQL_DEPOSIT_HIDUP_SO = """SELECT DISTINCT COALESCE(cd.sales_order_id, pf.sales_order_id) AS so_id
           FROM customer_deposits cd
           LEFT JOIN proformas pf ON pf.id = cd.proforma_id AND pf.tenant_id = cd.tenant_id
           WHERE cd.tenant_id = $1 AND COALESCE(cd.sales_order_id, pf.sales_order_id) = ANY($2::uuid[])
             AND cd.status <> ALL($3::text[])"""


def punya_term_dp(dp_percent, dp_amount) -> bool:
    return Decimal(str(dp_percent or 0)) > NOL or Decimal(str(dp_amount or 0)) > NOL


def dp_wajib(total, dp_percent, dp_amount) -> Decimal:
    """DP menurut SYARAT SO: dp_amount bila > 0, selain itu total x dp_percent / 100 (2 desimal, half-up = ROUND SQL
    kartu lama). Angka tampilan, bukan saldo (tak menulis apa pun)."""
    if Decimal(str(dp_amount or 0)) > NOL:
        return Decimal(str(dp_amount))
    return (Decimal(str(total or 0)) * Decimal(str(dp_percent or 0)) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)


def perlu_minta_dp(status: str, punya_term: bool, ada_proforma_terbit: bool, ada_deposit_hidup: bool,
                   ada_faktur: bool, sisa: Decimal) -> bool:
    """PERLU-MINTA-DP (14 Okt 2026, MASTER): SO berjalan bersyarat DP yang BELUM ditagih sama sekali -- tanpa proforma
    terbit APA PUN (PELUNASAN terbit = pelanggan sudah ditagih), tanpa uang muka hidup, tanpa faktur terbit, sisa > 0.
    Pola SAP billing plan: milestone DP jatuh tempo, down payment request belum dibuat; NetSuite: sebelum Pending Deposit."""
    if status in SO_TAK_BERJALAN:
        return False
    return punya_term and not ada_proforma_terbit and not ada_deposit_hidup and not ada_faktur and sisa > NOL


async def fakta_dp(conn, tenant_id: str, rows: list) -> dict:
    """rows: SO berjalan (id, status, total_amount, dp_percent, dp_amount). -> {so_id: (kunci, nilai)} dengan kunci
    'menunggu_dp' (nilai = sisa proforma DP terbit yang belum terbayar, terbayar_proforma kanonik) atau 'perlu_minta_dp'
    (nilai = dp_wajib syarat SO). Keduanya saling lepas. Fakta menunggu_dp SAMA dengan fakta_daftar (Posisi).
    Usaha tanpa syarat DP dan tanpa proforma DP = satu kueri proforma saja."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    pfs = [dict(p) for p in await conn.fetch(SQL_PROFORMA_SO, tenant_id, ids)]
    per_so = {}
    for p in pfs:
        per_so.setdefault(p["sales_order_id"], []).append(p)
    terbit = {p["sales_order_id"] for p in pfs if p["status"] not in PROFORMA_BELUM_TERBIT}
    tunggu = sorted({p["sales_order_id"] for p in pfs if p["status"] == "issued" and p["purpose"] == "DP"})
    # pengecualian proforma terbit/uang muka/faktur HANYA di perlu_minta_dp (satu tempat; kontrol merah 14 Okt)
    minta = sorted(r["id"] for r in rows if r["status"] not in SO_TAK_BERJALAN
                   and punya_term_dp(r.get("dp_percent"), r.get("dp_amount")))
    semua = sorted(set(tunggu) | set(minta))
    if not semua:
        return {}
    rg = await ringkasan_pesanan(conn, tenant_id, semua)
    faktur = {r["so_id"]: r["n"] for r in await conn.fetch(SQL_JUMLAH_FAKTUR, tenant_id, semua)}
    terbayar = await terbayar_proforma(conn, tenant_id, tunggu) if tunggu else {}
    hidup = {r["so_id"] for r in await conn.fetch(SQL_DEPOSIT_HIDUP_SO, tenant_id, minta, list(DEPOSIT_TAK_HIDUP))} if minta else set()
    st = {r["id"]: r for r in rows}
    out = {}
    for sid in semua:
        r = st[sid]
        sisa = Decimal(str(r["total_amount"] or 0)) - rg[sid]["tertutup"]
        ada_faktur = faktur.get(sid, 0) > 0
        if sid in per_so and menunggu_dp(r["status"], sisa, belum_bayar_so(per_so[sid], terbayar), ada_faktur):
            out[sid] = ("menunggu_dp", sum((max(Decimal(str(p["amount"])) - terbayar.get(p["id"], {}).get("paid", NOL), NOL)
                                            for p in per_so[sid] if p["status"] == "issued" and p["purpose"] == "DP"), NOL))
        elif sid in minta and perlu_minta_dp(r["status"], True, sid in terbit, sid in hidup, ada_faktur, sisa):
            out[sid] = ("perlu_minta_dp", dp_wajib(r["total_amount"], r.get("dp_percent"), r.get("dp_amount")))
    return out


async def fakta_daftar(conn, tenant_id: str, rows: list, hari: date) -> dict:
    """rows: baris sales_orders (id, status, total_amount, expected_ship_date, quote_id).
    -> {str(so_id): {position_text, position_muted, ship_text, ship_style, doc_count}}."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    rg = await ringkasan_pesanan(conn, tenant_id, ids)

    pfs = [dict(p) for p in await conn.fetch(SQL_PROFORMA_SO, tenant_id, ids)]
    # Terbayar per proforma = SATU sumber kanonik (terbayar_proforma: atribusi uang muka tertaut/tercocok + kolam
    # pesanan tanpa uang muka di luar tagihan) -- sama dengan detail proforma & Rekap. Dulu P1 memakai alokasikan
    # dengan eksplisit = Σ uang muka ber-proforma_id saja (versi ringkas; diukur 1 Okt: lihat commit).
    terbayar = await terbayar_proforma(conn, tenant_id, ids) if pfs else {}
    faktur = {r["so_id"]: r["n"] for r in await conn.fetch(SQL_JUMLAH_FAKTUR, tenant_id, ids)}
    sj = {r["so_id"]: r["n"] for r in await conn.fetch(
        f"""SELECT x.so_id, count(DISTINCT x.fid) AS n FROM (
                SELECT si.sales_order_id AS so_id, f.id AS fid
                FROM invoice_fulfillments f JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
                WHERE si.tenant_id = $1 AND si.sales_order_id = ANY($2::uuid[]) AND {_AKTIF}
                UNION ALL
                SELECT f.sales_order_id, f.id FROM invoice_fulfillments f
                WHERE f.tenant_id = $1 AND f.asal = 'so' AND f.sales_order_id = ANY($2::uuid[]) AND {_AKTIF}
            ) x GROUP BY 1""",
        tenant_id, ids,
    )}
    baris = await conn.fetch(
        """SELECT soi.id, soi.sales_order_id AS so_id, soi.quantity
           FROM sales_order_items soi
           JOIN sales_orders so ON so.id = soi.sales_order_id AND so.tenant_id = $1
           LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id
           WHERE soi.sales_order_id = ANY($2::uuid[]) AND COALESCE(soi.perlu_kirim, p.track_inventory, false)""",
        tenant_id, ids,
    )
    per_baris, _ = await terkirim_per_baris(conn, tenant_id, ids)
    # Kwitansi = himpunan yang SAMA dengan kelompok "Pembayaran" panel /documents (routers/dokumen.susun_dokumen):
    # uang muka tertaut ber-jurnal + uang muka lepas yang diterapkan ke faktur SO + penerimaan atas faktur SO,
    # dihitung per nomor unik. Dulu uang muka dihitung tanpa syarat jurnal & tanpa yang diterapkan (gerbang 05 D3).
    kw = {r["so_id"]: r["n"] for r in await conn.fetch(
        """SELECT so_id, count(DISTINCT id) AS n FROM (
               SELECT COALESCE(cd.sales_order_id, p.sales_order_id) AS so_id, cd.id
               FROM customer_deposits cd
               LEFT JOIN proformas p ON p.id = cd.proforma_id AND p.tenant_id = cd.tenant_id
               WHERE cd.tenant_id = $1 AND cd.status <> 'void' AND cd.journal_id IS NOT NULL
                 AND COALESCE(cd.sales_order_id, p.sales_order_id) = ANY($2::uuid[])
               UNION
               SELECT si.sales_order_id, cd.id
               FROM customer_deposit_applications cda
               JOIN customer_deposits cd ON cd.id = cda.deposit_id AND cd.tenant_id = $1
               JOIN sales_invoices si ON si.id = cda.invoice_id AND si.tenant_id = $1
               WHERE cda.tenant_id = $1 AND cda.status = 'active' AND cd.status <> 'void'
                 AND cd.journal_id IS NOT NULL AND si.sales_order_id = ANY($2::uuid[])
               UNION
               SELECT si.sales_order_id, rpa.payment_id
               FROM receive_payment_allocations rpa
               JOIN receive_payments rp ON rp.id = rpa.payment_id AND rp.tenant_id = $1 AND rp.status = 'posted'
               JOIN sales_invoices si ON si.id = rpa.invoice_id AND si.tenant_id = $1
               WHERE si.sales_order_id = ANY($2::uuid[]) AND rpa.status = 'active'
           ) x GROUP BY 1""",
        tenant_id, ids,
    )}

    # 7 Okt 2026: nama gudang fase lokasi (satu kueri, hanya bila ada SO berfase; tenant eksplisit)
    gid = list({r.get("fase_gudang_id") for r in rows if r.get("fase_gudang_id")})
    nama_gudang = {g["id"]: g["name"] for g in await conn.fetch(
        "SELECT id, name FROM warehouses WHERE tenant_id = $1 AND id = ANY($2::uuid[])", tenant_id, gid)} if gid else {}

    per_so_pf, per_so_baris = {}, {}
    for p in pfs:
        per_so_pf.setdefault(p["sales_order_id"], []).append(p)
    for b in baris:
        per_so_baris.setdefault(b["so_id"], []).append(b)

    hasil = {}
    for r in rows:
        sid = r["id"]
        o = rg[sid]
        sisa = Decimal(str(r["total_amount"] or 0)) - o["tertutup"]
        daftar_pf = per_so_pf.get(sid, [])
        belum_bayar = belum_bayar_so(daftar_pf, terbayar)
        n_faktur = faktur.get(sid, 0)
        fase = r.get("fase_lokasi") if r["status"] not in _FASE_TUTUP else None
        nama_g = nama_gudang.get(r.get("fase_gudang_id")) if fase else None
        teks_pos, muted = posisi(r["status"], sisa, belum_bayar, n_faktur > 0, o["invoice_outstanding"],
                                 sj.get(sid, 0) > 0, o["dp_received"], teks_fase(fase, nama_g))
        bk = per_so_baris.get(sid, [])
        semua = bool(bk) and all(belum_dikirim(b["quantity"], per_baris.get(b["id"])) == NOL for b in bk)
        teks_kirim, gaya = kirim(r["status"], r["expected_ship_date"], bool(bk), semua, hari)
        k_hari, k_buka = kirim_hari(r["status"], r["expected_ship_date"], bool(bk), semua, hari)
        # Sama dengan panel: proforma draf tak tampil; proforma batal tampil hanya bila SO batal (cap DIBATALKAN).
        dok = dokumen_so(bool(r["quote_id"]),
                         sum(1 for p in daftar_pf if p["status"] != "draft"
                             and (p["status"] != "cancelled" or r["status"] == "cancelled")),
                         kw.get(sid, 0), sj.get(sid, 0), n_faktur)
        hasil[str(sid)] = {"position_text": teks_pos, "position_muted": muted,
                           "ship_text": teks_kirim, "ship_style": gaya, "doc_count": len(dok), "documents": dok,
                           "kirim_hari": k_hari, "kirim_terbuka": k_buka,
                           # "Sisa" (7 Okt): total - ringkasan_pesanan.tertutup (journal-derived, Law 1/16/29), tak negatif; draf/batal = null
                           "outstanding_amount": None if r["status"] in ("draft", "cancelled") else float(max(sisa, NOL)),
                           "fase_lokasi": fase, "fase_gudang_id": str(r["fase_gudang_id"]) if fase and r.get("fase_gudang_id") else None,
                           "fase_gudang_nama": nama_g}
    return hasil


async def urut_outstanding(conn, tenant_id: str, rows: list, arah: str = "desc") -> list:
    """sort_by=outstanding (7 Okt 2026): id SO terurut menurut "Sisa" (total - ringkasan_pesanan.tertutup, sumber SAMA dengan kolom
    outstanding_amount). rows = {id, total_amount, status} SEMUA yang cocok filter (urut dasar deterministik dari pemanggil);
    draf/batal (tanpa sisa) selalu di ujung. Ringkasan dihitung ber-batch 200."""
    nilai = await sisa_per_so(conn, tenant_id, rows)
    ada = [r["id"] for r in rows if nilai[r["id"]] is not None]
    tak = [r["id"] for r in rows if nilai[r["id"]] is None]
    ada.sort(key=lambda i: nilai[i], reverse=(arah == "desc"))   # sort stabil: seri mengikuti urutan dasar
    return ada + tak


async def sisa_per_so(conn, tenant_id: str, rows: list) -> dict:
    """"Sisa" per SO = total - ringkasan_pesanan.tertutup (journal-derived, Law 1/16/29), tak negatif; draf/batal = None.
    SATU sumber untuk kolom outstanding_amount (fakta_daftar), sort_by=outstanding dan sum_sisa daftar.
    rows = {id, total_amount, status}; ringkasan ber-batch 200."""
    nilai = {}
    for i in range(0, len(rows), 200):
        potong = rows[i:i + 200]
        rg = await ringkasan_pesanan(conn, tenant_id, [r["id"] for r in potong])
        for r in potong:
            nilai[r["id"]] = (None if r["status"] in ("draft", "cancelled")
                              else max(Decimal(str(r["total_amount"] or 0)) - rg[r["id"]]["tertutup"], NOL))
    return nilai


async def jumlah_daftar(conn, tenant_id: str, rows: list) -> dict:
    """LISTPAGE-DATA (16 Okt 2026): agregat baris total daftar atas SEMUA SO yang cocok filter (bukan halaman).
    rows = {id, total_amount, status} dari WHERE yang SAMA dengan COUNT. sum_total = Σ total_amount (semua status, =
    Σ kolom Total); sum_sisa = Σ sisa_per_so (draf/batal tak menyumbang, = Σ kolom Sisa)."""
    sisa = await sisa_per_so(conn, tenant_id, rows)
    return {"sum_total": float(sum((Decimal(str(r["total_amount"] or 0)) for r in rows), NOL)),
            "sum_sisa": float(sum((v for v in sisa.values() if v is not None), NOL))}
