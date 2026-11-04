"""Alokasi pembayaran TERTUA-DULU -- SATU penentu (4 Nov 2026, "bayar banyak faktur", desain disetujui MASTER).

Dipakai: pratinjau /bill-payments + /receive-payments (kolam bill_ids / invoice_ids), pelunasan SO/faktur (so_pelunasan),
re-cap total kartu chat (unified_chat) dan bangun alokasi chat (tool_executor). MURNI: sisa per dokumen dihitung pemanggil
dari fungsi DB (arap Rule 5: compute_ar_outstanding / compute_ap_rincian). Law 9: Decimal, tanpa float di hitungan.
Urutan = jatuh tempo (kosong paling akhir), tanggal dokumen, nomor, id -- pola auto-apply NetSuite/QBO, = urutan open-bills.
"""
from decimal import Decimal

NOL = Decimal("0")


def kunci_tertua(jatuh_tempo, tanggal, nomor, id_) -> tuple:
    """Kunci urut tertua-dulu. str() = tanggal ISO (date maupun teks JSON) terurut benar."""
    return (jatuh_tempo is None, str(jatuh_tempo or ""), str(tanggal or ""), str(nomor or ""), str(id_ or ""))


def selesaikan(baris: list, total) -> list:
    """baris: [{"remaining", "manual"}] SUDAH urut kunci_tertua. manual (bukan None) dipakai apa adanya (batas sisa = penentu
    tulis); baris kolam mendapat (total - jumlah manual) tertua-dulu, dibatasi sisanya. -> applied Decimal per baris."""
    def d(x):
        return Decimal(str(x or 0))

    sisa = d(total) - sum((d(b["manual"]) for b in baris if b.get("manual") is not None), NOL)
    keluar = []
    for b in baris:
        if b.get("manual") is not None:
            keluar.append(d(b["manual"]))
            continue
        r = d(b.get("remaining"))
        a = min(r, sisa) if sisa > 0 and r > 0 else NOL
        keluar.append(a)
        sisa -= a
    return keluar


async def selesaikan_kolam(manual: list, kolam_ids: list, muat, sisa, total) -> tuple:
    """Pratinjau "bayar banyak faktur". manual = [(id, nominal)] dari allocations (dipakai apa adanya); kolam_ids = id tanpa
    nominal (duplikat / yang sudah manual dibuang). muat(ids) -> {id_kecil: {"nomor","jatuh_tempo","tanggal","galat"}} milik
    tenant (galat = (code, pesan) bila bukan milik pihak pembayaran); sisa(id) -> Decimal = sumber yang SAMA dengan validator
    tulis. -> (baris urut tertua [{id, nomor, manual, remaining, applied, source}], galat [(code, pesan)])."""
    kecil = [str(i).strip().lower() for i, _ in manual]
    kolam = []
    for i in kolam_ids:
        k = str(i).strip().lower()
        if k and k not in kecil and k not in kolam:
            kolam.append(k)
    info = await muat(kecil + kolam)
    galat, baris = [], []
    for (i, nominal), k in zip(manual, kecil):
        d = info.get(k) or {}
        baris.append({"id": str(i), "nomor": d.get("nomor"), "manual": Decimal(str(nominal)), "remaining": None,
                      "source": "manual", "_k": kunci_tertua(d.get("jatuh_tempo"), d.get("tanggal"), d.get("nomor"), k)})
    for k in kolam:
        d = info.get(k)
        if not d or d.get("galat"):  # muat mengisi galat untuk id tak ditemukan / pihak lain (kalimat modulnya)
            galat.append(d["galat"] if d else ("DOKUMEN_TIDAK_DITEMUKAN", "Dokumen tidak ditemukan."))
            continue
        baris.append({"id": k, "nomor": d["nomor"], "manual": None, "remaining": max(await sisa(k), NOL), "source": "otomatis",
                      "_k": kunci_tertua(d["jatuh_tempo"], d["tanggal"], d["nomor"], k)})
    baris.sort(key=lambda b: b.pop("_k"))
    for b, a in zip(baris, selesaikan(baris, total)):
        b["applied"] = a
    return baris, galat


def gabung_baris(kolam: list, hasil: list, kunci_id: str, baris_nol) -> list:
    """Respons pratinjau ber-kolam: SEMUA baris urut tertua + source. hasil = baris penentu tulis (alokasi > 0, dicari lewat
    kunci_id); yang tak kebagian -> baris_nol(b) (applied 0, sisa tetap)."""
    per = {str(x[kunci_id]).lower(): x for x in hasil}
    return [{**per[b["id"].lower()], "source": b["source"]} if b["id"].lower() in per else {**baris_nol(b), "source": b["source"]}
            for b in kolam]


def catatan_tanpa_sisa(kolam: list, kode: str) -> list:
    """Baris kolam bersisa 0 (lunas / draf / batal) -> satu catatan."""
    nol = [b["nomor"] for b in kolam if b["source"] == "otomatis" and b["remaining"] <= 0]
    return [{"code": kode, "message": f"Faktur {', '.join(nol)} tanpa sisa tagihan — tidak ikut dibayar."}] if nol else []


def sisa_atau_nol(v) -> Decimal:
    """Sisa dari baris API/DB chat -> Decimal; kosong / bukan angka -> 0 (perilaku loop lama)."""
    from decimal import InvalidOperation
    try:
        return Decimal(str(v or 0))
    except (InvalidOperation, ValueError):
        return NOL


def alokasi_chat(rows: list, total, ident) -> tuple:
    """Bangun alokasi kartu chat (tool_executor). ident(baris) -> (id, nomor, jatuh_tempo, tanggal); rows [{remaining, manual}].
    -> (allocations [{invoice_id, amount_applied}], nomor faktur)."""
    rows = sorted(rows, key=lambda b: kunci_tertua(ident(b)[2], ident(b)[3], ident(b)[1], ident(b)[0]))
    built, nomor = [], []
    for b, a in zip(rows, selesaikan(rows, total)):
        if a > 0:
            i, n, _, _ = ident(b)
            built.append({"invoice_id": str(i), "amount_applied": angka_json(a)})
            if n:
                nomor.append(str(n))
    return built, nomor


def angka_json(a: Decimal):
    """Untuk payload chat (JSON): bulat -> int (bentuk lama), bersen -> float (dulu int() membuang sen)."""
    return int(a) if a == a.to_integral_value() else float(a)


def catatan_lebih_pelanggan(kelebihan) -> dict:
    from .teks_galat import rp
    return {"code": "RP_OVERPAYMENT_DEPOSIT",
            "message": f"Kelebihan {rp(kelebihan)} dicatat sebagai uang muka pelanggan (bisa dipakai "
                       "untuk faktur berikutnya atau dikembalikan)."}


def catatan_lebih_vendor(kelebihan) -> dict:
    # Jujur (Law 34): kelebihan AP hanya dibukukan Dr Uang Muka Pembelian, TANPA baris vendor_deposits (tiket: otomatis uang muka vendor)
    from .teks_galat import rp
    return {"code": "BP_OVERPAYMENT_PREPAID",
            "message": f"Lebih {rp(kelebihan)} dicatat di akun Uang Muka Pembelian; belum bisa dipakai ke faktur lain."}
