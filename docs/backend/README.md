# Dokumentasi Backend MilkyHoop

## 1. Arsitektur Backend
MilkyHoop menggunakan arsitektur **microservices** dengan FastAPI dan gRPC sebagai teknologi utama. Backend terdiri dari beberapa layanan yang berjalan secara terpisah dan berkomunikasi melalui API Gateway dan Kafka.

### 2. Layanan Backend
Berikut adalah daftar layanan yang ada di backend:
- **API Gateway**: Mengelola request dari frontend dan mengarahkan ke layanan yang sesuai.
- **Auth Service**: Mengelola autentikasi pengguna, termasuk OAuth Google.
- **Chatbot Service**: Layanan utama AI chatbot.
- **Business Service**: Mengelola data bisnis yang terhubung ke chatbot.
- **Notification Service**: Mengirimkan notifikasi ke pengguna.
- **Middleware Service**: Menangani komunikasi antar layanan menggunakan Kafka.

### 3. Teknologi yang Digunakan
- **Framework**: FastAPI
- **Messaging**: Kafka
- **Database**: PostgreSQL (terpusat), Redis (cache), MongoDB (AI chatbot)
- **Protocol**: gRPC untuk komunikasi antar layanan
- **Containerization**: Docker

### 4. Struktur Direktori
backend/ ├── api_gateway/ ├── services/ │ ├── auth-service/ │ ├── chatbot_service/ │ ├── business-service/ │ ├── notification-service/ │ ├── middleware-service/


### 5. Cara Menjalankan Backend
Jalankan semua layanan backend dengan Docker Compose:
```bash
docker-compose up -d

cd backend/services/chatbot_service
uvicorn main:app --host 0.0.0.0 --port 8000



====================== dokumentasi 2 ==========================


# Dokumentasi Backend MilkyHoop

## 1. Pendahuluan
MilkyHoop menggunakan **Microservices Architecture** berbasis **FastAPI (Python)** dan **gRPC** untuk komunikasi antar layanan. 

## 2. Struktur Backend


backend/ ├── api_gateway/ # Gateway utama untuk mengarahkan request │ ├── main.py # Entry point API Gateway │ ├── requirements.txt # Dependensi │ ├── Dockerfile # Konfigurasi Docker ├── services/ # Kumpulan layanan backend │ ├── auth-service/ # Layanan autentikasi (OAuth, JWT, RBAC) │ ├── business-service/ # Manajemen bisnis & organisasi │ ├── chatbot_service/ # AI Chatbot (gRPC + Kafka) │ ├── middleware-service/ # Middleware untuk logging & event processing │ ├── notification-service/# Manajemen notifikasi & WebSocket │ ├── rag-service/ # Retrieval-Augmented Generation (AI) │ ├── security-service/ # Manajemen keamanan & enkripsi │ ├── log-aggregator/ # Logging & monitoring layanan │ ├── scheduler-service/ # Layanan penjadwalan & background tasks │ ├── streaming-service/ # Layanan streaming real-time │ └── organization-service/# Manajemen organisasi & multi-tenant



## 3. API Gateway
API Gateway menangani semua permintaan pengguna dan meneruskannya ke layanan yang sesuai.

- **Framework**: FastAPI
- **Rate Limiting**: Dibatasi per user untuk mencegah spam
- **Autentikasi**: OAuth2 + JWT
- **Load Balancer**: HAProxy/Nginx untuk membagi trafik

## 4. Komunikasi Antar Layanan
- **gRPC** digunakan untuk komunikasi cepat antara service.
- **Kafka** digunakan untuk event-driven architecture.
- **Redis** digunakan untuk caching dan real-time session.

## 5. Cara Menjalankan Backend
Jalankan backend menggunakan **Docker Compose**:

```bash
docker-compose up -d api_gateway auth-service chatbot_service


Cek layanan yang berjalan:

docker ps | grep api_gateway
docker ps | grep auth-service
docker ps | grep chatbot_service




# Dokumentasi Backend MilkyHoop

## 1. Pendahuluan
MilkyHoop memiliki arsitektur backend berbasis **microservices**, menggunakan **FastAPI + gRPC**, dengan komunikasi antar layanan melalui **Kafka**.

## 2. Struktur Backend

backend/ ├── api_gateway/ # Gateway utama untuk menangani request │ ├── main.py # Entry point API Gateway │ ├── requirements.txt # Dependensi API Gateway ├── services/ # Layanan backend berbasis microservices │ ├── auth-service/ # Layanan autentikasi pengguna │ ├── business-service/ # Layanan bisnis & transaksi │ ├── chatbot_service/ # Layanan AI chatbot & NLP │ ├── middleware-service/ # gRPC Middleware untuk agen │ ├── notification-service/ # Layanan notifikasi & WebSocket │ ├── rag-service/ # Retrieval-Augmented Generation AI │ ├── streaming-service/ # Layanan streaming data berbasis Kafka │ └── security-service/ # Layanan keamanan & enkripsi


## 3. Teknologi yang Digunakan
| Komponen              | Teknologi         | Deskripsi |
|----------------------|------------------|-----------|
| **Framework API**    | FastAPI          | API backend utama |
| **Komunikasi Antar Layanan** | gRPC       | Protokol RPC untuk kecepatan tinggi |
| **Database**        | PostgreSQL, Redis, MongoDB | Penyimpanan data |
| **Streaming Data**  | Kafka             | Sinkronisasi & event-driven architecture |
| **Autentikasi**      | OAuth, JWT        | Login via Google OAuth, token JWT |

## 4. Cara Menjalankan Backend
Jalankan backend menggunakan **Docker Compose**:

```bash
docker-compose up -d api_gateway auth-service business-service chatbot_service



Cek apakah backend berjalan:

docker ps | grep api_gateway
docker ps | grep auth-service
docker ps | grep chatbot_service


Untuk melihat log layanan tertentu:

docker logs -f api_gateway
docker logs -f auth-service



5. API Documentation

Setiap layanan memiliki dokumentasi API otomatis dengan Swagger:

API Gateway: http://localhost:8000/docs
Auth Service: http://localhost:5001/docs
Chatbot Service: http://localhost:5002/docs



============ 3=================



# Dokumentasi Backend MilkyHoop

## 1. Pendahuluan
Backend MilkyHoop menggunakan arsitektur **microservices**, dengan setiap layanan memiliki tanggung jawab spesifik dan berkomunikasi melalui **gRPC & REST API**.

## 2. Struktur Backend
backend/ ├── api_gateway/ # Entry point utama API │ ├── main.py # FastAPI Gateway │ ├── requirements.txt # Dependensi API Gateway ├── services/ # Kumpulan layanan backend │ ├── auth-service/ # Layanan autentikasi & user management │ ├── chatbot_service/ # Layanan AI Chatbot │ ├── middleware-service/# Middleware untuk komunikasi antar layanan │ ├── business-service/ # Layanan manajemen bisnis │ ├── notification-service/ # Pengiriman notifikasi & email │ ├── log-aggregator/ # Logging & monitoring │ ├── reporting-service/ # Layanan analitik & laporan bisnis │ ├── security-service/ # Manajemen keamanan & akses │ ├── audit-logging-service/ # Audit aktivitas pengguna


## 3. Teknologi Backend
| Layanan                 | Teknologi         | Deskripsi |
|-------------------------|------------------|-----------|
| **API Gateway**         | FastAPI + gRPC   | Gateway utama untuk API |
| **Autentikasi**         | FastAPI + JWT    | Manajemen user & token |
| **Chatbot AI**         | FastAPI + LLM     | Chatbot AI berbasis RAG |
| **Database**           | PostgreSQL + Redis | Penyimpanan data utama |
| **Event Streaming**    | Kafka             | Sinkronisasi data antar layanan |
| **Logging & Monitoring** | ELK Stack + Grafana | Pemantauan sistem real-time |

## 4. Cara Menjalankan Backend
Jalankan semua layanan menggunakan **Docker Compose**:

```bash
docker-compose up -d
Atau jalankan layanan spesifik:

docker-compose up -d auth-service chatbot_service
Cek status layanan:

docker ps | grep backend
5. API Gateway

Layanan ini bertanggung jawab untuk menangani semua request yang masuk dan meneruskannya ke layanan yang sesuai.

Konfigurasi API Gateway (main.py):

from fastapi import FastAPI
import httpx

app = FastAPI()

@app.get("/status")
async def health_check():
    return {"status": "MilkyHoop API Gateway is running!"}
6. Autentikasi & Manajemen Pengguna

Layanan auth-service menangani registrasi, login, dan OAuth.

Contoh implementasi JWT di auth-service:

from fastapi import Depends, HTTPException
from jose import jwt

SECRET_KEY = "supersecretkey"

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except:
        raise HTTPException(status_code=401, detail="Token tidak valid")
7. Chatbot AI

Layanan ini menangani interaksi dengan pengguna menggunakan Retrieval-Augmented Generation (RAG).

Contoh implementasi chatbot_service:

from fastapi import FastAPI

app = FastAPI()

@app.post("/chat")
async def chat(user_input: str):
    return {"response": f"MilkyHoop AI menjawab: {user_input}"}
8. Logging & Monitoring

MilkyHoop menggunakan ELK Stack untuk pemantauan real-time.

Setup Log Aggregator di log-aggregator/main.py

import logging

logging.basicConfig(level=logging.INFO, filename="logs.log")
logger = logging.getLogger()

def log_event(event):
    logger.info(event)
9. Notifikasi & Laporan

Layanan ini menangani notifikasi email, push notification, dan laporan analitik.

Contoh pengiriman email di notification-service:

import smtplib

def send_email(to, subject, message):
    with smtplib.SMTP("smtp.mailserver.com", 587) as server:
        server.sendmail("noreply@milkyhoop.com", to, f"Subject: {subject}\n\n{message}")
10. Keamanan & Audit Logging

MilkyHoop memiliki Role-Based Access Control (RBAC) untuk membatasi akses.

Contoh aturan RBAC di security-service:

roles = {
    "admin": ["read", "write", "delete"],
    "user": ["read", "write"],
}

def check_access(user_role, action):
    if action in roles.get(user_role, []):
        return True
    return False




