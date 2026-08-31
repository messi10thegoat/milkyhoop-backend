"""KONTRAK RENDER — apa yang HARUS ada di amplop agar FE menggambar sesuatu.

Kenapa berkas ini ada. Gerbang entitas (T185) menerbitkan jawaban yang BENAR
menurut backend: `message_type="CLARIFICATION"`, kalimat sebab terisi di
`content`/`text`/`error.message`. Layar user tetap KOSONG. Sebabnya bukan
salah satu pihak, melainkan sambungannya: FE untuk CLARIFICATION TIDAK PERNAH
menggambar `content`, ia hanya menggambar `actionData.data`; dan backend
memaksa `data=None` justru pada bentuk yang gerbang terbitkan.

Kelas bug-nya: "backend benar" dan "FE benar" bisa dua-duanya betul sementara
tak ada satu piksel pun sampai ke user. Suite backend tidak bisa melihat ini
karena tak satu pun assertion-nya menyebut apa yang FE butuhkan. Tabel di
bawah membuat kebutuhan itu EKSPLISIT dan BISA GAGAL di CI.

SUMBER TABEL: bundel yang BENAR-BENAR TERPASANG,
`frontend/static/js/main.82ce0f51.js` — bukan sumber TypeScript di Mac dan
bukan dugaan. Tiap entri membawa potongan bundelnya. Bundel ter-minify, jadi
nama komponen berupa identifier pendek (nEt/iEt/oEt); yang mengikat bukan
namanya melainkan potongan kodenya.

BATAS KEJUJURAN berkas ini: ia memeriksa BENTUK amplop, bukan bahwa piksel
muncul. Ia menangkap "data null padahal FE hanya menggambar data"; ia TIDAK
menangkap CSS yang menyembunyikan elemen. Klaim yang boleh dibuat dengannya:
"kombinasi ini tidak bisa berakhir sebagai layar kosong karena amplopnya",
bukan "sudah terlihat di layar".

CARA MEMBACA `WAJIB_DATA`:
  None       = FE tidak membaca `data` untuk tipe ini; `data` boleh apa saja.
  frozenset  = `data` HARUS dict non-null dan memuat SEMUA kunci itu.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class Baris:
    """Satu baris kontrak, berikut bukti FE-nya."""

    __slots__ = ("tipe", "butuh_teks", "wajib_data", "daftar_data", "bukti")

    def __init__(
        self,
        tipe: str,
        butuh_teks: bool,
        wajib_data: Optional[frozenset],
        daftar_data: Tuple[str, ...],
        bukti: str,
    ) -> None:
        self.tipe = tipe
        self.butuh_teks = butuh_teks
        self.wajib_data = wajib_data
        # kunci yang FE akses lewat `.length` / `.map` => WAJIB list, bukan None.
        self.daftar_data = daftar_data
        self.bukti = bukti


KONTRAK: Dict[str, Baris] = {
    # FE menggambar `r.content` sebagai teks paragraf; `data` tak dibaca.
    "TEXT": Baris(
        "TEXT", True, None, (),
        'bundel: `children:t}` pada paragraf teks, dan `_to_chat_response` '
        'tidak mengisi `data` untuk TEXT. Tanpa teks => paragraf kosong.',
    ),
    # INI baris yang T185 tutup.
    "CLARIFICATION": Baris(
        "CLARIFICATION", False, frozenset({"question", "options"}), ("options",),
        'bundel: `case"CLARIFICATION":return ... jsx(nEt,{data:'
        'null===(n=r.actionData)||void 0===n?void 0:n.data,...})` — HANYA '
        '`data` yang digambar, `r.content` TIDAK PERNAH (yang menyertainya, '
        '`a&&jsx(iXt,{content:r.content})`, adalah tombol salin, bukan teks). '
        'Lalu `nEt=e=>{let{data:t...}=e;return t?(...):null}` => `data` null '
        '= LAYAR KOSONG. Di dalamnya `children:t.question` digambar TANPA '
        'SYARAT, dan `t.options.length>0&&(...)` mengakses `.length` LANGSUNG '
        '=> `options` wajib list; `None`/absen = TypeError, bukan sekadar '
        'kosong. Karena itu `options: []` sah dan WAJIB ada.',
    ),
    "DIRECT_ACTION_PREVIEW": Baris(
        "DIRECT_ACTION_PREVIEW", False, frozenset({"pending_action_id"}), (),
        'bundel: `("ACTION_PREVIEW"===n.message_type||"DIRECT_ACTION_PREVIEW"'
        '===n.message_type)&&n.pending_action_id` — kartu hanya didaftarkan '
        'bila `data` ada DAN `pending_action_id` ada; keduanya dibaca dari '
        'amplop `data`/field sejajarnya.',
    ),
    # `data` boleh null: FE jatuh ke `r.content`.
    "ACTION_RESULT": Baris(
        "ACTION_RESULT", True, None, (),
        'bundel: `const e=r.actionData?.data, t=e?.success?r.content||'
        '"Berhasil.":e?.error_message||r.content||"Gagal memproses aksi."` — '
        '`data` boleh null (ada rantai `?.` dan default literal), tapi kalau '
        '`content` kosong DAN `data` null user hanya melihat kalimat generik. '
        'Karena itu teks diwajibkan, `data` tidak.',
    ),
    "VALIDATION_ERROR": Baris(
        "VALIDATION_ERROR", False, frozenset({"errors", "suggestions"}),
        ("errors", "suggestions"),
        'bundel: `iEt=e=>{let{data:t}=e;return jsxs(...t.errors.map(...)...'
        't.suggestions.length>0&&...)}` — TIDAK ADA penjaga `t?`: `data` null '
        '= TypeError. `errors` diakses `.map` dan `suggestions` diakses '
        '`.length`, jadi KEDUANYA wajib list.',
    ),
    "CHART": Baris(
        "CHART", False, frozenset({"render_target", "chart_type", "title"}), (),
        'bundel: `case"CHART":{const e=r.actionData?.data;return e?'
        '"artifact"===e.render_target&&k?...` — dijaga `e?` (null = diam, '
        'tak crash), lalu membaca `render_target`, `chart_type`, `title`.',
    ),
    "TUTORIAL_STEP": Baris(
        "TUTORIAL_STEP", False, frozenset({"step_index", "total_steps"}), (),
        'bundel: `case"TUTORIAL_STEP":{const e=r.actionData?.data;return e?'
        '...jsx(oEt,{data:e,...})` lalu `oEt=...const i=t.step_index/'
        't.total_steps*100` — dijaga `e?`, tapi begitu digambar ia membagi '
        'dengan `total_steps`; keduanya wajib ada.',
    ),
}


def tipe_dikenal(tipe: str) -> bool:
    return tipe in KONTRAK


def periksa_kontrak_render(
    message_type: Any,
    text: Any = None,
    data: Any = None,
) -> List[str]:
    """Kembalikan DAFTAR pelanggaran. Kosong = amplop bisa digambar FE.

    Fungsi MURNI. Ia menjawab satu pertanyaan: kalau amplop ini dikirim apa
    adanya, apakah FE punya bahan untuk menggambar sesuatu?

    Sengaja mengembalikan daftar, bukan bool/exception: pemanggil di tes ingin
    tahu MANA yang kurang, dan satu amplop bisa melanggar lebih dari satu hal.
    """
    salah: List[str] = []

    if not isinstance(message_type, str) or not message_type:
        return ["message_type kosong/bukan string"]

    baris = KONTRAK.get(message_type)
    if baris is None:
        # BUKAN pelanggaran diam: tipe di luar tabel = tabel yang ketinggalan,
        # dan itu harus terlihat, bukan lolos karena tak dikenal.
        return [f"message_type '{message_type}' TIDAK ADA di kontrak render"]

    if baris.butuh_teks and not (isinstance(text, str) and text.strip()):
        salah.append(f"{message_type}: `text` wajib terisi, FE menggambarnya")

    if baris.wajib_data is None:
        return salah

    if not isinstance(data, dict):
        salah.append(
            f"{message_type}: `data` wajib dict non-null "
            f"(FE menggambar dari data, bukan dari text) — didapat {type(data).__name__}"
        )
        return salah

    for kunci in sorted(baris.wajib_data):
        if kunci not in data or data[kunci] is None:
            salah.append(f"{message_type}: `data.{kunci}` wajib ada dan non-null")

    for kunci in baris.daftar_data:
        if kunci in data and data[kunci] is not None and not isinstance(data[kunci], list):
            salah.append(
                f"{message_type}: `data.{kunci}` wajib list — FE mengakses "
                f".length/.map langsung, nilai lain = TypeError di layar"
            )

    return salah
