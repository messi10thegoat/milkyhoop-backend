"""GERBANG ENTITAS — Fase 1a.

Masalah yang ditutup (terukur sebelum batch ini): untuk `create_bill`, kartu
konfirmasi tetap dibangun walau vendor DAN/ATAU barangnya belum terdaftar.
Tiga kegagalan bertumpuk:
  1. resolver vendor di `_enrich_purchase_invoice` gagal TANPA cabang else —
     nihil = diam;
  2. `validate_payload` mewajibkan `vendor_name` (required) BUKAN `vendor_id`
     (hidden, tidak required) — jadi nama saja sudah lolos;
  3. `preview_warnings` terisi benar lalu tak pernah dibaca.
Akibatnya user menekan "Konfirmasi" atas kartu yang endpointnya pasti tolak.

Yang dikerjakan DI SINI hanyalah PAGAR: menolak membangun kartu. Ronde ini
TIDAK membangun alur bersambung (pendaftaran otomatis, sentinel `create_new:`
tetap perilakunya sekarang).

RADIUS: HANYA `create_bill`. Aksi lain — termasuk create_customer /
create_vendor / create_item, yang justru MEMBUAT entitasnya sehingga id kosong
adalah keadaan yang benar — tidak boleh berubah perilakunya sedikit pun.

Kenapa cek id EKSPLISIT dan bukan `preview_warnings`: warning bersifat
non-fatal, bisa None, dan sumbernya endpoint pratinjau yang boleh gagal diam.
Pagar yang bersandar pada sinyal yang boleh hilang bukan pagar.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Radius pagar. Menambah anggota daftar ini = mengubah radius = tiket baru.
AKSI_DIGERBANG = frozenset({"create_bill"})

KODE_GERBANG = "ENTITAS_BELUM_TERDAFTAR"


def _teks(nilai: Any) -> str:
    return str(nilai).strip() if nilai is not None else ""


def _id_kosong(nilai: Any) -> bool:
    """id dianggap KOSONG bila None / "" / spasi / sentinel pembuatan baru.

    Sentinel `create_new:<nama>` sengaja dihitung KOSONG: ia berarti "entitas
    ini belum ada". Perilaku sentinel di jalur lain tidak diubah batch ini —
    yang berubah hanya: kartu tidak dibangun untuknya di create_bill.
    """
    t = _teks(nilai)
    return t == "" or t.lower().startswith("create_new:")


def _nama_baris(baris: Dict[str, Any]) -> str:
    # Penamaan payload Bill = product_name/price/product_id.
    # (Quote/SO/SI memakai description/unit_price/item_id — beda amplop;
    # membaca amplop yang salah menghasilkan nol yang meyakinkan.)
    for k in ("product_name", "name", "item_name", "description"):
        t = _teks(baris.get(k))
        if t:
            return t
    return "(tanpa nama)"


def _rangkai(nama: List[str]) -> str:
    return ", ".join(nama)


def periksa_gerbang_entitas(
    action_key: str, payload: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Kembalikan dict CLARIFICATION bila kartu TIDAK boleh dibangun, else None.

    Fungsi MURNI: tanpa DB, tanpa HTTP, tanpa LLM — supaya bisa diuji
    deterministik di suite unit.
    """
    if action_key not in AKSI_DIGERBANG:
        return None
    if not isinstance(payload, dict):
        return None

    vendor_nama = _teks(payload.get("vendor_name"))
    vendor_hilang = bool(vendor_nama) and _id_kosong(payload.get("vendor_id"))

    baris = payload.get("items")
    yatim: List[str] = []
    if isinstance(baris, list):
        for b in baris:
            if isinstance(b, dict) and _id_kosong(b.get("product_id")):
                yatim.append(_nama_baris(b))

    if not vendor_hilang and not yatim:
        return None

    # SATU situs log. Tanpa nama entitas, tanpa isi payload — T181_PUING
    # dicabut persis karena mencetak isi payload ke log.
    logger.warning(
        "[GERBANG_ENTITAS] action=%s vendor=%s item_yatim=%d",
        action_key,
        "kosong" if vendor_hilang else "ada",
        len(yatim),
    )

    bagian: List[str] = []
    if vendor_hilang:
        bagian.append(f"{vendor_nama} belum terdaftar sebagai vendor")
    if yatim:
        bagian.append(f"{_rangkai(yatim)} belum ada di master barang")
    pesan = (
        ", dan ".join(bagian)
        + ". Daftarkan dulu, lalu kirim ulang faktur ini."
    )

    # Bentuk amplop SENGAJA rangkap. `_execute_propose_direct` dipanggil dari
    # ~20 situs; masing-masing membaca kunci yang berbeda kalau hasilnya BUKAN
    # DIRECT_ACTION_PREVIEW: ada yang membaca `text`, ada `error.message`, ada
    # `content`. Mengisi ketiganya = tak ada satu pun jalur yang berakhir di
    # `text: null` di layar (T181 Fase 1 di-rollback persis karena itu).
    return {
        "success": False,
        "message_type": "CLARIFICATION",
        "content": pesan,
        "text": pesan,
        "error": {"code": KODE_GERBANG, "message": pesan},
        "data": {
            "question": pesan,
            "options": [],
            "allow_freetext": True,
        },
    }
