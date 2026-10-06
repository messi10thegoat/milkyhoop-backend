"""Siapan cetak PDF Penawaran (templat baru, 7 Okt 2026; SPEC pemilik "template-penawaran", acuan Accurate-gaya grapgrap).

MURNI (tanpa DB): semua keputusan tampilan yang bukan sekadar menyalin nilai -- format telepon, kapan "Up." dicetak,
isi baris Kontak, label PPN -- ada di sini supaya diuji tanpa merender PDF. Templat (quote.html) hanya menaruh nilainya.
"""
import re
from decimal import Decimal


def format_telepon(t):
    """'081243065632' -> '0812 4306 5632' (kelompok 4-4-sisa) untuk nomor lokal berawalan 0; selain itu apa adanya."""
    if not t:
        return None
    s = str(t).strip()
    d = re.sub(r"\D", "", s)
    if d.startswith("62") and len(d) >= 10:
        d = "0" + d[2:]
    if not d.startswith("0") or len(d) < 9:
        return s
    return " ".join(x for x in (d[:4], d[4:8], d[8:]) if x)


def _sama(a, b) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def baris_up(nama_pelanggan, attention_name, attention_title):
    """"Up. Nama, Jabatan" HANYA bila penanggung jawab ada dan BERBEDA dari nama pelanggan (SPEC: tak mengulang nama)."""
    if not attention_name or _sama(attention_name, nama_pelanggan):
        return None
    return "Up. " + attention_name.strip() + (", " + attention_title.strip() if attention_title and attention_title.strip() else "")


def bagian_kontak(nama, telepon, email) -> list:
    """Bagian baris Kontak (putusan pemilik 7 Okt): nama penanda tangan · HP · email; bagian kosong dilewati."""
    out = []
    if nama and str(nama).strip():
        out.append(("teks", str(nama).strip()))
    if telepon and str(telepon).strip():
        out.append(("teks", format_telepon(telepon)))
    if email and str(email).strip():
        out.append(("email", str(email).strip()))
    return out


def label_pajak(items) -> str:
    """'PPN 11%' bila semua baris berpajak bertarif sama; 'PPN' bila campuran."""
    tarif = {Decimal(str(i.get("tax_rate") or 0)).normalize() for i in items if Decimal(str(i.get("tax_rate") or 0)) > 0}
    if len(tarif) == 1:
        t = tarif.pop()
        return f"PPN {t:f}%".replace(".", ",")
    return "PPN"


def alamat_pelanggan(alamat, address, kota) -> str:
    """Alamat tercetak di 'Kepada Yth.': alamat (Indonesia) ATAU address, + kota bila belum termuat."""
    a = (alamat or address or "").strip()
    k = (kota or "").strip()
    if k and k.casefold() not in a.casefold():
        a = f"{a}, {k}" if a else k
    return a or None


def siapkan(quote: dict, tenant: dict) -> dict:
    """Nilai tampilan turunan untuk templat; quote/tenant tak diubah."""
    return {
        "telepon_usaha": format_telepon(tenant.get("phone")),
        "up": baris_up(quote.get("customer_name"), quote.get("attention_name"), quote.get("attention_title")),
        "kontak": bagian_kontak(quote.get("signer_name"), quote.get("signer_phone"), quote.get("signer_email")),
        "label_pajak": label_pajak(quote.get("items") or []),
        "alamat_pelanggan": alamat_pelanggan(quote.get("customer_alamat"), quote.get("customer_address"), quote.get("customer_city")),
    }
