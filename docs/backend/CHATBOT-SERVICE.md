
# 📘 Dokumentasi Teknis Chatbot-Service MilkyHoop

---

## 1. Struktur Direktori & File Utama

| File/Folder                                | Deskripsi                                                                 |
|--------------------------------------------|--------------------------------------------------------------------------|
| `main.py`                                  | Entry point utama untuk chatbot_service, mengatur server gRPC, logic, dan dependensi eksternal |
| `kafka_producer.py`                        | Modul helper untuk Kafka Producer (mengirim event ke Kafka)             |
| `chatbot_pb2.py`, `chatbot_pb2_grpc.py`    | File hasil generate dari `.proto`, untuk mendefinisikan service gRPC     |
| `Dockerfile`                               | Konfigurasi image Docker untuk service ini                               |
| `requirements.txt`                         | Daftar lengkap dependensi Python                                         |
| `chatbot_schema.prisma`                    | Skema Prisma khusus untuk chatbot, lokasi: `database/schemas/`          |

---

## 2. Struktur Prisma: `chatbot_schema.prisma`

```prisma
generator client {
  provider = "prisma-client-py"
  recursive_type_depth = -1
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model ChatMessage {
  id        String   @id @default(uuid())
  sessionId String
  userId    String
  message   String
  reply     String
  createdAt DateTime @default(now())
}
```

---

## 3. Arsitektur Chatbot-Service

```
Client --> gRPC (Port 5003) --> main.py
                                ├── Redis (context)
                                ├── OpenAI (API)
                                ├── Kafka (logging)
                                └── Prisma (persist)
```

---

## 4. Alur Komunikasi gRPC

| Langkah | Proses |
|--------|--------|
| 1️⃣ | Client mengirim request `SendMessageStream` ke gRPC server |
| 2️⃣ | Server validasi token, pesan, rate limit |
| 3️⃣ | Ambil konteks dari Redis (jika ada) |
| 4️⃣ | Gabungkan context dengan pesan user |
| 5️⃣ | Kirim ke OpenAI dan dapatkan balasan |
| 6️⃣ | Kirim log ke Kafka |
| 7️⃣ | Kembalikan respons ke client |

---

## 5. Pola Koneksi Layanan Eksternal

| Layanan   | Library        | Pendekatan | Retry | Circuit Breaker | Timeout |
|-----------|----------------|------------|-------|------------------|---------|
| Redis     | `redis-py`     | RedisClient (wrapper) | ✅   | ❌               | ✅ |
| Kafka     | `confluent_kafka` | Singleton `producer` | ❌   | ❌               | ❌ |
| OpenAI    | `openai`       | `client.chat.completions.create()` | ✅ | ✅ | ✅ |

---

## 6. Environment Variables

### 📂 `.env_template`

```dotenv
# DATABASE
DATABASE_URL=postgresql://user:password@postgres:5432/milkydb

# REDIS
REDIS_HOST=redis
REDIS_PORT=6379

# OPENAI
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4o
MAX_TOKENS=1000
TEMPERATURE=0.7

# KAFKA
KAFKA_BROKER=kafka:9092
KAFKA_TOPIC=chatbot-log-topic

# GRPC
GRPC_TOKEN=your_secure_token

# RATE LIMITING
RATE_LIMIT_PREFIX=rate:user:
RATE_LIMIT_RATE=1
RATE_LIMIT_CAPACITY=10
```

---

## 7. Error & Solusi Reusable

| Error | Penyebab | Solusi | Modul Terkait |
|-------|----------|--------|---------------|
| `ModuleNotFoundError: No module named 'tiktoken'` | Belum install | Tambahkan ke `requirements.txt` | Semua modul pakai tiktoken |
| `ModuleNotFoundError: No module named 'config'` | Struktur path salah | Pastikan `config.py` bisa diimport | Semua modul pakai config |
| Kafka `No route to host` | IP Kafka salah | Gunakan `KAFKA_BROKER=kafka:9092` | Chatbot, API Gateway, RAG |
| Redis `ConnectionRefusedError` | Redis belum ready | Gunakan retry + backoff | Semua modul pakai Redis |
| Prisma Python tidak generate | Versi tidak cocok | Gunakan `prisma==0.15.0` | Semua modul pakai Prisma |
| `Invalid gRPC token` | Token tidak cocok | Sinkronkan `.env` dan client | Semua modul gRPC |

---

## 8. Logging & Observability

Contoh log:

```json
{
  "event": "message_processed",
  "user_id": "abc123",
  "trace_id": "e49212f1-3421-4d56-b1b4-c5a1234c2db1",
  "message": "Halo bot!",
  "reply": "Halo juga!",
  "timestamp": "2025-03-22T08:00:00Z"
}
```

---

## 9. Keamanan

| Fitur       | Implementasi                     |
|-------------|----------------------------------|
| Token Auth  | gRPC Interceptor (`AuthInterceptor`) |
| Rate Limit  | Redis Lua Script (Token Bucket)  |
| Circuit Breaker | `pybreaker` untuk OpenAI         |

---

## 10. Tips Pengembangan Modular

- Letakkan semua koneksi eksternal di folder `services/` atau `lib/`
- Gunakan `.env` terpusat dan `env_file` di docker-compose
- Pisahkan logic gRPC, handler, dan business logic
- Dokumentasikan semua error dalam format reusable




# 🧭 Chatbot-Service Roadmap – MilkyHoop Superapp

**Versi:** 1.0  
**Tanggal:** 2025-03-22  
**Maintainer:** MilkyHoop Dev Team  
**Deskripsi:** Roadmap pengembangan layanan chatbot_service untuk mencapai stabilitas MVP, kesiapan produksi, dan arsitektur enterprise langit ke-10.

---

## ✨ FASE 1 — MVP STABIL & DEMO-READY
🎯 *Fondasi kuat, service jalan, bisa dioperasikan tim kecil.*

| No | Task | Kategori |
|----|------|----------|
| 1️⃣ | Unit test RedisClient, RateLimiter, Tokenizer | Testing & Reliability |
| 2️⃣ | Modularisasi helper ke `common/helpers/` | Modularization |
| 3️⃣ | Dockerfile final dengan healthcheck HTTP mini | Docker & Build |
| 4️⃣ | Prometheus metrics dasar aktif | Metrics |
| 5️⃣ | Logging structured + trace ID | Observability |
| 6️⃣ | Dokumentasi utama + README + alur gRPC | Documentation |
| 7️⃣ | Validasi gRPC & sanitasi input ✅ | Security |
| 8️⃣ | Prisma dual-generate ✅ | Database Layer |
| 9️⃣ | Redis fallback + retry | Resilience |

---

## 🛠️ FASE 2 — PRODUKSI SIAP SKALA
🎯 *Scaling otomatis, observability full, CI/CD stabil.*

| No | Task | Kategori |
|----|------|----------|
| 1️⃣0️⃣ | CI/CD full GitHub Actions (test, lint, build) | Automation |
| 1️⃣1️⃣ | Distributed tracing (Jaeger/Zipkin) | Observability |
| 1️⃣2️⃣ | Load testing untuk gRPC endpoint | Testing |
| 1️⃣3️⃣ | Otomatisasi deploy ke staging/production | CI/CD |
| 1️⃣4️⃣ | Docker image security scanning (Trivy) | Security |
| 1️⃣5️⃣ | API doc (gRPCurl atau gateway Swagger) | Documentation |
| 1️⃣6️⃣ | SLO/SLA monitoring | Metrics |

---

## 🧬 FASE 3 — HARDENING & EVOLUSI INFRA LANGIT KE-10
🎯 *Platform tahan gempa, aman, backward-compatible, siap tim besar.*

| No | Task | Kategori |
|----|------|----------|
| 1️⃣7️⃣ | Chaos Engineering (simulasi Redis/Kafka down) | Resilience |
| 1️⃣8️⃣ | Backward compatibility di gRPC proto | Compatibility Layer |
| 1️⃣9️⃣ | Versioning untuk helper classes | Modularization |
| 2️⃣0️⃣ | Dependency vulnerability scanning (Snyk) | Security |
| 2️⃣1️⃣ | Auto-document changelog antar rilis | Documentation |
| 2️⃣2️⃣ | Deployment dashboard & rollback tool | CI/CD Ops |
| 2️⃣3️⃣ | Arsitektur visual + playbook developer onboarding | Docs + Ops |

---

📘 **Catatan:**  
Roadmap ini bersifat dinamis dan akan terus diperbarui seiring berkembangnya ekosistem MilkyHoop. Semua kontributor dipersilakan untuk menambahkan usulan melalui PR ke folder `docs/roadmap/`.


