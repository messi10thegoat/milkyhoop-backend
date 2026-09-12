"""fix/ocr-nota-tulisan-tangan — lapisan EKSTRAKSI dokumen (prompt + post-processing
deterministik) untuk jalur `[DocSimple]` di `routers/unified_chat.py`.

Gejala 2 Sep 2026: nota tulisan tangan (25 × Jersy Baseball @165.000, "Jumlah Rp
4.125.000", stempel LUNAS 10 AUG 2026) diekstrak gpt-4o-mini menjadi
total=165000 (harga SATUAN), document_date=2016-08-10 (abad salah), vendor="unknown".

Dua lapis perbaikan, keduanya di sini supaya bisa diuji tanpa LLM:
  * `build_ocr_prompt(caption)` — prompt dengan aturan nota/kwitansi Indonesia
    (grand total ≠ harga satuan, `line_items` + `grand_total` terpisah, tanggal
    dd-mm-yyyy, tahun 2 digit → 20xx, ragu → null).
  * `postprocess_ocr(data, today)` — koreksi deterministik atas JSON mentah LLM:
    (i)  total dari Σ(qty×unit_price) bila bukti mendukung → `total_source="line_items"`
    (ii) tahun < 2000 → +100 HANYA bila ≤ hari ini + 1 tahun DAN konteks mendukung,
         selain itu `document_date=None` + flag
    (iii) vendor/customer "unknown"/"null"/"-" → None
    Setiap koreksi ditulis ke `extraction_notes` (list str) supaya kartu bisa
    menampilkannya, bukan diam-diam.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

_UNKNOWN_TOKENS = {
    "", "unknown", "null", "none", "-", "n/a", "tidak diketahui",
    # label kolom nota yang kadang disalin LLM sebagai "nama"
    "tuan", "toko", "tuan/toko", "kepada", "tuan / toko",
}


def build_ocr_prompt(caption: str) -> str:
    """Prompt tunggal untuk vision OCR + ekstraksi (dipakai gpt-4o-mini / Gemini)."""
    return f"""Ekstrak data dari dokumen finansial Indonesia ini. User caption: "{caption}"

Return JSON ONLY:
{{
  "is_financial_document": true,
  "doc_type": "expense|bank_transfer|qris|merchant_payment|purchase_invoice|sales_invoice|receipt|unknown",
  "transfer_direction": "masuk|keluar|null",
  "vendor_name": "nama vendor / kop toko (untuk struk PLN: 'PLN', untuk transfer keluar: nama penerima) atau null",
  "customer_name": "nama customer / pihak yang ditagih (mis. baris 'Tuan/Toko') atau null",
  "document_number": "no faktur/no ref atau null",
  "document_date": "YYYY-MM-DD atau null",
  "line_items": [{{"qty": 0, "name": "nama barang", "unit_price": 0, "line_total": 0}}],
  "grand_total": 0,
  "total_amount": 0,
  "tax_amount": 0,
  "source_account_number": "nomor rek pengirim atau null",
  "destination_account_number": "nomor rek penerima atau null",
  "reference_note": "berita/keterangan",
  "confidence": 0.9,
  "extracted_text": "teks yang terbaca, RINGKAS: baris dipisah ' | ', maksimal 500 karakter, LEWATI baris/sel kosong, jangan mengulang karakter"
}}

Aturan:
- Struk PLN: doc_type="expense", total_amount=field "RP BAYAR" (BUKAN RP STROOM/TOKEN, BUKAN total dengan ADMIN BANK), tax_amount=PBJT-TL, vendor_name="PLN"
- QRIS/merchant payment ("Pembayaran QRIS Berhasil", Merchant PAN, Terminal ID): doc_type="qris", NOT bank_transfer. Ini pembayaran ke merchant, bukan transfer antar bank.
- transfer_direction: "masuk" jika uang MASUK ke rekening kita (kita yang menerima, nama penerima = nama bisnis/toko kita, atau caption bilang "dari pelanggan"/"pembayaran masuk"). "keluar" jika uang KELUAR dari rekening kita. null jika tidak bisa ditentukan.
- Bukti transfer: total_amount=Nominal Transfer (BUKAN Total Transaksi yang sudah +biaya admin)
- NOTA / KWITANSI / FAKTUR (cetak maupun TULISAN TANGAN) dengan tabel barang (kolom BANYAKNYA/QTY, NAMA BARANG, HARGA, JUMLAH):
  * line_items: satu entri per baris barang. qty=kolom BANYAKNYA, unit_price=kolom HARGA (harga SATUAN), line_total=kolom JUMLAH bila terisi (kalau kosong: null, JANGAN dihitung sendiri).
  * grand_total: angka pada baris "Jumlah Rp" / "Total" / "Grand Total" / baris paling bawah / yang distempel. null bila tidak ada.
  * total_amount = GRAND TOTAL dokumen (nilai yang harus dibayar), BUKAN harga satuan sebuah baris. Bila qty > 1, total_amount TIDAK MUNGKIN sama dengan unit_price.
  * vendor_name = nama pada KOP nota (toko/penerbit). customer_name = NAMA yang ditulis di sebelah label "Tuan"/"Toko"/"Kepada" (label itu sendiri BUKAN nama; kalau kosong → null).
- Dokumen tanpa tabel barang (bukti transfer, QRIS, e-wallet): line_items=[] dan grand_total=null.
- Tanggal Indonesia ditulis dd-mm-yyyy atau dd/mm/yy (hari DULU, baru bulan). Tahun 2 digit → 20xx. Stempel/cap (mis. "LUNAS 10 AUG 2026") adalah sumber tanggal yang sah. Dokumen ini dibaca tahun 2026 — tahun 19xx/20xx sebelum 2015 hampir pasti salah baca; bila ragu isi null.
- JANGAN mengarang. Field yang tidak terbaca → null, dan turunkan confidence (< 0.6).
- Semua angka Rupiah tanpa desimal (165.000 → 165000)
- is_financial_document: true jika gambar berisi dokumen keuangan (struk, invoice, bukti transfer, kwitansi, nota, faktur, slip gaji). false jika gambar berisi tabel data, screenshot aplikasi, foto produk, chat/pesan, atau konten non-keuangan.
- extracted_text: WAJIB diisi meskipun is_financial_document=false, tetapi RINGKAS (≤ 500 karakter) — sertakan tanggal, nama, angka, dan teks stempel; lewati sel tabel yang kosong.
- Jika is_financial_document=false, isi semua field KECUALI extracted_text dan confidence dengan null/0/"unknown"."""


# ---------------------------------------------------------------------------
# Parse JSON LLM (toleran terhadap pagar markdown + potongan max_tokens)
# ---------------------------------------------------------------------------


def parse_ocr_json(text: str) -> dict:
    """`json.loads` yang tahan pagar ```json dan keluaran TERPOTONG (finish=length).

    gpt-4o-mini kadang mengulang '\\n' di extracted_text sampai max_tokens habis;
    karena field kunci ditaruh SEBELUM extracted_text, memotong ekor lalu menutup
    kurung masih menyelamatkan doc_type/total/tanggal. Gagal total → {} (bukan raise),
    ditandai `_parse_error` supaya pemanggil bisa menurunkan confidence.
    """
    import json as _json

    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        d = _json.loads(s)
        return d if isinstance(d, dict) else {}
    except Exception as first_err:  # noqa: BLE001
        last_err = first_err
    # potong di koma terakhir (batas antar-field) lalu coba tutup
    body = s
    for _ in range(40):
        cut = body.rfind(",")
        if cut <= 0:
            break
        body = body[:cut]
        for tail in ("}", "]}", "\"}", "\"]}", "}]}", "}}"):
            try:
                d = _json.loads(body + tail)
                if isinstance(d, dict):
                    d["_parse_error"] = f"truncated-repaired: {last_err}"
                    d.setdefault("extracted_text", "")
                    return d
            except Exception as e:  # noqa: BLE001
                last_err = e
    return {"_parse_error": str(last_err), "extracted_text": ""}


# ---------------------------------------------------------------------------
# Post-processing deterministik
# ---------------------------------------------------------------------------


def _num(v: Any) -> Optional[float]:
    """'165.000' / '4.125.000,-' / 165000 / None → float | None (Rupiah, tanpa desimal)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = re.sub(r"[^\d,\.]", "", s)
    if not s:
        return None
    # Format Indonesia: titik = ribuan, koma = desimal. Buang bagian desimal.
    s = s.split(",")[0].replace(".", "")
    return float(s) if s.isdigit() else None


def _fmt_idr(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", ".")


def _clean_name(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in _UNKNOWN_TOKENS else s


def _normalize_line_items(raw: Any) -> list[dict]:
    items: list[dict] = []
    if not isinstance(raw, list):
        return items
    for it in raw:
        if not isinstance(it, dict):
            continue
        qty = _num(it.get("qty"))
        unit = _num(it.get("unit_price"))
        line_total = _num(it.get("line_total"))
        name = _clean_name(it.get("name"))
        if qty is None and unit is None and line_total is None:
            continue
        items.append(
            {"qty": qty, "name": name, "unit_price": unit, "line_total": line_total}
        )
    return items


def _sum_line_items(items: list[dict]) -> Optional[float]:
    """Σ(qty×unit_price); bila salah satu tak ada, pakai line_total; bila tak ada
    juga → None (tidak bisa dihitung → jangan pura-pura bisa)."""
    total = 0.0
    any_line = False
    for it in items:
        qty, unit, lt = it["qty"], it["unit_price"], it["line_total"]
        if qty is not None and unit is not None:
            total += qty * unit
            any_line = True
        elif lt is not None:
            total += lt
            any_line = True
        else:
            return None
    return total if any_line else None


def _parse_date(v: Any) -> tuple[Optional[date], Optional[str]]:
    """Terima 'YYYY-MM-DD', 'dd-mm-yyyy', 'dd/mm/yy'. → (date|None, alasan_gagal|None)."""
    if v is None:
        return None, None
    s = str(v).strip()
    if not s or s.lower() in _UNKNOWN_TOKENS:
        return None, None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
        if not m:
            return None, f"format tanggal tak dikenal: {s!r}"
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d), None
    except ValueError:
        return None, f"tanggal tidak valid: {s!r}"


def _context_years(data: dict, caption: str = "") -> set[int]:
    """Semua tahun 4 digit yang disebut konteks lain (stempel, teks OCR, caption)."""
    hay = " ".join(
        str(data.get(k) or "")
        for k in ("extracted_text", "reference_note", "user_caption", "raw_text")
    ) + " " + (caption or "")
    return {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", hay)}


def _context_supports_year(data: dict, year: int, caption: str = "") -> bool:
    return year in _context_years(data, caption)


def _recent_context_year(data: dict, today: date, caption: str = "") -> Optional[int]:
    """Tahun tunggal dari konteks yang masuk akal untuk pembukuan sekarang
    (today-1 .. today+1). Lebih dari satu kandidat → None (jangan menebak)."""
    cands = {
        y for y in _context_years(data, caption) if today.year - 1 <= y <= today.year + 1
    }
    return next(iter(cands)) if len(cands) == 1 else None


def postprocess_ocr(
    data: dict, today: Optional[date] = None, caption: str = ""
) -> dict:
    """Koreksi deterministik atas JSON mentah LLM. Memutasi & mengembalikan `data`.

    Kunci yang ditambahkan:
      total_source      : "ocr" | "line_items" | "grand_total"
      total_flag        : None | "line_items_mismatch"
      date_flag         : None | "century_fixed" | "ambiguous_year" | "unparseable"
      extraction_notes  : list[str] — kalimat ringkas untuk kartu preview
    """
    if not isinstance(data, dict):
        return data
    today = today or date.today()
    notes: list[str] = list(data.get("extraction_notes") or [])
    if isinstance(data.get("extracted_text"), str):
        data["extracted_text"] = re.sub(r"\s{3,}", " ", data["extracted_text"]).strip()[:2000]
    if data.get("_parse_error"):
        data["confidence"] = min(float(data.get("confidence") or 0) or 0.0, 0.5)
        notes.append("Keluaran OCR terpotong; sebagian field mungkin kosong")

    # ── (iii) nama "unknown" → None ────────────────────────────────────────
    for k in ("vendor_name", "customer_name", "document_number"):
        if k in data:
            data[k] = _clean_name(data.get(k))

    # ── (i) total dari line_items ──────────────────────────────────────────
    items = _normalize_line_items(data.get("line_items"))
    data["line_items"] = items
    grand = _num(data.get("grand_total"))
    data["grand_total"] = grand
    total = _num(data.get("total_amount"))
    data["total_source"] = "ocr"
    data["total_flag"] = None
    line_sum = _sum_line_items(items)

    def _eq(a: Optional[float], b: Optional[float]) -> bool:
        return a is not None and b is not None and abs(a - b) < 0.5

    if line_sum is not None and line_sum > 0 and not _eq(line_sum, total):
        multi = [it for it in items if (it["qty"] or 0) > 1]
        # bukti A: grand_total dokumen == Σ  → Σ dipercaya
        # bukti B: total_amount == unit_price sebuah baris ber-qty>1 → LLM
        #          mengambil harga satuan (persis gejala 2 Sep)
        bukti_a = _eq(grand, line_sum)
        bukti_b = any(_eq(total, it["unit_price"]) for it in multi)
        if bukti_a or bukti_b:
            data["total_amount"] = line_sum
            data["total_source"] = "line_items"
            if len(items) == 1 and items[0]["qty"] is not None and items[0]["unit_price"] is not None:
                notes.append(
                    f"Total dihitung dari {_fmt_idr(items[0]['qty'])} × "
                    f"{_fmt_idr(items[0]['unit_price'])} = {_fmt_idr(line_sum)}"
                )
            else:
                notes.append(
                    f"Total dihitung dari {len(items)} baris barang = {_fmt_idr(line_sum)}"
                )
            total = line_sum
        else:
            data["total_flag"] = "line_items_mismatch"
            notes.append(
                f"Σ baris barang {_fmt_idr(line_sum)} ≠ total terbaca "
                f"{_fmt_idr(total) if total is not None else '—'}; mohon periksa"
            )
    elif (total is None or total == 0) and grand is not None and grand > 0:
        data["total_amount"] = grand
        data["total_source"] = "grand_total"
        total = grand
    if total is not None:
        data["total_amount"] = total

    # ── (ii) tahun abad salah ──────────────────────────────────────────────
    data["date_flag"] = None
    raw_date = data.get("document_date")
    parsed, why = _parse_date(raw_date)
    if raw_date not in (None, "") and parsed is None:
        data["document_date"] = None
        data["date_flag"] = "unparseable"
        notes.append(f"Tanggal '{raw_date}' tidak terbaca; mohon isi manual")
    elif parsed is not None:
        max_ok = today.replace(year=today.year + 1)
        if parsed.year < 2000:
            fixed = parsed.replace(year=parsed.year + 100)
            if fixed <= max_ok and _context_supports_year(data, fixed.year, caption):
                data["document_date"] = fixed.isoformat()
                data["date_flag"] = "century_fixed"
                notes.append(
                    f"Tahun terbaca {parsed.year}, dikoreksi ke {fixed.year} "
                    f"(konteks dokumen menyebut {fixed.year})"
                )
            else:
                data["document_date"] = None
                data["date_flag"] = "ambiguous_year"
                notes.append(
                    f"Tanggal terbaca {parsed.isoformat()} (tahun meragukan); mohon periksa"
                )
        elif parsed.year <= today.year - 5:
            # gejala 2 Sep: '10 08 2026' dibaca 2016 padahal teks OCR/stempel menyebut 2026
            ctx_year = _recent_context_year(data, today, caption)
            if ctx_year is not None:
                fixed = parsed.replace(year=ctx_year)
                data["document_date"] = fixed.isoformat()
                data["date_flag"] = "year_from_context"
                notes.append(
                    f"Tahun terbaca {parsed.year}, dikoreksi ke {ctx_year} "
                    f"(konteks dokumen menyebut {ctx_year})"
                )
            else:
                data["document_date"] = parsed.isoformat()
                data["date_flag"] = "old_year"
                notes.append(
                    f"Tanggal terbaca {parsed.isoformat()} — jauh dari sekarang; mohon periksa"
                )
        elif parsed > max_ok:
            data["document_date"] = None
            data["date_flag"] = "future_year"
            notes.append(
                f"Tanggal terbaca {parsed.isoformat()} (masa depan); mohon periksa"
            )
        else:
            data["document_date"] = parsed.isoformat()

    data["extraction_notes"] = notes
    return data


def extraction_notes_text(data: dict) -> str:
    """Satu baris ringkas untuk disisipkan ke narasi/kartu; '' bila tak ada koreksi."""
    notes = (data or {}).get("extraction_notes") or []
    return " · ".join(str(n) for n in notes if n)


def display_party(name: Any) -> str:
    """Nama pihak untuk kartu: None/'unknown' → '—' (jangan tampilkan 'unknown')."""
    return _clean_name(name) or "—"
