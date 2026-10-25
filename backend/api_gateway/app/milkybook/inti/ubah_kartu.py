"""#8 ubah isian langsung di kartu Tinjau (kontrak T3 keenam + koreksi FRONTEND 25 Okt).

- daftar_medan: medan yang BOLEH diubah, diturunkan dari skema tool (bukan daftar tangan di FE yang bisa basi).
  Parameter jalur, id rujukan (*_id) dan nama yang terikat id (customer_name) TIDAK bisa diubah di kartu.
  Baris (items) hanya yang sudah ada; tambah/hapus baris tetap lewat obrolan.
- normalkan_angka: medan angka menerima number ATAU string desimal ("45000", "45000.50") -> number.
- galat_kolom / blok_pratinjau: galat siap tampil per kolom [{path, pesan}] / [{kode, pesan, path?}].
"""
from __future__ import annotations

import re
from typing import Any, List, Optional

UANG = {"unit_price", "discount_value", "dp_amount", "shipping_amount", "discount_amount", "sales_price", "purchase_price"}
TAK_BISA_DIUBAH = {"id", "customer_name"}  # + semua *_id
LABEL = {
    "description": "Keterangan", "quantity": "Jumlah", "unit": "Satuan", "unit_price": "Harga satuan",
    "discount_percent": "Diskon baris (%)", "tax_rate": "Tarif pajak (%)", "quote_date": "Tanggal penawaran",
    "expiry_date": "Berlaku sampai", "order_date": "Tanggal pesanan", "expected_ship_date": "Perkiraan kirim",
    "subject": "Perihal", "reference": "Referensi", "notes": "Catatan", "discount_type": "Jenis diskon dokumen",
    "discount_value": "Diskon dokumen", "dp_amount": "Uang muka (Rp)", "dp_percent": "Uang muka (%)", "reason": "Alasan",
    "name": "Nama", "phone": "Telepon", "email": "Surel", "address": "Alamat", "city": "Kota", "base_unit": "Satuan dasar",
    "sales_price": "Harga jual", "purchase_price": "Harga beli", "payment_terms_days": "Syarat bayar (hari)",
}
_ANGKA = re.compile(r"^-?\d+(\.\d+)?$")


def _bisa_diubah(nama: str) -> bool:
    return nama not in TAK_BISA_DIUBAH and not nama.endswith("_id")


def _tipe(nama: str, sk: dict) -> Optional[str]:
    t = sk.get("type")
    if "enum" in sk:
        return "pilihan"
    if t in ("number", "integer"):
        return "uang" if nama in UANG else "angka"
    if t == "boolean":
        return "ya_tidak"
    if t == "string":
        return "tanggal" if sk.get("format") == "date" else "teks"
    return None


def _medan(nama: str, sk: dict, path: str, wajib: bool) -> Optional[dict]:
    tipe = _tipe(nama, sk)
    if tipe is None or not _bisa_diubah(nama):
        return None
    m = {"path": path, "label": LABEL.get(nama, nama.replace("_", " ").capitalize()), "tipe": tipe, "wajib": wajib}
    if tipe == "pilihan":
        m["pilihan"] = list(sk["enum"])
    return m


def daftar_medan(skema: dict, payload: dict, param_jalur: List[str]) -> List[dict]:
    hasil: List[dict] = []
    props, wajib = skema.get("properties", {}), set(skema.get("required", []))
    for nama, sk in props.items():
        if nama in param_jalur:
            continue
        if sk.get("type") == "array" and isinstance(sk.get("items"), dict) and sk["items"].get("type") == "object":
            ip, iw = sk["items"].get("properties", {}), set(sk["items"].get("required", []))
            for i, _ in enumerate(payload.get(nama) or []):
                hasil += [m for n2, s2 in ip.items() if (m := _medan(n2, s2, f"{nama}[{i}].{n2}", n2 in iw))]
            continue
        m = _medan(nama, sk, nama, nama in wajib)
        if m:
            hasil.append(m)
    return hasil


def normalkan_angka(skema: dict, nilai: Any) -> Any:
    """String desimal di medan angka -> int (bulat) / float (pecahan; BE membulatkan 2 desimal). Selain itu apa adanya."""
    t = skema.get("type")
    if t in ("number", "integer") and isinstance(nilai, str) and _ANGKA.match(nilai.strip()):
        x = float(nilai.strip())
        return int(x) if x.is_integer() else x
    if t == "object" and isinstance(nilai, dict):
        props = skema.get("properties", {})
        return {k: normalkan_angka(props[k], v) if k in props else v for k, v in nilai.items()}
    if t == "array" and isinstance(nilai, list) and isinstance(skema.get("items"), dict):
        return [normalkan_angka(skema["items"], v) for v in nilai]
    return nilai


def _label_path(path: str) -> str:
    akhir = re.split(r"[.\[\]]", path.rstrip("]"))[-1] or path
    return LABEL.get(akhir, akhir.replace("_", " ").capitalize())


def galat_kolom(galat_validasi: str) -> List[dict]:
    """'masukan.items[0].quantity: harus angka' -> [{path:'items[0].quantity', pesan:'Jumlah harus berupa angka.'}]"""
    path, _, isi = galat_validasi.partition(": ")
    path = re.sub(r"^masukan\.?", "", path)
    lab = _label_path(path)
    pesan = {"harus angka": f"{lab} harus berupa angka.", "wajib": f"{lab} wajib diisi.",
             "harus bilangan bulat": f"{lab} harus bilangan bulat.", "nilai di luar pilihan": f"{lab}: pilihan tidak sah.",
             "tidak dikenal": f"Kolom {path} tidak bisa diubah di sini."}.get(isi, f"{lab}: {isi}.")
    return [{"path": path, "pesan": pesan}]


def blok_pratinjau(status: int, isi: Any) -> List[dict]:
    """Penolakan pratinjau BE -> [{kode, pesan, path?}] siap tampil (blocks BE, detail teks, atau galat Pydantic 422)."""
    data = isi.get("data", isi) if isinstance(isi, dict) else {}
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if isinstance(blocks, list) and blocks:
        return [{"kode": b.get("code") or "BLOK", "pesan": b.get("message") or b.get("detail") or "Ditolak server.",
                 **({"path": b["field"]} if b.get("field") else {})} for b in blocks if isinstance(b, dict)]
    detail = isi.get("detail") if isinstance(isi, dict) else None
    if isinstance(detail, list):  # Pydantic 422: [{loc, msg}]
        return [{"kode": "DATA_TIDAK_SAH", "pesan": d.get("msg", "Data tidak sah."),
                 "path": ".".join(str(x) for x in d.get("loc", [])[1:])} for d in detail if isinstance(d, dict)]
    if isinstance(detail, dict):
        return [{"kode": detail.get("code") or "DITOLAK", "pesan": detail.get("message") or "Ditolak server."}]
    return [{"kode": "DITOLAK", "pesan": str(detail or "Ditolak server.")}] if status >= 400 else []
