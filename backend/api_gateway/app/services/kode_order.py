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
PEMICU = ("so_confirmed", "manual_only")  # putusan pemilik 2 Okt: murni SAP/NetSuite -- terbit saat SO dikonfirmasi
PERAN_UBAH = ("OWNER", "ADMIN")
LABEL_BAWAAN = "Kode order"  # label generik tenant baru (putusan 2 Okt); tenant mengganti lewat setelan (mis. "No. SPK")
LABEL_JUDUL_BAWAAN = "Judul order"  # V390 (pemilik 5 Okt): label judul juga setelan tenant (mis. "Judul SPK")


def label_kalimat(label: str) -> str:
    """Label di TENGAH kalimat: bawaan generik "Kode order" -> "kode order" (kalimat lama tak berubah); label tenant lain
    (mis. "No. SPK") apa adanya. SEMUA teks ke pengguna WAJIB memakai label setelan tenant, bukan teks tetap (U7, 5 Okt)."""
    return label.lower() if label == LABEL_BAWAAN else label


BAWAAN = {"enabled": False, "template": "{SEQ}", "min_digits": 4, "reset": "never",
          "trigger": "so_confirmed", "allow_override": False, "label": LABEL_BAWAAN, "title_label": LABEL_JUDUL_BAWAAN}
MAKS_KODE, MAKS_JUDUL, MAKS_LABEL = 40, 60, 30
# PDF: judul dipotong supaya "{SO} · {label} {kode} · {judul}" tetap SEBARIS di baris yang sudah ada
MAKS_JUDUL_CETAK = 24


class KodeOrderGalat(ValueError):
    """Galat masukan (-> 422 di router)."""


def validasi_setelan(template: str, min_digits: int, reset: str, trigger: str,
                     allow_override: Optional[bool] = None) -> None:
    """Aturan yang SAMA dengan CHECK V359 (chk_ocs_seq / chk_ocs_reset_token) + token yang dikenal saja.
    allow_override dikirim router setelan: manual_only tanpa ganti manual = tak ada yang bisa memberi kode (3 Okt)."""
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
    if trigger == "manual_only" and allow_override is False:
        raise KodeOrderGalat("Pemicu 'Manual saja' wajib dengan izin ganti manual supaya kode bisa diisi.")
    if reset == "monthly" and "{MM}" not in template:
        raise KodeOrderGalat("Reset bulanan wajib memuat {MM} supaya kode tak bentrok antarbulan.")
    if reset == "yearly" and "{YY}" not in template and "{YYYY}" not in template:
        raise KodeOrderGalat("Reset tahunan wajib memuat {YY} atau {YYYY} supaya kode tak bentrok antartahun.")


def normal_label(label, nama: str = "Label") -> str:
    """Label kode ATAU label judul (V390): aturan SAMA -- teks, dipangkas, 1..MAKS_LABEL, tanpa karakter kontrol."""
    if not isinstance(label, str):
        raise KodeOrderGalat(f"{nama} wajib berupa teks.")
    lb = " ".join(label.split())
    if not lb or len(lb) > MAKS_LABEL or any(ord(c) < 32 for c in lb):
        raise KodeOrderGalat(f"{nama} 1 sampai {MAKS_LABEL} karakter.")
    return lb


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


def normal_kode(kode, label: str = LABEL_BAWAAN) -> str:
    if not isinstance(kode, str):
        raise KodeOrderGalat(f"{label} wajib berupa teks.")
    k = kode.strip()
    if not k:
        raise KodeOrderGalat(f"{label} tidak boleh dikosongkan.")
    if len(k) > MAKS_KODE or any(ord(c) < 32 for c in k):
        raise KodeOrderGalat(f"{label} maksimal {MAKS_KODE} karakter tanpa karakter kontrol.")
    return k


def normal_judul(judul, label: str = LABEL_JUDUL_BAWAAN) -> Optional[str]:
    """Judul order: dipangkas, HURUF BESAR (seperti folder worksheet pemilik), maks 60; kosong -> None.
    `label` = label judul setelan tenant (V390) untuk teks galat."""
    if judul is None:
        return None
    if not isinstance(judul, str):
        raise KodeOrderGalat(f"{label} wajib berupa teks.")
    j = " ".join(judul.split()).upper()
    if not j:
        return None
    if len(j) > MAKS_JUDUL:
        raise KodeOrderGalat(f"{label} maksimal {MAKS_JUDUL} karakter.")
    return j


async def muat_setelan(conn, tenant_id: str) -> dict:
    row = await conn.fetchrow(
        """SELECT enabled, template, min_digits, reset, trigger, allow_override, label, title_label FROM order_code_settings
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
    pemicu: 'so_confirmed' (satu-satunya pemicu otomatis; manual_only = hanya ganti manual/impor).
    Tidak menyentuh jurnal/nominal apa pun."""
    if so_id is None:
        return None
    s = await muat_setelan(conn, tenant_id)
    if not s["enabled"]:
        return None
    if pemicu != "so_confirmed" or s["trigger"] != "so_confirmed":
        return None
    return await _terbitkan_inti(conn, tenant_id, so_id, tanggal, s, "auto", aktor,
                                 {"trigger": pemicu, "doc_number": dok_nomor})


async def _terbitkan_inti(conn, tenant_id: str, so_id, tanggal: date, s: dict, sumber: str, aktor,
                          meta: dict) -> Optional[str]:
    """Penerbitan dari penghitung -- dipakai otomatis (konfirmasi) DAN manual 'berikutnya'. None = SO tak ada /
    sudah berkode."""
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
        """UPDATE sales_orders SET order_code = $3, order_code_source = $4, updated_at = NOW()
           WHERE id = $1 AND tenant_id = $2 AND order_code IS NULL""", so_id, tenant_id, kode, sumber)
    if not str(hasil).endswith(" 1"):
        raise RuntimeError("kode order: SO berubah di tengah penerbitan")  # batalkan transaksi -> nomor tak terpakai
    await _catat(conn, tenant_id, so_id, so["order_number"], None, kode, sumber, aktor, "ORDER_CODE_ISSUED",
                 f"{s['label']} {kode} terbit" + (" (manual)" if sumber == "manual" else ""), {"code": kode, **meta})
    return kode


async def terbitkan_berikutnya(conn, tenant_id: str, so_id, tanggal: date, aktor) -> dict:
    """'+ {label}' tanpa mengetik (3 Okt, MASTER): nomor berikutnya dari penghitung yang SAMA (kunci sama),
    source='manual'. Jalan untuk pemicu apa pun selama setelan menyala. Pemanggil sudah memeriksa boleh_ganti_kode
    dan membuka transaksi."""
    s = await muat_setelan(conn, tenant_id)
    if not s["enabled"]:
        raise KodeOrderGalat(f"{s['label']} belum dinyalakan di Pengaturan.")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE_SO:{tenant_id}:{so_id}")
    so = await conn.fetchrow(
        "SELECT id, order_code FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tenant_id)
    if not so:
        return {"status": 404}
    if so["order_code"]:
        return {"status": 409, "order_code": so["order_code"]}
    kode = await _terbitkan_inti(conn, tenant_id, so_id, tanggal, s, "manual", aktor, {"trigger": "manual_next"})
    return {"status": 200, "order_code": kode, "old_code": None, "source": "manual"}


async def ganti_kode(conn, tenant_id: str, so_id, kode_baru: str, aktor) -> dict:
    """Ganti manual (NetSuite 'Allow Override'). Pemanggil sudah memeriksa boleh_ganti_kode."""
    s = await muat_setelan(conn, tenant_id)
    kode = normal_kode(kode_baru, s["label"])
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
    await conn.execute(
        """UPDATE sales_orders SET order_code = $3, order_code_source = 'manual', updated_at = NOW()
           WHERE id = $1 AND tenant_id = $2""", so_id, tenant_id, kode)
    await naikkan_penghitung(conn, tenant_id, s, kode)
    lama = so["order_code"]
    await _catat(conn, tenant_id, so_id, so["order_number"], lama, kode, "manual", aktor, "ORDER_CODE_OVERRIDDEN",
                 f"{s['label']}: {lama or '—'} → {kode}")
    return {"status": 200, "order_code": kode, "old_code": lama}


async def ubah_judul(conn, tenant_id: str, so_id, judul, aktor) -> dict:
    label_judul = (await muat_setelan(conn, tenant_id))["title_label"]
    baru = normal_judul(judul, label_judul)
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
                        f"{label_judul}: {so['order_title'] or '—'} → {baru or '—'}",
                        {"old": so["order_title"], "new": baru}, source="api:kode_order")
    return {"status": 200, "order_title": baru}


def potong_judul(judul: Optional[str]) -> Optional[str]:
    if not judul:
        return None
    return judul if len(judul) <= MAKS_JUDUL_CETAK else judul[:MAKS_JUDUL_CETAK - 1].rstrip() + "…"


async def label_cetak(conn, tenant_id: str, so_id) -> dict:
    """{order_code_cetak} = "{label} {kode} · {judul}" (judul dipotong) untuk ditempel SEBARIS pada baris nomor
    pesanan yang SUDAH ADA di PDF. so_id None / SO tanpa kode -> None (template tak mencetak apa pun -> PDF dokumen
    tanpa kode sama dengan sebelumnya)."""
    kosong = {"order_code_cetak": None, "order_code_pendek": None}
    if not so_id:
        return kosong
    r = await conn.fetchrow("SELECT order_code, order_title FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                            so_id, tenant_id)
    if not r or not r["order_code"]:
        return kosong
    label = (await muat_setelan(conn, tenant_id))["label"]
    judul = potong_judul(r["order_title"])
    return {"order_code_cetak": f"{label} {r['order_code']}" + (f" · {judul}" if judul else ""),
            "order_code_pendek": f"{label} {r['order_code']}"}  # kwitansi A5: tanpa judul


MAKS_BARIS_IMPOR = 2000


async def _cari_so(conn, tenant_id: str, b: dict):
    """-> (so_row|None, galat|None). order_number dulu; tanpa nomor -> nama pelanggan + tanggal pesanan (persis,
    tak peka huruf). Lebih dari satu kecocokan = galat 'ambigu' -- tak pernah menebak."""
    nomor = (b.get("order_number") or "").strip()
    if nomor:
        r = await conn.fetchrow("""SELECT id, order_number, order_code FROM sales_orders
                                   WHERE tenant_id = $1 AND order_number = $2""", tenant_id, nomor)
        return (r, None) if r else (None, f"Pesanan {nomor} tidak ditemukan")
    nama, tgl = (b.get("customer_name") or "").strip(), b.get("order_date")
    if not nama or not tgl:
        return None, None  # baris tanpa pengenal = reservasi nomor saja
    try:
        tgl = date.fromisoformat(str(tgl)[:10])
    except ValueError:
        return None, f"Tanggal {b.get('order_date')} tidak sah (pakai YYYY-MM-DD)"
    rows = await conn.fetch("""SELECT id, order_number, order_code FROM sales_orders
                               WHERE tenant_id = $1 AND lower(btrim(customer_name)) = lower($2) AND order_date = $3
                                 AND status <> 'draft'""", tenant_id, nama, tgl)
    if not rows:
        return None, f"Tidak ada pesanan {nama} tanggal {tgl.isoformat()}"
    if len(rows) > 1:
        return None, (f"Ambigu: {len(rows)} pesanan {nama} tanggal {tgl.isoformat()} "
                      f"({', '.join(r['order_number'] for r in rows)}) -- pakai nomor pesanan")
    return rows[0], None


async def impor(conn, tenant_id: str, baris: list, dry_run: bool, aktor) -> dict:
    """Pemanggil membuka transaksi. Laporan per baris + ringkasan + penghitung yang akan diset. dry_run=True -> nol
    tulis. Terapkan dengan galat apa pun -> tak menulis apa pun (pemanggil menolak 422 dengan laporan yang sama).
    Urutan kunci SAMA dengan terbitkan(): per-SO (urut id) lalu per-periode (urut kunci) -> tak ada deadlock."""
    if not isinstance(baris, list) or not baris:
        raise KodeOrderGalat("rows wajib berisi minimal satu baris.")
    if len(baris) > MAKS_BARIS_IMPOR:
        raise KodeOrderGalat(f"Maksimal {MAKS_BARIS_IMPOR} baris per impor.")
    s = await muat_setelan(conn, tenant_id)
    laporan, dipakai, per_so, penghitung = [], {}, {}, {}
    for i, b in enumerate(baris, start=1):
        b = b if isinstance(b, dict) else {}
        hasil = {"row": i, "order_number": b.get("order_number"), "order_code": b.get("order_code")}
        try:
            kode = normal_kode(b.get("order_code"), s["label"])
            judul = normal_judul(b.get("order_title"), s["title_label"]) if b.get("order_title") is not None else None
        except KodeOrderGalat as e:
            laporan.append({**hasil, "status": "error", "message": str(e)})
            continue
        hasil["order_code"] = kode
        if kode in dipakai:
            laporan.append({**hasil, "status": "error", "message": f"{s['label']} {kode} ganda di berkas (baris {dipakai[kode]})"})
            continue
        dipakai[kode] = i
        so, galat = await _cari_so(conn, tenant_id, b)
        if galat:
            laporan.append({**hasil, "status": "error", "message": galat})
            continue
        lain = await conn.fetchval("""SELECT order_number FROM sales_orders WHERE tenant_id = $1 AND order_code = $2
                                        AND ($3::uuid IS NULL OR id <> $3)""", tenant_id, kode, so["id"] if so else None)
        if lain:
            laporan.append({**hasil, "status": "error", "message": f"{s['label']} {kode} sudah dipakai pesanan {lain}"})
            continue
        u = urai(s["template"], kode)
        pk = periode_dari_urai(s["reset"], u) if u and "SEQ" in u else None
        if pk:
            penghitung[pk] = max(penghitung.get(pk, 0), u["SEQ"])
        if so is None:
            laporan.append({**hasil, "status": "reserve", "message": "Tanpa pesanan: hanya memajukan penghitung"
                            if pk else "Tanpa pesanan dan tak cocok templat: tak berpengaruh"})
            continue
        hasil["order_number"] = so["order_number"]
        if so["id"] in per_so:
            laporan.append({**hasil, "status": "error", "message": f"Pesanan {so['order_number']} muncul dua kali (baris {per_so[so['id']][0]})"})
            continue
        if so["order_code"] and so["order_code"] != kode:
            laporan.append({**hasil, "status": "error",
                            "message": f"Pesanan {so['order_number']} sudah memakai {label_kalimat(s['label'])} {so['order_code']} (ubah lewat ganti manual)"})
            continue
        per_so[so["id"]] = (i, kode, judul, so)
        laporan.append({**hasil, "status": "unchanged" if so["order_code"] == kode else "ok",
                        "message": f"{s['label']} sudah sama" if so["order_code"] == kode else
                                   ("Cocok templat" if u else "Tak cocok templat: diterima, penghitung tak berubah")})
    galat_n = sum(1 for x in laporan if x["status"] == "error")
    ringkas = {"rows": len(baris), "ok": sum(1 for x in laporan if x["status"] == "ok"),
               "unchanged": sum(1 for x in laporan if x["status"] == "unchanged"),
               "reserve": sum(1 for x in laporan if x["status"] == "reserve"), "error": galat_n}
    hasil_akhir = {"dry_run": dry_run, "applied": False, "summary": ringkas, "rows": laporan,
                   "counters": [{"period": k, "last_seq_at_least": v} for k, v in sorted(penghitung.items())]}
    if dry_run or galat_n:
        return hasil_akhir
    for so_id in sorted(per_so, key=str):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE_SO:{tenant_id}:{so_id}")
    for pk in sorted(penghitung):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ORDER_CODE:{tenant_id}:{pk}")
    for so_id, (i, kode, judul, so) in per_so.items():
        if so["order_code"] == kode and judul is None:
            continue
        sekarang = await conn.fetchrow(
            "SELECT order_code, order_title FROM sales_orders WHERE id = $1 AND tenant_id = $2", so_id, tenant_id)
        if sekarang["order_code"] not in (None, kode):
            raise KodeOrderGalat(f"{s['label']} pesanan {so['order_number']} berubah selama impor; ulangi pratinjau.")
        await conn.execute(
            """UPDATE sales_orders SET order_code = $3, order_code_source = CASE WHEN order_code IS NULL THEN 'import'
                                                                               ELSE order_code_source END,
                      order_title = COALESCE($4, order_title), updated_at = NOW()
               WHERE id = $1 AND tenant_id = $2""", so_id, tenant_id, kode, judul)
        if sekarang["order_code"] is None:
            await _catat(conn, tenant_id, so_id, so["order_number"], None, kode, "import", aktor, "ORDER_CODE_IMPORTED",
                         f"{s['label']} {kode} diimpor", {"code": kode})
        if judul is not None and judul != sekarang["order_title"]:
            # 3 Okt (pemilik): SETIAP perubahan judul tampil di riwayat SO "dari -> ke" -- impor juga (bentuk = ubah_judul)
            from .so_riwayat import catat_riwayat
            await catat_riwayat(conn, tenant_id, "sales_order", so_id, so["order_number"], "ORDER_TITLE_CHANGED", aktor,
                                f"{s['title_label']}: {sekarang['order_title'] or '—'} → {judul}",
                                {"old": sekarang["order_title"], "new": judul}, source="api:kode_order.impor")
    for pk, seq in penghitung.items():
        await conn.execute(
            """INSERT INTO order_code_counters (tenant_id, period_key, last_seq) VALUES ($1, $2, $3)
               ON CONFLICT (tenant_id, period_key) DO UPDATE
                 SET last_seq = GREATEST(order_code_counters.last_seq, EXCLUDED.last_seq), updated_at = now()""",
            tenant_id, pk, seq)
    hasil_akhir["applied"] = True
    return hasil_akhir


# ── Kode order di DOKUMEN ANAK (3 Okt 2026, MASTER/pemilik: sub-judul "005-10-26 · KEMEJA GMIM" di semua dokumen anak).
# Jalur SO induk SAMA dengan pemuat PDF (label_cetak di proformas/sales_invoices/deliveries/customer_deposits/
# receive_payments) supaya layar = cetak. Tenant eksplisit di KEDUA sisi setiap join (gateway BYPASSRLS).
SUMBER_SO = {
    "proforma": """SELECT d.id, d.sales_order_id AS so_id FROM proformas d
                   WHERE d.tenant_id = $1 AND d.id = ANY($2::uuid[])""",
    "sales_invoice": """SELECT d.id, d.sales_order_id AS so_id FROM sales_invoices d
                        WHERE d.tenant_id = $1 AND d.id = ANY($2::uuid[])""",
    "delivery": """SELECT f.id, si.sales_order_id AS so_id FROM invoice_fulfillments f
                   JOIN sales_invoices si ON si.id = f.invoice_id AND si.tenant_id = f.tenant_id
                   WHERE f.tenant_id = $1 AND f.id = ANY($2::uuid[])""",
    # = resolve_order_id_for_deposit: sales_order_id dulu, kalau kosong SO milik proformanya
    "customer_deposit": """SELECT d.id, COALESCE(d.sales_order_id, p.sales_order_id) AS so_id FROM customer_deposits d
                           LEFT JOIN proformas p ON p.id = d.proforma_id AND p.tenant_id = d.tenant_id
                           WHERE d.tenant_id = $1 AND d.id = ANY($2::uuid[])""",
    # penawaran (U1e, 4 Okt 2026): SO HASIL konversi = quotes.converted_to_id bila converted_to_type = 'sales_order'
    # (konversi ke faktur / belum dikonversi -> tanpa kode). Selaras sales_orders.quote_id (1:1, diukur kaos).
    "quote": """SELECT q.id, q.converted_to_id AS so_id FROM quotes q
                WHERE q.tenant_id = $1 AND q.id = ANY($2::uuid[]) AND q.converted_to_type = 'sales_order'
                  AND q.converted_to_id IS NOT NULL""",
    # nota kredit tak punya sales_order_id: lewat faktur asalnya (tanpa faktur asal -> tanpa kode)
    "credit_note": """SELECT d.id, si.sales_order_id AS so_id FROM credit_notes d
                      LEFT JOIN sales_invoices si ON si.id = d.original_invoice_id AND si.tenant_id = d.tenant_id
                      WHERE d.tenant_id = $1 AND d.id = ANY($2::uuid[])""",
    # penerimaan bisa melunasi faktur dari BEBERAPA SO -> daftar (alokasi aktif, sama dengan PDF kwitansi)
    "receive_payment": """SELECT DISTINCT rp.id, si.sales_order_id AS so_id FROM receive_payments rp
                          JOIN receive_payment_allocations rpa ON rpa.payment_id = rp.id AND rpa.tenant_id = rp.tenant_id
                               AND rpa.status = 'active'
                          JOIN sales_invoices si ON si.id = rpa.invoice_id AND si.tenant_id = rp.tenant_id
                          WHERE rp.tenant_id = $1 AND rp.id = ANY($2::uuid[]) AND si.sales_order_id IS NOT NULL""",
}


# Pencarian dokumen anak lewat SO INDUK: nomor, kode order, ATAU judul order (4 Okt 2026). Jalur induk SAMA dengan
# SUMBER_SO di atas; tenant = parameter {t} eksplisit di SETIAP subkueri (koneksi BYPASSRLS, set_config bukan pagar).
_SO_COCOK = ("SELECT so_c.id FROM sales_orders so_c WHERE so_c.tenant_id = {t} AND (so_c.order_number ILIKE {p} "
             "OR so_c.order_code ILIKE {p} OR so_c.order_title ILIKE {p})")


def sql_cari_so_induk(jenis: str, alias: str, t: str, p: str) -> str:
    """Predikat SQL: dokumen `alias` (jenis SUMBER_SO) milik SO yang nomor/kode/judulnya ILIKE {p}.
    t/p = placeholder ($1, $5, ...) tenant dan pola."""
    so = _SO_COCOK.format(t=t, p=p)
    if jenis in ("sales_invoice", "proforma"):
        return f"{alias}.sales_order_id IN ({so})"
    if jenis == "customer_deposit":
        return (f"COALESCE({alias}.sales_order_id, (SELECT p_c.sales_order_id FROM proformas p_c "
                f"WHERE p_c.id = {alias}.proforma_id AND p_c.tenant_id = {t})) IN ({so})")
    if jenis == "quote":  # SO hasil konversi (U1e)
        return f"({alias}.converted_to_type = 'sales_order' AND {alias}.converted_to_id IN ({so}))"
    if jenis in ("delivery", "credit_note"):
        kol = "invoice_id" if jenis == "delivery" else "original_invoice_id"
        return (f"{alias}.{kol} IN (SELECT si_c.id FROM sales_invoices si_c WHERE si_c.tenant_id = {t} "
                f"AND si_c.sales_order_id IN ({so}))")
    if jenis == "receive_payment":
        return (f"EXISTS (SELECT 1 FROM receive_payment_allocations rpa_c JOIN sales_invoices si_c "
                f"ON si_c.id = rpa_c.invoice_id AND si_c.tenant_id = {t} WHERE rpa_c.payment_id = {alias}.id "
                f"AND rpa_c.tenant_id = {t} AND rpa_c.status = 'active' AND si_c.sales_order_id IN ({so}))")
    raise ValueError(jenis)


async def kode_untuk_dokumen(conn, tenant_id: str, jenis: str, ids) -> dict:
    """{str(id): {order_code, order_title, order_code_label}}; receive_payment: {order_codes: [{order_code, order_title,
    order_number}], order_code_label}. SO tanpa kode / tanpa SO -> null / []. Label selalu terisi (setelan tenant).
    TIGA kueri tetap per panggilan (setelan, pasangan dokumen->SO, SO) berapa pun jumlah dokumennya -- tanpa N+1."""
    import uuid as _uuid
    ids = [i if isinstance(i, _uuid.UUID) else _uuid.UUID(str(i)) for i in ids if i]
    _st = await muat_setelan(conn, tenant_id)
    label, label_judul = _st["label"], _st["title_label"]
    banyak = jenis == "receive_payment"
    hasil = {str(i): ({"order_codes": [], "order_code_label": label, "order_title_label": label_judul} if banyak
                      else {"order_code": None, "order_title": None, "order_code_label": label, "order_title_label": label_judul}) for i in ids}
    if not ids:
        return hasil
    pasangan = await conn.fetch(SUMBER_SO[jenis], tenant_id, ids)
    so_ids = sorted({p["so_id"] for p in pasangan if p["so_id"]}, key=str)
    so = {}
    if so_ids:
        for r in await conn.fetch("""SELECT id, order_number, order_code, order_title FROM sales_orders
                                     WHERE tenant_id = $1 AND id = ANY($2::uuid[])""", tenant_id, so_ids):
            so[r["id"]] = r
    for p in pasangan:
        s = so.get(p["so_id"])
        if not s or not s["order_code"] or str(p["id"]) not in hasil:
            continue
        h = hasil[str(p["id"])]
        if banyak:
            h["order_codes"].append({"order_code": s["order_code"], "order_title": s["order_title"],
                                     "order_number": s["order_number"]})
        else:
            h["order_code"], h["order_title"] = s["order_code"], s["order_title"]
    if banyak:
        for h in hasil.values():
            h["order_codes"].sort(key=lambda x: x["order_code"])
    return hasil


async def tempel_kode(conn, tenant_id: str, jenis: str, dokumen: list, kunci: str = "id") -> list:
    """Tempelkan medan kode order ke daftar dict (di tempat). Baris yang id-nya bukan dokumen jenis ini (mis. penerimaan
    berinduk jurnal) mendapat nilai kosong, bukan galat."""
    import uuid as _uuid
    ok = []
    for d in dokumen:
        try:
            ok.append(_uuid.UUID(str(d[kunci])))
        except (ValueError, TypeError, KeyError):
            pass
    k = await kode_untuk_dokumen(conn, tenant_id, jenis, ok)
    if k:
        _v = next(iter(k.values()))
        label, label_judul = _v["order_code_label"], _v["order_title_label"]
    else:
        _st = await muat_setelan(conn, tenant_id)
        label, label_judul = _st["label"], _st["title_label"]
    kosong = ({"order_codes": [], "order_code_label": label, "order_title_label": label_judul} if jenis == "receive_payment"
              else {"order_code": None, "order_title": None, "order_code_label": label, "order_title_label": label_judul})
    for d in dokumen:
        d.update(k.get(str(d.get(kunci)), kosong))
    return dokumen


async def so_hasil_penawaran(conn, tenant_id: str, quote_ids) -> dict:
    """{str(quote_id): {sales_order_id, sales_order_number, order_code, order_title, order_code_label}} untuk SO HASIL
    konversi penawaran (U1e, 4 Okt 2026). Nama field SAMA dengan dokumen anak U0 (sales_order_id, sales_order_number,
    order_code, order_title) + order_code_label. Tak dikonversi / konversi ke faktur / SO hilang -> nilai None (label
    SELALU terisi, setelan tenant). Kode/judul/label lewat kode_untuk_dokumen (satu aturan dgn modul lain); id+nomor SO
    satu kueri tambahan -- konstan per halaman (nol N+1), tenant eksplisit di kedua sisi join."""
    import uuid as _uuid
    ids = []
    for q in quote_ids:
        try:
            ids.append(q if isinstance(q, _uuid.UUID) else _uuid.UUID(str(q)))
        except (ValueError, TypeError):
            pass
    _st = await muat_setelan(conn, tenant_id)
    label, label_judul = _st["label"], _st["title_label"]
    hasil = {str(i): {"sales_order_id": None, "sales_order_number": None, "order_code": None, "order_title": None,
                      "order_code_label": label, "order_title_label": label_judul} for i in ids}
    if not ids:
        return hasil
    kode = await kode_untuk_dokumen(conn, tenant_id, "quote", ids)
    for r in await conn.fetch(
            """SELECT q.id AS qid, so.id AS so_id, so.order_number
               FROM quotes q JOIN sales_orders so ON so.id = q.converted_to_id AND so.tenant_id = q.tenant_id
               WHERE q.tenant_id = $1 AND q.id = ANY($2::uuid[]) AND q.converted_to_type = 'sales_order'""",
            tenant_id, ids):
        h = hasil[str(r["qid"])]
        h["sales_order_id"], h["sales_order_number"] = str(r["so_id"]), r["order_number"]
        k = kode.get(str(r["qid"]))
        if k:
            h["order_code"], h["order_title"], h["order_code_label"] = k["order_code"], k["order_title"], k["order_code_label"]
            h["order_title_label"] = k["order_title_label"]
    return hasil

