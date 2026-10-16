"""Alamat pelanggan terstruktur (V428 customer_addresses; kontrak backend/docs/KONTRAK-alamat-terstruktur-20261016.md).

Pola NetSuite addressbook / SAP BP address: banyak alamat per pelanggan, tepat satu bawaan tagih + satu bawaan kirim
(indeks unik parsial di DB), dokumen menyimpan SALINAN teks. SATU tempat untuk:
  format_alamat()      teks tercetak (NetSuite addrtext) -- turunan, tak disimpan.
  cermin_ke_datar()    baris bawaan tagih -> customers.alamat/city/province/postal_code (kolom datar = CERMIN).
  cermin_dari_datar()  API lama (POST/PATCH /customers address/city/province/postal_code) -> baris bawaan tagih.
  tambah/ubah/jadikan_bawaan/nonaktifkan  operasi PER BARIS (tak ada ganti-seluruh-daftar: kelas kehilangan data
                       "penulis menganggap yang tampil = segalanya").
Pemanggil membuka transaksi; tiap penulis mengunci pelanggan (Law 13: CUSTOMER_ADDRESS:<id>) lalu mencerminkan dalam
transaksi yang SAMA. Filter tenant eksplisit di setiap kueri (BYPASSRLS; set_config bukan pagar); trigger V428
trg_ca_tenant_sama menolak alamat ber-tenant beda dengan pelanggannya.

Aturan cermin (tahap 1, pembaca lama TAK berubah):
  customers.alamat = jalan(bawaan tagih) = addr1, addr2, addr3 yang terisi digabung ", " (isi APA ADANYA);
  city = city, province = state, postal_code = zip. country TIDAK dicerminkan (customers.negara = identitas pajak V420).
  Arah balik: alamat datar yang BERBEDA dari jalan(baris) -> addr1 = teks itu, addr2/addr3 dikosongkan (form lama
  memegang seluruh jalan sebagai satu teks); sama -> baris jalan tak disentuh.
"""
import logging
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

MEDAN = ("label", "attention", "addressee", "phone", "addr1", "addr2", "addr3", "city", "state", "zip", "country")
MEDAN_ISI = ("addr1", "city", "state", "zip")  # = CHECK ca_isi V428
BATAS = {"label": 100, "attention": 255, "addressee": 255, "phone": 50, "city": 100, "state": 100, "zip": 20}
NEGARA_RUMAH = "IDN"  # negara bawaan tampilan (customers.negara / builder pajak memakai IDN sebagai bawaan)
KOLOM_SQL = ", ".join(("id",) + MEDAN + ("is_default_billing", "is_default_shipping", "is_active", "sumber",
                                          "created_at", "updated_at"))


def _teks(v) -> Optional[str]:
    """Pelaku -> teks (ctx user_id = UUID; kolom created_by/updated_by = text)."""
    return str(v) if v is not None else None


def _isi(v) -> bool:
    return v is not None and str(v).strip() != ""


def jalan(a: dict) -> Optional[str]:
    """Baris jalan addr1..addr3 yang terisi, digabung ", " APA ADANYA (tanpa trim isi) -> None bila kosong."""
    bagian = [a.get(k) for k in ("addr1", "addr2", "addr3") if _isi(a.get(k))]
    return ", ".join(bagian) if bagian else None


def format_alamat(a: Optional[dict], negara_rumah: str = NEGARA_RUMAH) -> Optional[str]:
    """Teks alamat tercetak (satu sumber). jalan, lalu kota / provinsi / kode pos yang BELUM termuat di teks
    (aturan penawaran_cetak.alamat_pelanggan untuk kota, diperluas), lalu kode negara bila bukan negara rumah."""
    if not a:
        return None
    # teks CETAK: tiap baris di-trim (= penawaran_cetak); jalan() untuk cermin tetap apa adanya (byte)
    teks = ", ".join(a[k].strip() for k in ("addr1", "addr2", "addr3") if _isi(a.get(k)))
    for k in ("city", "state", "zip"):
        v = (a.get(k) or "").strip()
        if v and v.casefold() not in teks.casefold():
            teks = f"{teks}, {v}" if teks else v
    n = (a.get("country") or "").strip().upper()
    if n and n != negara_rumah:
        teks = f"{teks}, {n}" if teks else n
    return teks or None


def ke_json(r) -> dict:
    d = {k: r[k] for k in MEDAN}
    d.update({"id": str(r["id"]), "is_default_billing": r["is_default_billing"],
              "is_default_shipping": r["is_default_shipping"], "text": format_alamat(d)})
    return d


def bersihkan(data: dict) -> dict:
    """Masukan API -> nilai kolom. Teks kosong -> None; country huruf besar ISO alpha-3; panjang dibatasi (422)."""
    out = {}
    for k, v in data.items():
        if k not in MEDAN:
            continue
        if v is not None and not isinstance(v, str):
            v = str(v)
        if v is not None and v.strip() == "":
            v = None
        if k == "country" and v is not None:
            v = v.strip().upper()
            if len(v) != 3 or not v.isalpha():
                raise HTTPException(status_code=422, detail="Negara harus kode 3 huruf (mis. IDN).")
        if v is not None and k in BATAS and len(v) > BATAS[k]:
            raise HTTPException(status_code=422, detail=f"Isian alamat '{k}' terlalu panjang (maks {BATAS[k]}).")
        out[k] = v
    return out


def _tolak_kosong(a: dict) -> None:
    if not any(_isi(a.get(k)) for k in MEDAN_ISI):
        raise HTTPException(status_code=422, detail="Alamat wajib berisi jalan, kota, provinsi, atau kode pos.")


async def _kunci_pelanggan(conn, tenant_id: str, customer_id) -> None:
    """404 bila pelanggan tak ada di tenant ini; kunci serial per pelanggan (pindah bawaan = baca-lalu-tulis)."""
    ada = await conn.fetchval("SELECT 1 FROM customers WHERE id = $1 AND tenant_id = $2", customer_id, tenant_id)
    if not ada:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan.")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"CUSTOMER_ADDRESS:{customer_id}")


async def daftar(conn, tenant_id: str, customer_id, aktif_saja: bool = True) -> list:
    return [ke_json(r) for r in await conn.fetch(
        f"""SELECT {KOLOM_SQL} FROM customer_addresses
            WHERE tenant_id = $1 AND customer_id = $2 {"AND is_active" if aktif_saja else ""}
            ORDER BY is_default_billing DESC, is_default_shipping DESC, created_at, id""",
        tenant_id, customer_id)]


async def _baris(conn, tenant_id: str, customer_id, address_id):
    r = await conn.fetchrow(
        f"SELECT {KOLOM_SQL} FROM customer_addresses WHERE id = $1 AND customer_id = $2 AND tenant_id = $3",
        address_id, customer_id, tenant_id)
    if not r or not r["is_active"]:
        raise HTTPException(status_code=404, detail="Alamat tidak ditemukan.")
    return r


async def _bawaan(conn, tenant_id: str, customer_id, kolom: str):
    return await conn.fetchrow(
        f"SELECT {KOLOM_SQL} FROM customer_addresses WHERE tenant_id = $1 AND customer_id = $2 AND {kolom}",
        tenant_id, customer_id)


async def cermin_ke_datar(conn, tenant_id: str, customer_id) -> None:
    """Bawaan tagih -> kolom datar customers (pembaca lama). Tanpa bawaan tagih: kolom datar tak disentuh."""
    b = await _bawaan(conn, tenant_id, customer_id, "is_default_billing")
    if b is None:
        return
    await conn.execute(
        """UPDATE customers SET alamat = $3, city = $4, province = $5, postal_code = $6, updated_at = NOW()
           WHERE id = $1 AND tenant_id = $2""",
        customer_id, tenant_id, jalan(dict(b)), b["city"], b["state"], b["zip"])


async def cermin_dari_datar(conn, tenant_id: str, customer_id, oleh: Optional[str] = None) -> None:
    """Kolom datar (sesudah POST/PATCH lama) -> baris bawaan tagih, dalam transaksi pemanggil."""
    oleh = _teks(oleh)
    await _kunci_pelanggan(conn, tenant_id, customer_id)
    c = await conn.fetchrow(
        "SELECT alamat, city, province, postal_code FROM customers WHERE id = $1 AND tenant_id = $2",
        customer_id, tenant_id)
    b = await _bawaan(conn, tenant_id, customer_id, "is_default_billing")
    if b is None:
        if not any(_isi(c[k]) for k in ("alamat", "city", "province", "postal_code")):
            return
        ada_kirim = await _bawaan(conn, tenant_id, customer_id, "is_default_shipping")
        await conn.execute(
            """INSERT INTO customer_addresses (tenant_id, customer_id, addr1, city, state, zip,
                   is_default_billing, is_default_shipping, created_by, updated_by)
               VALUES ($1, $2, $3, $4, $5, $6, true, $7, $8, $8)""",
            tenant_id, customer_id, c["alamat"] if _isi(c["alamat"]) else None,
            c["city"] if _isi(c["city"]) else None, c["province"] if _isi(c["province"]) else None,
            c["postal_code"] if _isi(c["postal_code"]) else None, ada_kirim is None, oleh)
        return
    baru = {"addr1": b["addr1"], "addr2": b["addr2"], "addr3": b["addr3"],
            "city": c["city"] if _isi(c["city"]) else None,
            "state": c["province"] if _isi(c["province"]) else None,
            "zip": c["postal_code"] if _isi(c["postal_code"]) else None}
    if (c["alamat"] if _isi(c["alamat"]) else None) != jalan(dict(b)):
        baru.update(addr1=c["alamat"] if _isi(c["alamat"]) else None, addr2=None, addr3=None)
    if not any(_isi(baru[k]) for k in MEDAN_ISI):
        # alamat datar dikosongkan lewat form lama -> baris bawaan dilepas (nonaktif, bukan dihapus: bisa dirujuk SO)
        await conn.execute(
            """UPDATE customer_addresses SET is_default_billing = false, is_default_shipping = false, is_active = false,
                   updated_at = NOW(), updated_by = $3 WHERE id = $1 AND tenant_id = $2""",
            b["id"], tenant_id, oleh)
        return
    await conn.execute(
        """UPDATE customer_addresses SET addr1 = $3, addr2 = $4, addr3 = $5, city = $6, state = $7, zip = $8,
               updated_at = NOW(), updated_by = $9 WHERE id = $1 AND tenant_id = $2""",
        b["id"], tenant_id, baru["addr1"], baru["addr2"], baru["addr3"], baru["city"], baru["state"], baru["zip"], oleh)


async def _lepas_bawaan(conn, tenant_id: str, customer_id, kolom: str, kecuali) -> None:
    await conn.execute(
        f"""UPDATE customer_addresses SET {kolom} = false, updated_at = NOW()
            WHERE tenant_id = $1 AND customer_id = $2 AND {kolom} AND id <> $3""",
        tenant_id, customer_id, kecuali)


async def _catat(conn, tenant_id: str, customer_id, uraian: str, rinci: Optional[str], oleh, nama) -> None:
    await conn.execute(
        """INSERT INTO customer_activities (customer_id, tenant_id, type, description, details, actor_id, actor_name)
           VALUES ($1, $2, 'updated', $3, $4, $5, $6)""",
        customer_id, tenant_id, uraian, rinci, oleh, nama)


async def tambah(conn, tenant_id: str, customer_id, data: dict, tagih: bool = False, kirim: bool = False,
                 oleh: Optional[str] = None, nama: Optional[str] = None) -> dict:
    """Alamat baru. Alamat aktif PERTAMA pelanggan otomatis jadi bawaan tagih + kirim (NetSuite)."""
    oleh = _teks(oleh)
    await _kunci_pelanggan(conn, tenant_id, customer_id)
    a = bersihkan(data)
    _tolak_kosong(a)
    pertama = not await conn.fetchval(
        "SELECT 1 FROM customer_addresses WHERE tenant_id = $1 AND customer_id = $2 AND is_active LIMIT 1",
        tenant_id, customer_id)
    tagih, kirim = tagih or pertama, kirim or pertama
    kolom = [k for k in MEDAN if k in a]
    aid = await conn.fetchval(
        f"""INSERT INTO customer_addresses (tenant_id, customer_id, {", ".join(kolom)}, created_by, updated_by)
            VALUES ($1, $2, {", ".join(f"${i + 3}" for i in range(len(kolom)))}, ${len(kolom) + 3}, ${len(kolom) + 3})
            RETURNING id""",
        tenant_id, customer_id, *[a[k] for k in kolom], oleh)
    await _jadikan(conn, tenant_id, customer_id, aid, tagih, kirim)
    r = await _baris(conn, tenant_id, customer_id, aid)
    await _catat(conn, tenant_id, customer_id, "Alamat ditambah", format_alamat(dict(r)), oleh, nama)
    return ke_json(r)


async def _jadikan(conn, tenant_id: str, customer_id, aid, tagih: bool, kirim: bool) -> None:
    # lepas bawaan lama DULU (indeks unik parsial tak tertunda), baru pasang
    if tagih:
        await _lepas_bawaan(conn, tenant_id, customer_id, "is_default_billing", aid)
    if kirim:
        await _lepas_bawaan(conn, tenant_id, customer_id, "is_default_shipping", aid)
    if tagih or kirim:
        await conn.execute(
            f"""UPDATE customer_addresses SET {", ".join(c for c, y in (("is_default_billing = true", tagih),
                                                                     ("is_default_shipping = true", kirim)) if y)},
                    updated_at = NOW() WHERE id = $1 AND tenant_id = $2""", aid, tenant_id)
    if tagih:
        await cermin_ke_datar(conn, tenant_id, customer_id)


async def ubah(conn, tenant_id: str, customer_id, address_id, data: dict, oleh: Optional[str] = None,
               nama: Optional[str] = None) -> dict:
    """Ubah HANYA medan yang dikirim (model_fields_set pemanggil)."""
    oleh = _teks(oleh)
    await _kunci_pelanggan(conn, tenant_id, customer_id)
    lama = await _baris(conn, tenant_id, customer_id, address_id)
    a = bersihkan(data)
    if not a:
        return ke_json(lama)
    gabung = {**{k: lama[k] for k in MEDAN}, **a}
    _tolak_kosong(gabung)
    kolom = list(a)
    await conn.execute(
        f"""UPDATE customer_addresses SET {", ".join(f"{k} = ${i + 3}" for i, k in enumerate(kolom))},
                updated_at = NOW(), updated_by = ${len(kolom) + 3}
            WHERE id = $1 AND tenant_id = $2""",
        address_id, tenant_id, *[a[k] for k in kolom], oleh)
    if lama["is_default_billing"]:
        await cermin_ke_datar(conn, tenant_id, customer_id)
    baru = await _baris(conn, tenant_id, customer_id, address_id)
    if format_alamat(dict(lama)) != format_alamat(dict(baru)):
        await _catat(conn, tenant_id, customer_id, "Alamat diubah",
                     f"{format_alamat(dict(lama)) or '-'} -> {format_alamat(dict(baru)) or '-'}", oleh, nama)
    return ke_json(baru)


async def jadikan_bawaan(conn, tenant_id: str, customer_id, address_id, tagih: bool, kirim: bool,
                         oleh: Optional[str] = None, nama: Optional[str] = None) -> dict:
    oleh = _teks(oleh)
    await _kunci_pelanggan(conn, tenant_id, customer_id)
    r = await _baris(conn, tenant_id, customer_id, address_id)
    await _jadikan(conn, tenant_id, customer_id, address_id, tagih, kirim)
    jenis = " & ".join(j for j, y in (("tagih", tagih), ("kirim", kirim)) if y)
    if jenis:
        await _catat(conn, tenant_id, customer_id, f"Alamat bawaan {jenis} diganti", format_alamat(dict(r)), oleh, nama)
    return ke_json(await _baris(conn, tenant_id, customer_id, address_id))


async def nonaktifkan(conn, tenant_id: str, customer_id, address_id, oleh: Optional[str] = None,
                      nama: Optional[str] = None) -> None:
    """Hapus = nonaktif (dokumen bisa merujuk). Bawaan ditolak 409 ALAMAT_BAWAAN: pindahkan bawaan dulu."""
    oleh = _teks(oleh)
    await _kunci_pelanggan(conn, tenant_id, customer_id)
    r = await _baris(conn, tenant_id, customer_id, address_id)
    if r["is_default_billing"] or r["is_default_shipping"]:
        raise HTTPException(status_code=409, detail={
            "code": "ALAMAT_BAWAAN",
            "message": "Alamat ini masih jadi alamat bawaan. Pilih alamat bawaan lain dulu."})
    await conn.execute(
        """UPDATE customer_addresses SET is_active = false, updated_at = NOW(), updated_by = $3
           WHERE id = $1 AND tenant_id = $2""", address_id, tenant_id, oleh)
    await _catat(conn, tenant_id, customer_id, "Alamat dihapus", format_alamat(dict(r)), oleh, nama)


async def hitung_pindah_gabung(conn, tenant_id: str, sumber: list) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM customer_addresses WHERE tenant_id = $1 AND customer_id = ANY($2::uuid[]) AND is_active",
        tenant_id, sumber)


async def pindah_saat_gabung(conn, tenant_id: str, sasaran, sumber: list) -> int:
    """Penggabungan pelanggan: alamat aktif pelanggan sumber pindah ke tujuan sebagai alamat BIASA (bawaan tujuan
    tetap). Yang nonaktif ikut pindah (riwayat rujukan SO tetap utuh). -> jumlah alamat aktif yang pindah."""
    n = await hitung_pindah_gabung(conn, tenant_id, sumber)
    await conn.execute(
        """UPDATE customer_addresses SET customer_id = $1, is_default_billing = false, is_default_shipping = false,
               updated_at = NOW() WHERE tenant_id = $2 AND customer_id = ANY($3::uuid[])""",
        sasaran, tenant_id, sumber)
    return n
