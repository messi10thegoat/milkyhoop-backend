# 🧱 PONDASI ANTI GEMPA — API Gateway MilkyHoop

Dokumentasi milestone pertama pembangunan ulang API Gateway MilkyHoop yang future-proof, modular, dan bebas konflik warisan lama.

---

## ✅ 1. Panduan Roadmap yang Dijalankan

| Tahap | Fokus |
|-------|-------|
| Tahap 1 | Setup struktur minimal API Gateway (FastAPI, routers, config) |
| Tahap 2 | Modularisasi folders (`routers/`, `core/`, `main.py`) |
| Tahap 3 | Buat endpoint `/healthz` |
| Tahap 4 | Setup `mypy.ini`, `pyproject.toml` dengan linting ketat |
| Tahap 5 | Buat `Dockerfile` future-proof (multi-stage) |
| Tahap 6 | Uji lokal pakai `uvicorn` |
| Tahap 7 | Validasi type hint dengan `mypy` tanpa error |

---

## ⚠️ 2. Error yang Muncul

```bash
mypy: can't read file 'app': No such file or directory
mypy: Source file found twice under different module names: "health" and "app.routers.health"
app/routers/health.py: error: Function is missing a return type annotation  [no-untyped-def]



🔧 3. Cara Perbaikan Error

Error	Solusi
can't read file	Jalankan mypy dari dalam folder api_gateway
found twice under different module names	Tambahkan __init__.py di: app/, routers/, dan core/
missing return type annotation	Tambahkan -> dict atau -> Dict[str, str] di handler FastAPI



🧪 4. Cara Validasi

Langkah	Perintah
Jalankan server lokal	uvicorn app.main:app --reload
Test endpoint	Akses http://localhost:8000 dan /healthz
Validasi type hint	mypy app/ hasil: Success: no issues found
Validasi struktur package	Cek bahwa semua folder ada __init__.py



💾 Perintah Backup

Jalankan dari root proyek:

cp -r backend/api_gateway ~/Dropbox/MILKYHOOP\ BACKUP/API\ GATEWAY/PONDASI\ ANTI\ GEMPA
Dokumentasi ini ditulis otomatis oleh sistem MilkyHoop untuk menjaga kualitas teknis dan transfer knowledge yang bersih.

---

📌 Kalau kamu mau, tinggal paste ke file:

```bash
nano ~/Dropbox/MILKYHOOP\ BACKUP/API\ GATEWAY/PONDASI\ ANTI\ GEMPA/pondasi-anti-gempa.md





📘 Dokumentasi Singkat Milestone: Prisma Lifespan Integration

🎯 Tujuan:
Mengintegrasikan Prisma Python Client ke dalam FastAPI Gateway menggunakan pendekatan lifespan yang future-proof, tanpa konflik import, tanpa error mypy, dan siap digunakan seluruh modul lain.

✅ Yang Telah Diselesaikan:
Item	Status
Import Prisma Python dari libs/milkyhoop_prisma	✅
Konfigurasi lifespan dengan @asynccontextmanager	✅
Fungsi startup/shutdown Prisma clean	✅
Tidak ada lagi shadowing types.py atau prisma.Prima error	✅
Validasi ketat via mypy app/ (100% clean)	✅
Prisma siap digunakan di semua route/router/service	✅
📂 Struktur Final (Ringkas)
backend/api_gateway/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   └── database.py
│   ├── routers/
│   │   ├── __init__.py
│   │   └── health.py
│   └── __init__.py
├── libs/
│   └── milkyhoop_prisma/  ← hasil generate Prisma Python
├── pyproject.toml
├── mypy.ini
└── Dockerfile
📦 Perintah Backup ke Dropbox

🔐 Lokasi:
~/Dropbox/MILKYHOOP BACKUP/API GATEWAY/PONDASI ANTI GEMPA
📌 Jalankan dari root proyek milkyhoop/:
rm -rf ~/Dropbox/MILKYHOOP\ BACKUP/API\ GATEWAY/PONDASI\ ANTI\ GEMPA
cp -r backend/api_gateway ~/Dropbox/MILKYHOOP\ BACKUP/API\ GATEWAY/PONDASI\ ANTI\ GEMPA



from pathlib import Path

# Path ke file dokumentasi baru
doc_path = Path("docs/backend/api_gateway-hibrid.md")

# Konten dokumentasi dari jawaban sebelumnya
documentation_content = """
# 📘 Dokumentasi Teknis — API Gateway MilkyHoop (Docker + Prisma Hybrid)

## 1. 📌 Overview
`api_gateway` adalah gerbang utama semua request REST untuk MilkyHoop.  
Modul ini bertugas:

- Mengelola autentikasi & authorisasi.
- Menyambungkan permintaan user ke berbagai layanan backend (microservices).
- Berinteraksi dengan database PostgreSQL melalui Prisma Client Python.
- Menggunakan Prisma Hybrid (JS + Python) agar Prisma Python dapat berjalan di production.

## 2. 📁 Struktur Folder
```bash
backend/api_gateway/
├── app/
│   ├── core/
│   │   └── database.py          # Prisma client init
│   ├── routers/
│   │   ├── health.py            # Endpoint /healthz
│   │   └── users.py             # Placeholder user route
│   └── main.py                  # Entrypoint FastAPI
├── libs/
│   └── milkyhoop_prisma/        # Prisma Python Client
│       └── engine/              # Query engine binary
├── Dockerfile                   # Multi-stage Dockerfile
├── pyproject.toml
├── poetry.lock


from pathlib import Path

# Path ke file dokumentasi baru
doc_path = Path("docs/backend/api_gateway-hibrid.md")

# Konten dokumentasi dari jawaban sebelumnya
documentation_content = """
# 📘 Dokumentasi Teknis — API Gateway MilkyHoop (Docker + Prisma Hybrid)

## 1. 📌 Overview
`api_gateway` adalah gerbang utama semua request REST untuk MilkyHoop.  
Modul ini bertugas:

- Mengelola autentikasi & authorisasi.
- Menyambungkan permintaan user ke berbagai layanan backend (microservices).
- Berinteraksi dengan database PostgreSQL melalui Prisma Client Python.
- Menggunakan Prisma Hybrid (JS + Python) agar Prisma Python dapat berjalan di production.

## 2. 📁 Struktur Folder
```bash
backend/api_gateway/
├── app/
│   ├── core/
│   │   └── database.py          # Prisma client init
│   ├── routers/
│   │   ├── health.py            # Endpoint /healthz
│   │   └── users.py             # Placeholder user route
│   └── main.py                  # Entrypoint FastAPI
├── libs/
│   └── milkyhoop_prisma/        # Prisma Python Client
│       └── engine/              # Query engine binary
├── Dockerfile                   # Multi-stage Dockerfile
├── pyproject.toml
├── poetry.lock
3. ⚙️ Konfigurasi Environment

📂 Local .env
Always show details

DATABASE_URL="postgresql://postgres:yourpassword@host:5432/dbname?sslmode=require"
🐳 Saat Docker Run:
Always show details

docker run -p 8000:8000 \\
  -e DATABASE_URL="postgresql://..." \\
  milkyhoop-api_gateway:dev
❗ Penting:
Jangan hardcode DATABASE_URL di Dockerfile — selalu inject via -e
PRISMA_QUERY_ENGINE_BINARY di-set lewat main.py
4. 🐳 Docker Setup (Multi-Stage Build)

🔨 STAGE 1: Builder
Install Node.js dan Prisma JS CLI v5.17.0
Generate Prisma JS Client → menghasilkan binary query engine
Generate Prisma Python Client → masuk ke folder libs/
🏃 STAGE 2: Runtime
Copy hasil generate dari builder
Copy binary query-engine-* dan rename ke:
Always show details

/app/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.0.x
Jalankan uvicorn app.main:app
5. 🧠 Prisma Hybrid Logic

🔁 Alur Hybrid:
Prisma JS CLI (npx prisma generate)
→ menghasilkan binary query engine di node_modules/.prisma/.
Binary Prisma JS dipindahkan ke:
Always show details

/app/libs/milkyhoop_prisma/engine/query-engine-debian-openssl-3.0.x
Prisma Python Client (prisma generate)
→ membaca schema.prisma, menghasilkan Python client di libs/milkyhoop_prisma/.
Runtime:
Python client akan mencari binary engine di path environment PRISMA_QUERY_ENGINE_BINARY.
6. 🧪 Endpoint Testing

✅ GET /
Always show details

curl http://localhost:8000/
# Response:
# {"message": "Welcome to MilkyHoop API Gateway"}
✅ GET /healthz/
Always show details

curl http://localhost:8000/healthz/
# Response:
# {"status": "ok", "timestamp": "2025-03-28 12:03:56.800Z"}
❗ CATATAN:
Jangan akses /healthz tanpa slash (/healthz) → akan redirect (307).
Selalu pakai: curl http://localhost:8000/healthz/
7. 🔥 Error & Solusi Umum

🧨 Error	💡 Solusi
BinaryNotFoundError	Pastikan path PRISMA_QUERY_ENGINE_BINARY mengarah ke binary valid
EngineConnectionError	Pastikan DATABASE_URL diset dengan benar & koneksi DB terbuka
Environment variable not found: DATABASE_URL	Pastikan -e DATABASE_URL=... disertakan saat docker run
GET /healthz → tidak merespon	Tambahkan slash di akhir: curl /healthz/
8. 💾 Backup & Restore

🔄 Backup Project:
Always show details

zip -r -q ~/Dropbox/MILKYHOOP\\ BACKUP/MILKYHOOP2.0/28MARET-API\\ GATEWAY-FULLDOCKERIZED.zip . \\
  -x "*/node_modules/*" "*.dmg" "*.pem" "*.sql" "*.pyc" "__pycache__/*" ".venv/*"
📁 Tips Backup:
Gunakan tanggal dan nama modul
Simpan di folder cloud yang terorganisir (Dropbox, GDrive, dsb)
Backup .env, .md, dan semua file Dockerfile, pyproject.toml, dan Prisma schema
9. 🚀 Future Enhancements

🔐 Tambahkan OAuth2 middleware (Google Sign-In)
🛡️ Middleware RBAC per endpoint
📊 Prometheus untuk monitoring query DB
🧪 Test gRPC health dari API Gateway ke microservices
📦 Build ke DockerHub milkyhoop/api_gateway:latest
10. 📎 Referensi Teknis

Komponen	Link
Prisma JS CLI v5.17.0	https://www.prisma.io/docs/reference/api-reference/command-reference#generate
Prisma Python	https://github.com/RobertCraigie/prisma-client-py
FastAPI Lifespan	https://fastapi.tiangolo.com/advanced/events/#lifespan
Prisma Binary Engine	https://www.prisma.io/docs/concepts/components/prisma-engines/query-engine
Prisma Python Env Path	https://github.com/RobertCraigie/prisma-client-py/issues/312
"""	
Tulis file ke dalam direktori dokumentasi

doc_path.parent.mkdir(parents=True, exist_ok=True) doc_path.write_text(documentation_content.strip(), encoding="utf-8")

"✅ Dokumentasi berhasil disimpan ke docs/backend/api_gateway-hibrid.md"


