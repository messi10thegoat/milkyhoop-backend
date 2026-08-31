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

RADIUS (Fase 1b): `create_bill` + `create_quote` /
`create_sales_invoice` / `create_sales_order`. Aksi lain — termasuk
create_customer / create_vendor / create_item / create_expense, yang justru
MEMBUAT entitasnya sehingga id kosong adalah keadaan yang benar — tidak boleh
berubah perilakunya sedikit pun.

PENAMAAN BERBEDA PER AKSI, dan itu sumber bug berulang: Bill memakai
vendor_id / vendor_name / items[].product_id; Quote/SO/SI memakai
customer_id / customer_name / items[].item_id (+ `description` sebagai nama
baris). Karena itu peta field DIDEKLARASIKAN per aksi di PETA_AKSI, bukan
di-hardcode — membaca amplop yang salah menghasilkan nol yang meyakinkan.

CATATAN KECOCOKAN (terverifikasi di direct_action_registry.py): untuk
Quote/SO/SI `customer_id` adalah required=True, sedangkan `vendor_id` pada
Bill hidden=True/tidak required. Jadi validate_payload SUDAH menangkap
customer_id yang benar-benar KOSONG. Yang TIDAK ia tangkap — dan justru
alasan pagar ini diperluas — ada dua: (1) sentinel `create_new:<nama>` yang
truthy sehingga lolos cek required; (2) baris item yatim, karena `items`
hanya dicek truthy sebagai list, tidak pernah per-baris.

Kenapa cek id EKSPLISIT dan bukan `preview_warnings`: warning bersifat
non-fatal, bisa None, dan sumbernya endpoint pratinjau yang boleh gagal diam.
Pagar yang bersandar pada sinyal yang boleh hilang bukan pagar.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Peta field per aksi. Menambah anggota = mengubah radius = tiket baru.
# `label_pihak` masuk ke kalimat user, jadi ia bagian dari kontrak pesan.
PETA_AKSI: Dict[str, Dict[str, Any]] = {
    "create_bill": {
        # kata_dokumen create_bill DIPIN, TIDAK diturunkan dari registry.
        # display_name registry = "Buat Faktur Pembelian" -> "faktur pembelian",
        # sedangkan kalimat yang SUDAH LIVE berbunyi "kirim ulang faktur ini".
        # Seluruh argumen keamanan batch ini bertumpu pada create_bill tidak
        # tergores, jadi kata ini dipin + dijaga tes byte-exact. Memperbaiki
        # kata bill = tiket sendiri dengan probe produksinya sendiri.
        "kata_dokumen": "faktur",
        "id_pihak": "vendor_id",
        "nama_pihak": "vendor_name",
        "label_pihak": "vendor",
        "id_baris": "product_id",
        "nama_baris": ("product_name", "name", "item_name", "description"),
    },
    "create_quote": {
        # None = DITURUNKAN dari display_name registry (_kata_dokumen).
        "kata_dokumen": None,
        "id_pihak": "customer_id",
        "nama_pihak": "customer_name",
        "label_pihak": "pelanggan",
        "id_baris": "item_id",
        "nama_baris": ("description", "item_name", "name", "product_name"),
    },
    "create_sales_invoice": {
        # None = DITURUNKAN dari display_name registry (_kata_dokumen).
        "kata_dokumen": None,
        "id_pihak": "customer_id",
        "nama_pihak": "customer_name",
        "label_pihak": "pelanggan",
        "id_baris": "item_id",
        "nama_baris": ("description", "item_name", "name", "product_name"),
    },
    "create_sales_order": {
        # None = DITURUNKAN dari display_name registry (_kata_dokumen).
        "kata_dokumen": None,
        "id_pihak": "customer_id",
        "nama_pihak": "customer_name",
        "label_pihak": "pelanggan",
        "id_baris": "item_id",
        "nama_baris": ("description", "item_name", "name", "product_name"),
    },
}

AKSI_DIGERBANG = frozenset(PETA_AKSI)

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


# Nilai penanda yang DITULIS OLEH PENGAYAAN saat nama asli hilang, bukan nama
# barang. Terukur di `tool_executor`: `item["description"] = item.get("name")
# or "Item"` (ekor _enrich_quote / _enrich_sales_order) dan
# `item["description"] = detail.get("name", "Item")` (_enrich_items). Cabang
# BUG-item-slot malah menulis `description = None`. Jadi pada saat gerbang
# membaca payload, nama baris bisa sudah hilang — bukan karena kunci yang
# dibaca salah. Mencetaknya apa adanya melahirkan kalimat "Item, Item belum
# ada di master barang" yang tak bisa ditindaklanjuti siapa pun.
PENANDA_TANPA_MAKNA = frozenset({"item", "(tanpa nama)", "-", "n/a"})


def _nama_baris(baris: Dict[str, Any], kunci: Any) -> Optional[str]:
    """Nama baris menurut urutan kunci milik AKSI ITU, atau None.

    Bill menaruh nama di `product_name`; Quote/SO/SI di `description`.
    Urutannya berbeda per aksi supaya kunci yang paling mungkin benar dibaca
    lebih dulu — bukan karena kunci lain terlarang.

    None berarti "nama tidak terbaca", BUKAN "baris tidak ada". Pemanggil
    wajib mengatakannya kepada user, bukan mencetak penanda kosong makna.
    """
    for k in kunci:
        t = _teks(baris.get(k))
        if t and t.lower() not in PENANDA_TANPA_MAKNA:
            return t
    return None


def _kata_dokumen(action_key: str, peta: Dict[str, Any]) -> str:
    """Kata benda dokumen untuk kalimat user.

    Diturunkan dari `display_name` registry (satu sumber), yang berbentuk
    "Buat <Dokumen>" -> "<dokumen>". Bukan konstanta di berkas ini: kalau
    registry berubah nama dokumen, kalimat ikut, tanpa ada yang perlu ingat.
    `create_bill` DIPIN (lihat komentar di PETA_AKSI).
    """
    dipin = peta.get("kata_dokumen")
    if dipin:
        return dipin
    try:
        from .direct_action_registry import DIRECT_ACTIONS

        dn = _teks(getattr(DIRECT_ACTIONS.get(action_key), "display_name", ""))
    except Exception:  # registry tak bisa diimpor = pagar tetap harus bicara
        dn = ""
    if not dn:
        # Bukan diam-diam: kalimat tetap benar secara umum, dan barisnya
        # tercetak supaya penurunan yang putus bisa ditemukan.
        logger.warning(
            "[GERBANG_ENTITAS] display_name registry tak terbaca action=%s",
            action_key,
        )
        return "dokumen"
    for awalan in ("Buat ", "Catat ", "Ubah ", "Edit "):
        if dn.startswith(awalan):
            dn = dn[len(awalan):]
            break
    return dn.lower()


def _kutipan(teks_user: Any, batas: int = 160) -> str:
    t = _teks(teks_user)
    if not t:
        return ""
    if len(t) > batas:
        t = t[: batas - 1].rstrip() + "\u2026"
    return t


def _rangkai(nama: List[str]) -> str:
    return ", ".join(nama)


def periksa_gerbang_entitas(
    action_key: str,
    payload: Dict[str, Any],
    teks_user: Any = None,
) -> Optional[Dict[str, Any]]:
    """Kembalikan dict CLARIFICATION bila kartu TIDAK boleh dibangun, else None.

    Fungsi MURNI: tanpa DB, tanpa HTTP, tanpa LLM — supaya bisa diuji
    deterministik di suite unit.
    """
    peta = PETA_AKSI.get(action_key)
    if peta is None:
        return None
    if not isinstance(payload, dict):
        return None

    pihak_nama = _teks(payload.get(peta["nama_pihak"]))
    pihak_hilang = bool(pihak_nama) and _id_kosong(payload.get(peta["id_pihak"]))

    baris = payload.get("items")
    yatim: List[str] = []          # baris yatim yang NAMANYA terbaca
    yatim_tanpa_nama = 0           # baris yatim yang namanya sudah hilang
    if isinstance(baris, list):
        for b in baris:
            if isinstance(b, dict) and _id_kosong(b.get(peta["id_baris"])):
                nm = _nama_baris(b, peta["nama_baris"])
                if nm is None:
                    yatim_tanpa_nama += 1
                else:
                    yatim.append(nm)

    ada_yatim = bool(yatim) or yatim_tanpa_nama > 0
    if not pihak_hilang and not ada_yatim:
        return None

    # SATU situs log. Tanpa nama entitas, tanpa isi payload — T181_PUING
    # dicabut persis karena mencetak isi payload ke log.
    # Fase 1a mencetak `vendor=`; diseragamkan jadi `pihak=` karena radius
    # kini memuat aksi yang pihaknya PELANGGAN, bukan vendor. Perubahan baris
    # log create_bill ini DISENGAJA.
    logger.warning(
        "[GERBANG_ENTITAS] action=%s pihak=%s item_yatim=%d",
        action_key,
        "kosong" if pihak_hilang else "ada",
        len(yatim) + yatim_tanpa_nama,
    )

    # DIFERENSIAL: tiap klausa hanya muncul kalau penyebabnya memang ada.
    # Kalau cuma barang yang kurang, kalimat TIDAK menyebut pihak, dan
    # sebaliknya — kalimat buram yang selalu menyebut keduanya membuat user
    # memperbaiki hal yang tidak rusak.
    bagian: List[str] = []
    if pihak_hilang:
        bagian.append(
            f"{pihak_nama} belum terdaftar sebagai {peta['label_pihak']}"
        )
    if yatim and yatim_tanpa_nama:
        bagian.append(
            f"{_rangkai(yatim)} belum ada di master barang, dan "
            f"{yatim_tanpa_nama} baris barang lain namanya tidak terbaca"
        )
    elif yatim:
        bagian.append(f"{_rangkai(yatim)} belum ada di master barang")
    elif yatim_tanpa_nama:
        # TIDAK mencetak "(tanpa nama)": itu memberi user kata yang tak bisa
        # ia cari di mana pun. Yang jujur = katakan namanya tidak terbaca,
        # lalu kutip apa yang ia tulis supaya ia tahu kalimat mana yang salah.
        bagian.append(
            f"{yatim_tanpa_nama} baris barang belum ada di master barang, "
            "dan nama barangnya tidak terbaca"
        )

    pesan = (
        ", dan ".join(bagian)
        + f". Daftarkan dulu, lalu kirim ulang {_kata_dokumen(action_key, peta)} ini."
    )
    kutip = _kutipan(teks_user) if yatim_tanpa_nama else ""
    if kutip:
        pesan += f' Yang Anda tulis: "{kutip}".'

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
