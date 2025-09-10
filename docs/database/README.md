# Dokumentasi Database MilkyHoop

## 1. Pendahuluan
MilkyHoop menggunakan pendekatan **Hybrid Database Architecture** yang menggabungkan database **terpusat** dan **modular per layanan**.

## 2. Jenis Database yang Digunakan
| Komponen         | Jenis Database | Fungsi |
|-----------------|--------------|--------|
| **PostgreSQL**  | Relational DB | Database utama untuk user, transaksi, dan bisnis |
| **Redis**       | In-Memory Cache | Cache untuk mempercepat query dan menyimpan sesi pengguna |
| **MongoDB**     | NoSQL Document DB | Menyimpan metadata chatbot dan riwayat percakapan |
| **FAISS**       | Vector DB | Menyimpan embedding AI untuk RAG (Retrieval-Augmented Generation) |
| **Kafka**       | Event Streaming | Sinkronisasi data antar layanan |

## 3. Struktur Database
Database terbagi menjadi:
1. **Database Terpusat (PostgreSQL)** untuk user management, autentikasi, transaksi, dan log.
2. **Database Modular** untuk layanan dengan kebutuhan spesifik:
   - **AI Chatbot (MongoDB)**
   - **AI RAG Embedding (FAISS)**
   - **Cache (Redis)**
   - **Event Streaming (Kafka)**

### 📍 Lokasi Skema:

database/ ├── database_utils/ │ ├── db.py ├── migrations/ ├── optimizations/ │ ├── indexing/ │ ├── query_optimization/ │ ├── sharding_partitioning/ ├── schemas/ │ ├── global_schema.prisma # Skema database terpusat │ ├── chatbot_schema.prisma # Skema database AI chatbot └── seeders/


## 4. Cara Menjalankan Database
Jalankan database dengan Docker Compose:
```bash
docker-compose up -d postgres redis mongo


Pastikan database berjalan dengan:

docker ps | grep postgres
docker ps | grep redis
docker ps | grep mongo


5. Sinkronisasi Database

Gunakan Prisma ORM untuk update skema database:

cd database
prisma migrate dev


Untuk mengakses database PostgreSQL:

docker exec -it postgres psql -U postgres
Untuk mengakses Redis CLI:

docker exec -it redis redis-cli
Untuk mengecek isi MongoDB:

docker exec -it mongo mongosh



======================= DOKUMENTASI 2 ====================

# Dokumentasi Database MilkyHoop

## 1. Pendahuluan
MilkyHoop menggunakan **Hybrid Database Architecture** untuk mengelola data secara efisien dengan kombinasi **PostgreSQL, Redis, MongoDB, Kafka, FAISS, dan Neo4j**. 

## 2. Struktur Database

database/ ├── database_utils/ # Skrip utilitas untuk koneksi DB │ ├── db.py # Konfigurasi koneksi PostgreSQL ├── migrations/ # Skrip migrasi database ├── optimizations/ # Folder optimasi performa │ ├── indexing/ # Indexing query │ ├── query_optimization/ │ ├── sharding_partitioning/ ├── schemas/ # Skema Prisma untuk database │ ├── global_schema.prisma # Schema utama (PostgreSQL) │ ├── chatbot_schema.prisma # Schema chatbot (MongoDB, VectorDB) ├── seeders/ # Data awal untuk pengujian


## 3. Database yang Digunakan
| Komponen         | Jenis Database       | Deskripsi |
|-----------------|----------------------|-----------|
| **PostgreSQL**  | Relational DB        | Database utama untuk pengguna, bisnis, transaksi, autentikasi |
| **Redis**       | In-Memory Cache      | Menyimpan data sesi, autentikasi, dan cache query |
| **MongoDB**     | NoSQL Document DB    | Metadata chatbot dan riwayat percakapan pengguna |
| **Kafka**       | Event Streaming      | Sinkronisasi antar database dan layanan |
| **FAISS**       | Vector DB            | Penyimpanan embedding untuk AI Persona & AI Assistant (RAG) |
| **Neo4j**       | Graph DB             | Menyimpan hubungan antar pengguna & fitur jaringan sosial |

## 4. Optimasi Database
✅ **PostgreSQL**: 
- **Indexing** pada kolom yang sering digunakan dalam query.
- **Sharding** menggunakan CitusDB (jika perlu untuk skala besar).
- **Read-Replica & Load Balancing** untuk beban tinggi.

✅ **Redis**:
- **TTL (Time-To-Live)** untuk mengelola cache otomatis.
- **Sentinel & Clustering** untuk high availability.

✅ **Kafka**:
- **Partitioning** untuk membagi beban streaming data.

## 5. Cara Menjalankan Database
Gunakan **Docker Compose** untuk menjalankan database:

```bash
docker-compose up -d postgres redis mongodb kafka

Cek apakah database berjalan dengan baik:

docker ps | grep postgres
docker ps | grep redis
docker ps | grep mongodb
docker ps | grep kafka




=========== DOKUMENTASI 2 ===============


# Dokumentasi Database MilkyHoop

## 1. Pendahuluan
MilkyHoop menggunakan **Hybrid Database Architecture**, menggabungkan database **terpusat** (PostgreSQL) dan **terisolasi** (Redis, MongoDB, Kafka, Vector DB) untuk skalabilitas dan efisiensi.

## 2. Struktur Database
database/ ├── database_utils/ # Helper untuk koneksi database │ ├── db.py # Skrip koneksi utama ke PostgreSQL ├── schemas/ # Skema database Prisma │ ├── global_schema.prisma # Skema utama PostgreSQL │ ├── chatbot_schema.prisma # Skema chatbot (MongoDB) ├── optimizations/ # Optimasi database │ ├── indexing/ # Strategi indexing untuk query cepat │ ├── query_optimization/ # Optimasi query SQL │ ├── sharding_partitioning/ # Teknik sharding & partitioning ├── migrations/ # Skrip migrasi database │ ├── README.md # Panduan migrasi database ├── seeders/ # Data awal untuk pengujian │ ├── README.md # Panduan seeding database


## 3. Teknologi yang Digunakan
| Komponen             | Teknologi        | Deskripsi |
|---------------------|----------------|-----------|
| **Database Utama**  | PostgreSQL      | Penyimpanan data pengguna & transaksi |
| **Caching & Session** | Redis         | Cache cepat untuk session & autentikasi |
| **Log & Event**     | Kafka           | Streaming data & sinkronisasi layanan |
| **Vector Database** | FAISS / Pinecone | Penyimpanan embedding AI |
| **Graph Database**  | Neo4j / Nebula  | Rekomendasi & hubungan sosial |

## 4. Cara Menjalankan Database
Jalankan database menggunakan **Docker Compose**:

```bash
docker-compose up -d postgres redis mongo kafka
Cek apakah database berjalan:

docker ps | grep postgres
docker ps | grep redis
docker ps | grep mongo
docker ps | grep kafka
5. Skema Database

PostgreSQL (global_schema.prisma)
Digunakan untuk data utama seperti pengguna, transaksi, autentikasi, dan keuangan.

model User {
  id            String   @id @default(uuid())
  email         String   @unique
  username      String?  @unique
  passwordHash  String?
  isVerified    Boolean  @default(false)
  role          Role     @default(FREE)
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt
  accounts      Account[]
  sessions      Session[]
}
MongoDB (chatbot_schema.prisma)
Digunakan untuk menyimpan metadata chatbot dan riwayat percakapan pengguna.

model ChatbotSession {
  id         String    @id @default(uuid())
  userId     String
  messages   Json
  createdAt  DateTime  @default(now())
}
Redis (Session & Caching)
Digunakan untuk menyimpan data sementara, seperti token autentikasi.

import redis

redis_client = redis.Redis(
    host="localhost",
    port=6379,
    db=0,
    decode_responses=True
)

redis_client.set("test_key", "MilkyHoop is running!")
print(redis_client.get("test_key"))
Kafka (Streaming Data)
Digunakan untuk menyinkronkan data antar layanan.

from confluent_kafka import Producer

producer = Producer({'bootstrap.servers': 'localhost:9092'})
producer.produce('test_topic', value="Halo MilkyHoop!")
producer.flush()
6. Backup & Restore Database

PostgreSQL
Backup database dengan perintah:

pg_dump -U postgres -d milkyhoop > backup.sql
Restore database:

psql -U postgres -d milkyhoop < backup.sql
Redis
Simpan snapshot Redis:

redis-cli save
Restore dengan:

cp /var/lib/redis/dump.rdb /path/to/backup/



========== doc 4================


# Dokumentasi Database MilkyHoop

## 1. Pendahuluan
MilkyHoop menggunakan pendekatan **Hybrid Database Architecture**, yang mengkombinasikan **PostgreSQL** (database utama), **Redis** (caching), **Kafka** (event streaming), **MongoDB** (metadata chatbot), dan **Vector Database** (Pinecone/FAISS).

---

## 2. Struktur Database
Struktur database dibagi menjadi dua kategori utama:

1. **Database Terpusat (PostgreSQL)**  
   - Menyimpan data lintas layanan seperti user management, bisnis, transaksi, dan autentikasi.  
   - Lokasi skema: `database/schemas/global_schema.prisma`

2. **Database Terisolasi untuk Layanan Spesifik**
   - **MongoDB** → Metadata chatbot & riwayat percakapan (`database/schemas/chatbot_schema.prisma`).
   - **FAISS / Pinecone** → Embedding AI untuk pencarian berbasis vektor (`database/vector_storage`).
   - **Redis** → Cache untuk komunikasi real-time (`database/caching`).
   - **Kafka** → Sinkronisasi event-driven antar layanan (`backend/middleware-service/kafka_events.py`).

---

## 3. Struktur Direktori Database
database/ ├── database_utils/ │ └── db.py # Koneksi utama ke PostgreSQL ├── migrations/ │ └── README.md # Panduan migrasi database ├── optimizations/ # Optimasi PostgreSQL untuk performa tinggi │ ├── indexing/ │ ├── query_optimization/ │ └── sharding_partitioning/ ├── schemas/ │ ├── global_schema.prisma # Skema utama (PostgreSQL) │ ├── chatbot_schema.prisma # Skema chatbot (MongoDB) ├── seeders/ │ └── README.md # Data awal untuk pengisian database


---

## 4. Instalasi & Koneksi Database
### 4.1. Menjalankan PostgreSQL
```bash
docker-compose up -d postgres
Cek status:

docker ps | grep postgres
Cek koneksi:

docker exec -it postgres psql -U milkyhoop -d milkyhoop_db
4.2. Menjalankan Redis
docker-compose up -d redis
Cek koneksi:

redis-cli
127.0.0.1:6379> PING
PONG
4.3. Menjalankan Kafka
docker-compose up -d kafka
Cek daftar topik Kafka:

docker exec -it kafka /opt/kafka/bin/kafka-topics.sh --list --bootstrap-server localhost:9092
5. Migrasi Database

Untuk menjalankan migrasi database di Prisma:

cd backend/services/auth-service/
prisma migrate dev --name init
Cek status migrasi:

prisma migrate status
Untuk rollback migrasi terakhir:

prisma migrate reset
6. Optimasi Database

✅ Indexing: Tambahkan index untuk query yang sering dijalankan.
✅ Sharding & Partitioning: Pisahkan data besar ke tabel lebih kecil.
✅ Connection Pooling: Gunakan PgBouncer agar koneksi PostgreSQL lebih efisien.
✅ Redis TTL: Atur Time-To-Live untuk cache agar tidak overload.
✅ Kafka Partitioning: Sesuaikan jumlah partition agar proses event streaming optimal.

7. Backup & Disaster Recovery

7.1. Backup PostgreSQL
pg_dump -U milkyhoop -d milkyhoop_db -F c -f backup.sql
7.2. Restore PostgreSQL
pg_restore -U milkyhoop -d milkyhoop_db backup.sql
7.3. Backup Redis
redis-cli SAVE

