#!/usr/bin/env python3
"""Buat tenant MilkyHoop lewat JALUR SAH, sampai tuntas + gerbang.

    echo '<kata-sandi>' | python3 scripts/ops/buat-tenant.py <email> "<Nama Bisnis>"
    python3 scripts/ops/buat-tenant.py --periksa <email>      # gerbang saja

DIJALANKAN DI SERVER (butuh docker + akses :8001).

KENAPA SKRIP INI ADA, bukan sekadar catatan langkah:
langkahnya empat, tiga di antaranya punya jebakan yang masing-masing memakan
waktu, dan dua terakhir hanya terlihat SESUDAH salah. Semua sudah dikodekan
di sini supaya tak perlu ditemukan ulang:

  1. Prefiksnya `/api/auth/signup`, BUKAN `/api/signup`. Salah prefiks memberi
     401 MISSING_TOKEN -- terbaca seperti masalah izin, padahal rutenya memang
     tak ada di situ.
  2. Kode verifikasi tersimpan TER-HASH di `pending_registrations`, jadi tak
     bisa dibaca. Yang dipakai `magic_token` di baris yang sama -- tautan yang
     SAMA dengan yang akan diklik pemilik dari emailnya.
  3. `psql` ada DI DALAM kontainer postgres, bukan di host. Memanggilnya
     langsung mengembalikan string kosong TANPA galat.
  4. Nama tabelnya `"User"` HURUF BESAR (gaya Prisma), bukan `users`; dan
     `user_tenant_roles` menyimpan `role_id`, bukan kolom `role`. Gerbang
     versi pertama salah keduanya dan melapor MERAH untuk data yang SEHAT.

NOL INSERT LANGSUNG. `create_tenant_and_user` mengisi Tenant, User."tenantId",
user_tenant_roles, lalu menyemai CoA/peran akun/kode pajak/gudang dalam SATU
transaksi. Menyisipkan sendiri melahirkan tenant setengah jadi yang gejalanya
baru muncul minggu depan.

KATA SANDI dibaca dari STDIN, tak pernah jadi argumen: argumen terlihat di
`ps` dan tersimpan di riwayat shell. Skrip ini tidak pernah mencetaknya dan
tidak pernah menuliskannya ke berkas.

⚠️ Alur ini menandai email TERVERIFIKASI tanpa orangnya mengklik apa pun.
Pakai untuk tenant milikmu sendiri atau demo yang kau kelola. Jangan
membuatkan akun atas alamat email orang lain tanpa sepengetahuannya --
klik-email itulah persetujuannya, dan skrip ini melewatinya.
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASIS = "http://localhost:8001"
PG = ["docker", "exec", "-i", "milkyhoop-dev-postgres-1",
      "psql", "-U", "postgres", "-d", "milkydb", "-At", "-c"]
gagal = []


def sql(q):
    r = subprocess.run(PG + [q], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"   ! psql gagal: {r.stderr.strip()[:200]}")
        return ""
    return r.stdout.strip()


def cek(ok, pesan):
    print(f"  {'OK  ' if ok else 'GAGAL'} {pesan}")
    if not ok:
        gagal.append(pesan)


class TanpaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def minta(metode, jalur, data=None, token=None, ikuti=True):
    req = urllib.request.Request(BASIS + jalur, method=metode)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(data).encode()
    opener = (urllib.request.build_opener() if ikuti
              else urllib.request.build_opener(TanpaRedirect))
    try:
        with opener.open(req, data, timeout=30) as r:
            body = r.read()
            try:
                return r.status, json.loads(body or b"{}"), dict(r.headers)
            except ValueError:
                return r.status, {}, dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}"), dict(e.headers)
        except ValueError:
            return e.code, {}, dict(e.headers)
    except Exception as e:  # jaringan / kontainer mati
        return 0, {"error": str(e)}, {}


def buat(email, nama, sandi):
    ada = sql(f"SELECT \"tenantId\" FROM \"User\" WHERE email='{email}'")
    if ada:
        print(f"BERHENTI: {email} sudah punya tenant '{ada}'. "
              "Skrip ini tidak menimpa pengguna yang sudah ada.")
        return 1

    print(f"1. POST /api/auth/signup/register  ({email})")
    kode, d, _ = minta("POST", "/api/auth/signup/register", {"email": email})
    print(f"   -> {kode} {str(d)[:100]}")
    if kode not in (200, 201):
        return 1

    print("2. ambil magic_token (kode verifikasi TER-HASH, tak bisa dibaca)")
    tok = sql("SELECT magic_token FROM pending_registrations "
              f"WHERE email='{email}' ORDER BY created_at DESC LIMIT 1")
    print(f"   magic_token: {tok[:8]}… ({len(tok)} karakter)")
    if not tok:
        print("   ! tak ada baris pending_registrations — periksa langkah 1")
        return 1

    print("3. GET verify-link -> setup_token dari header Location")
    kode, _, hdr = minta("GET", f"/api/auth/signup/verify-link/{tok}", ikuti=False)
    lok = hdr.get("Location") or hdr.get("location") or ""
    setup = lok.split("token=")[-1] if "token=" in lok else ""
    print(f"   -> {kode}, setup_token {len(setup)} karakter")
    if not setup:
        print(f"   ! Location tak memuat token: {lok[:160]}")
        return 1

    print(f'4. POST complete-setup  (business_name="{nama}")')
    kode, d, _ = minta("POST", "/api/auth/signup/complete-setup",
                       {"password": sandi, "business_name": nama}, token=setup)
    isi = d.get("data") or d
    aman = {k: v for k, v in isi.items() if "token" not in k.lower()} if isinstance(isi, dict) else {}
    print(f"   -> {kode} {json.dumps(aman, ensure_ascii=False)[:280]}")
    if kode not in (200, 201):
        return 1
    print(f"   slug tenant: {aman.get('tenant_id')!r}  (PERMANEN)")
    return 0


def periksa(email, sandi=None):
    print("\n=== GERBANG ===")
    slug = sql(f"SELECT \"tenantId\" FROM \"User\" WHERE email='{email}'")
    cek(bool(slug), f'User."tenantId" = {slug!r} (harus terisi; login membacanya, '
                    "bukan user_tenant_roles)")
    if not slug:
        return 1

    peran = sql("SELECT r.name FROM user_tenant_roles utr "
                "JOIN roles r ON r.id = utr.role_id "
                f"JOIN \"User\" u ON u.id::text = utr.user_id::text WHERE u.email='{email}'")
    cek(peran == "Owner", f"peran: {peran!r} (harap 'Owner')")
    st = sql("SELECT status||'/'||is_primary FROM user_tenant_roles utr "
             f"JOIN \"User\" u ON u.id::text = utr.user_id::text WHERE u.email='{email}'")
    cek(st.startswith("ACTIVE"), f"status/is_primary: {st!r}")

    n_akun = sql(f"SELECT count(*) FROM chart_of_accounts WHERE tenant_id='{slug}'")
    cek(int(n_akun or 0) > 0, f"bagan akun: {n_akun} akun")
    n_pajak = sql("SELECT count(*) FROM tax_codes WHERE tenant_id='%s'" % slug)
    n_gudang = sql("SELECT count(*) FROM warehouses WHERE tenant_id='%s'" % slug)
    print(f"       kode pajak: {n_pajak} · gudang: {n_gudang}")

    if not sandi:
        print("  (login/dashboard/isolasi dilewati — jalankan tanpa --periksa "
              "atau salurkan sandi lewat stdin)")
        return 1 if gagal else 0

    kode, d, _ = minta("POST", "/api/auth/login", {"email": email, "password": sandi})
    tok = (d.get("data") or {}).get("access_token")
    cek(kode == 200 and bool(tok), f"login -> {kode}, token {len(tok or '')} karakter")
    if not tok:
        return 1
    kode, _, _ = minta("GET", "/api/dashboard/all", token=tok)
    cek(kode == 200, f"GET /api/dashboard/all -> {kode}")

    lain = f"tenant_id <> '{slug}'"
    for jalur, label, tabel in (("/api/sales-invoices?limit=100", "faktur", "sales_invoices"),
                                ("/api/customers?limit=100", "pelanggan", "customers"),
                                ("/api/items?limit=100", "barang", "products")):
        kode, d, _ = minta("GET", jalur, token=tok)
        n = len((d.get("data") or d).get("items") or [])
        cek(kode == 200 and n == 0,
            f"isolasi {label}: tenant ini melihat {n} "
            f"(tenant lain punya {sql('SELECT count(*) FROM %s WHERE %s' % (tabel, lain))})")
    return 1 if gagal else 0


def main():
    arg = sys.argv[1:]
    if not arg or arg[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    sandi = None
    if not sys.stdin.isatty():
        sandi = sys.stdin.readline().strip() or None

    if arg[0] == "--periksa":
        if len(arg) < 2:
            print("pemakaian: --periksa <email>")
            return 2
        kode = periksa(arg[1], sandi)
    else:
        if len(arg) < 2:
            print('pemakaian: <email> "<Nama Bisnis>"  (sandi lewat stdin)')
            return 2
        email, nama = arg[0], arg[1]
        if len(nama.strip()) < 2:
            print("BERHENTI: business_name minimal 2 huruf. Slug diturunkan darinya "
                  "dan PERMANEN — jangan mengarang; tanyakan ke pemilik.")
            return 2
        if not sandi:
            print("BERHENTI: kata sandi harus disalurkan lewat stdin, bukan argumen.")
            return 2
        if buat(email, nama, sandi) != 0:
            return 1
        kode = periksa(email, sandi)

    print()
    if gagal:
        print("GAGAL:")
        for g in gagal:
            print("  - " + g)
        return 1
    print("OK: tenant sehat dan terisolasi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
