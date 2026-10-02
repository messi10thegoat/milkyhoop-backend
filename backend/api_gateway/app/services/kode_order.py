"""Kode order produksi (2 Okt 2026, pemilik + MASTER; pola SAP number range / NetSuite auto-generated numbers).

Nomor SO tetap otomatis dan BOLEH bolong (jejak audit). Kode order terpisah: format per tenant (templat), urut per
periode reset TANPA bolong, terbit OTOMATIS pada pemicu yang dipilih tenant (bawaan: uang masuk PERTAMA untuk SO --
DP, penerimaan faktur SO, atau penerapan deposit/kredit pelanggan ke faktur SO; nota kredit BUKAN uang masuk).
Tak pernah diterbitkan ulang, tak pernah dikosongkan; DP di-void -> kode tetap. Ganti manual (allow_override) =
nilai lain yang unik + peristiwa audit; nomor lamanya jadi 'lubang' yang tercatat (old_code).

Satu-satunya penulis order_code: modul ini. Tabel: V359 (order_code_settings / _counters / _events).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

TOKEN = re.compile(r"\{(SEQ|MM|YY|YYYY)\}")
RESET = ("never", "yearly", "monthly")
PEMICU = ("first_payment", "so_confirmed", "manual_only")
PERAN_UBAH = ("OWNER", "ADMIN")
BAWAAN = {"enabled": False, "template": "{SEQ}", "min_digits": 3, "reset": "never",
          "trigger": "first_payment", "allow_override": False}
MAKS_KODE, MAKS_JUDUL = 40, 60


class KodeOrderGalat(ValueError):
    """Galat masukan (-> 422 di router)."""


def validasi_setelan(template: str, min_digits: int, reset: str, trigger: str) -> None:
    """Aturan yang SAMA dengan CHECK V359 (chk_ocs_seq / chk_ocs_reset_token) + token yang dikenal saja."""
    if not isinstance(template, str) or not template.strip() or len(template) > MAKS_KODE:
        raise KodeOrderGalat(f"Templat wajib diisi, maksimal {MAKS_KODE} karakter.")
    if template.count("{SEQ}") != 1:
        raise KodeOrderGalat("Templat wajib memuat tepat satu {SEQ}.")
    sisa = TOKEN.sub("", template)
    if "{" in sisa or "}" in sisa:
        raise KodeOrderGalat("Token yang dikenal hanya {SEQ}, {MM}, {YY}, {YYYY}.")
    if any(ord(c) < 32 for c in template):
        raise KodeOrderGalat("Templat memuat karakter tak sah.")
    if not isinstance(min_digits, int) or not 1 <= min_digits <= 9:
        raise KodeOrderGalat("Jumlah digit minimal 1 sampai 9.")
    if reset not in RESET:
        raise KodeOrderGalat(f"Reset harus salah satu dari {list(RESET)}.")
    if trigger not in PEMICU:
        raise KodeOrderGalat(f"Pemicu harus salah satu dari {list(PEMICU)}.")
    if reset == "monthly" and "{MM}" not in template:
        raise KodeOrderGalat("Reset bulanan wajib memuat {MM} supaya kode tak bentrok antarbulan.")
    if reset == "yearly" and "{YY}" not in template and "{YYYY}" not in template:
        raise KodeOrderGalat("Reset tahunan wajib memuat {YY} atau {YYYY} supaya kode tak bentrok antartahun.")


def kunci_periode(reset: str, tgl: date) -> str:
    return {"never": "ALL", "yearly": f"{tgl.year:04d}", "monthly": f"{tgl.year:04d}-{tgl.month:02d}"}[reset]


def render(template: str, seq: int, min_digits: int, tgl: date) -> str:
    nilai = {"SEQ": str(seq).zfill(min_digits), "MM": f"{tgl.month:02d}", "YY": f"{tgl.year % 100:02d}",
             "YYYY": f"{tgl.year:04d}"}
    return TOKEN.sub(lambda m: nilai[m.group(1)], template)


def urai(template: str, kode: str) -> Optional[dict]:
    """Kode cocok templat? -> {seq, MM?, YY?, YYYY?} (angka), else None. Dipakai ganti manual & impor untuk
    menaikkan penghitung periode itu bila seq-nya melampaui."""
    bagian, pos = [], 0
    for m in TOKEN.finditer(template):
        bagian.append(re.escape(template[pos:m.start()]))
        bagian.append({"SEQ": r"(?P<SEQ>\d+)", "MM": r"(?P<MM>\d{2})", "YY": r"(?P<YY>\d{2})",
                       "YYYY": r"(?P<YYYY>\d{4})"}[m.group(1)])
        pos = m.end()
    bagian.append(re.escape(template[pos:]))
    m = re.fullmatch("".join(bagian), kode or "")
    if not m:
        return None
    hasil = {k: int(v) for k, v in m.groupdict().items() if v is not None}
    if "MM" in hasil and not 1 <= hasil["MM"] <= 12:
        return None
    return hasil


def periode_dari_urai(reset: str, u: dict) -> Optional[str]:
    if reset == "never":
        return "ALL"
    tahun = u.get("YYYY") if "YYYY" in u else (2000 + u["YY"] if "YY" in u else None)
    if tahun is None:
        return None
    if reset == "yearly":
        return f"{tahun:04d}"
    return f"{tahun:04d}-{u['MM']:02d}" if "MM" in u else None


def normal_kode(kode) -> str:
    if not isinstance(kode, str):
        raise KodeOrderGalat("Kode order wajib berupa teks.")
    k = kode.strip()
    if not k:
        raise KodeOrderGalat("Kode order tidak boleh dikosongkan.")
    if len(k) > MAKS_KODE or any(ord(c) < 32 for c in k):
        raise KodeOrderGalat(f"Kode order maksimal {MAKS_KODE} karakter tanpa karakter kontrol.")
    return k


def normal_judul(judul) -> Optional[str]:
    """Judul order: dipangkas, HURUF BESAR (seperti folder worksheet pemilik), maks 60; kosong -> None."""
    if judul is None:
        return None
    if not isinstance(judul, str):
        raise KodeOrderGalat("Judul order wajib berupa teks.")
    j = " ".join(judul.split()).upper()
    if not j:
        return None
    if len(j) > MAKS_JUDUL:
        raise KodeOrderGalat(f"Judul order maksimal {MAKS_JUDUL} karakter.")
    return j


async def muat_setelan(conn, tenant_id: str) -> dict:
    row = await conn.fetchrow(
        """SELECT enabled, template, min_digits, reset, trigger, allow_override FROM order_code_settings
           WHERE tenant_id = $1""", tenant_id)
    return dict(row) if row else dict(BAWAAN)


async def boleh_ganti_kode(conn, tenant_id: str, user_id, setelan: Optional[dict] = None) -> bool:
    """Server yang memutuskan pensil ganti-kode: allow_override DAN peran pemanggil OWNER/ADMIN (dibaca dari DB,
    bukan dari token)."""
    s = setelan or await muat_setelan(conn, tenant_id)
    if not s.get("allow_override") or not user_id:
        return False
    from .role_resolution import try_resolve_business_role
    return (await try_resolve_business_role(conn, str(user_id), tenant_id)) in PERAN_UBAH


async def _catat(conn, tenant_id, so_id, nomor_so, lama, baru, sumber, aktor, event, ringkas, meta=None):
    await conn.execute(
        """INSERT INTO order_code_events (tenant_id, sales_order_id, old_code, new_code, source, actor)
           VALUES ($1, $2, $3, $4, $5, $6)""", tenant_id, so_id, lama, baru, sumber, str(aktor) if aktor else None)
    from .so_riwayat import catat_riwayat
    isi = {"old": lama, "new": baru, **(meta or {})}
    await catat_riwayat(conn, tenant_id, "sales_order", so_id, nomor_so, event, aktor, ringkas, isi,
                        source="api:kode_order")


async def naikkan_penghitung(conn, tenant_id: str, setelan: dict, kode: str) -> None:
    """Kode manual/impor yang cocok templat dgn seq > penghitung periodenya -> penghitung melompat ke seq itu
    (yang otomatis berikutnya melanjutkan SESUDAHNYA). Tak cocok templat -> penghitung tak disentuh."""
    u = urai(setelan["template"], kode)
    if not u or "SEQ" not in u:
        return
    pk = periode_dari_urai(setelan["reset"], u)
    if pk is None:
        return
    await conn.execute(
        """INSERT INTO order_code_counters (tenant_id, period_key, last_seq) VALUES ($1, $2, $3)
           ON CONFLICT (tenant_id, period_key) DO UPDATE
             SET last_seq = GREATEST(order_code_counters.last_seq, EXCLUDED.last_seq), updated_at = now()""",
        tenant_id, pk, u["SEQ"])


async def terbitkan(conn, tenant_id: str, so_id, tanggal: date, pemicu: str, aktor=None,
                    dok_nomor: Optional[str] = None) -> Optional[str]:
    """Terbitkan kode untuk SO bila: setelan menyala, pemicu cocok, SO belum berkode. Idempoten. WAJIB dipanggil
    DI DALAM transaksi inti yang memicunya, SESUDAH kunci inti itu (urutan kunci tetap -> tak ada deadlock).
    pemicu: 'deposit' | 'payment' | 'deposit_application' (-> first_payment) | 'so_confirmed'.
    Tidak menyentuh jurnal/nominal apa pun."""
    if so_id is None:
        return None
    s = await muat_setelan(conn, tenant_id)
    if not s["enabled"]:
        return None
    jenis = "so_confirmed" if pemicu == "so_confirmed" else "first_payment"
    if s["trigger"] != jenis:
        return None
    # Urutan kunci TETAP: per-SO dulu, lalu per-periode. Kunci per-SO membuat dua pemicu untuk SO yang sama
    # (mis. DP + penerimaan bersamaan, beda tanggal = beda periode) berurutan -> yang kedua membaca kode yang sudah
    # terbit dan berhenti, tak menghabiskan nomor (= tak ada lubang). Tanpa FOR UPDATE baris SO supaya tak bertabrakan
    # dengan penyuntingan SO yang memegang baris lama.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE_SO:{tenant_id}:{so_id}")
    so = await conn.fetchrow(
        "SELECT id, order_number, order_code FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tenant_id)
    if not so or so["order_code"]:
        return None
    pk = kunci_periode(s["reset"], tanggal)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE:{tenant_id}:{pk}")
    for _ in range(1000):
        seq = await conn.fetchval(
            """INSERT INTO order_code_counters (tenant_id, period_key, last_seq) VALUES ($1, $2, 1)
               ON CONFLICT (tenant_id, period_key) DO UPDATE
                 SET last_seq = order_code_counters.last_seq + 1, updated_at = now()
               RETURNING last_seq""", tenant_id, pk)
        kode = render(s["template"], seq, s["min_digits"], tanggal)
        # Kode yang sudah dipakai (manual/impor di luar penghitung) dilompati -- nomor itu sudah ada, jadi tak bolong.
        if not await conn.fetchval("SELECT 1 FROM sales_orders WHERE tenant_id = $1 AND order_code = $2",
                                   tenant_id, kode):
            break
    else:
        raise RuntimeError("kode order: 1000 nomor berturut-turut sudah terpakai")
    hasil = await conn.execute(
        """UPDATE sales_orders SET order_code = $3, order_code_source = 'auto', updated_at = NOW()
           WHERE id = $1 AND tenant_id = $2 AND order_code IS NULL""", so_id, tenant_id, kode)
    if not str(hasil).endswith(" 1"):
        raise RuntimeError("kode order: SO berubah di tengah penerbitan")  # batalkan transaksi -> nomor tak terpakai
    await _catat(conn, tenant_id, so_id, so["order_number"], None, kode, "auto", aktor, "ORDER_CODE_ISSUED",
                 f"Kode order {kode} terbit", {"code": kode, "trigger": pemicu, "doc_number": dok_nomor})
    return kode


async def ganti_kode(conn, tenant_id: str, so_id, kode_baru: str, aktor) -> dict:
    """Ganti manual (NetSuite 'Allow Override'). Pemanggil sudah memeriksa boleh_ganti_kode."""
    kode = normal_kode(kode_baru)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE_SO:{tenant_id}:{so_id}")
    so = await conn.fetchrow(
        "SELECT id, order_number, order_code FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tenant_id)
    if not so:
        return {"status": 404}
    if so["order_code"] == kode:
        return {"status": 200, "order_code": kode, "unchanged": True}
    if await conn.fetchval("SELECT 1 FROM sales_orders WHERE tenant_id = $1 AND order_code = $2 AND id <> $3",
                           tenant_id, kode, so_id):
        return {"status": 409}
    s = await muat_setelan(conn, tenant_id)
    await conn.execute(
        """UPDATE sales_orders SET order_code = $3, order_code_source = 'manual', updated_at = NOW()
           WHERE id = $1 AND tenant_id = $2""", so_id, tenant_id, kode)
    await naikkan_penghitung(conn, tenant_id, s, kode)
    lama = so["order_code"]
    await _catat(conn, tenant_id, so_id, so["order_number"], lama, kode, "manual", aktor, "ORDER_CODE_OVERRIDDEN",
                 f"Kode order: {lama or '—'} → {kode}")
    return {"status": 200, "order_code": kode, "old_code": lama}


async def ubah_judul(conn, tenant_id: str, so_id, judul, aktor) -> dict:
    baru = normal_judul(judul)
    so = await conn.fetchrow(
        "SELECT id, order_number, order_title FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
        so_id, tenant_id)
    if not so:
        return {"status": 404}
    if so["order_title"] == baru:
        return {"status": 200, "order_title": baru, "unchanged": True}
    await conn.execute("UPDATE sales_orders SET order_title = $3, updated_at = NOW() WHERE id = $1 AND tenant_id = $2",
                       so_id, tenant_id, baru)
    from .so_riwayat import catat_riwayat
    await catat_riwayat(conn, tenant_id, "sales_order", so_id, so["order_number"], "ORDER_TITLE_CHANGED", aktor,
                        f"Judul order: {so['order_title'] or '—'} → {baru or '—'}",
                        {"old": so["order_title"], "new": baru}, source="api:kode_order")
    return {"status": 200, "order_title": baru}
