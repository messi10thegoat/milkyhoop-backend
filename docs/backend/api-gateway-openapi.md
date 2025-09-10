# 📘 Dokumentasi OpenAPI — API Gateway MilkyHoop

## 📌 Tujuan
Dokumentasi ini menjelaskan cara mengakses Swagger UI, Redoc, serta penggunaan endpoint melalui OpenAPI bawaan FastAPI.

---

## 🌐 Akses Dokumentasi

### 🔹 Swagger UI (Interactive)
http://localhost:8000/docs


### 🔹 ReDoc (Static, Ringan)
http://localhost:8000/redoc


---

## 🔑 Otentikasi

### 🔐 JWT (Bearer Token)
Klik tombol **Authorize** (kanan atas Swagger UI), lalu masukkan:
Bearer <your_token>


Contoh:
Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...


### 🔑 API Key (x-api-key)
Gunakan `curl` atau Postman, tambahkan header:
x-api-key: <your_internal_key>


---

## 🧪 Endpoint Tersedia

### ✅ `/healthz/` — Health Check
- Cek status service
- Tidak butuh autentikasi

### ✅ `/users/` — Get Users
- Butuh JWT Auth
- Response: daftar user

### ✅ `/proxy/ping` — Proxy ke Service Internal
- Butuh API Key
- Response: dummy dari `business-service`

---

## 🛠 Tips Testing di Swagger

- Klik `Authorize` untuk masuk token JWT
- Gunakan `Try it out` di setiap endpoint
- Perhatikan response code `200`, `401`, `403`

---

## 🧱 Status
- Swagger UI tersedia otomatis via FastAPI
- Endpoint didokumentasikan otomatis
- Tidak perlu install Swagger secara manual
- Bisa di-extend untuk OAuth2, Login Form, dll

---

## ✅ Next
Untuk API publik, bisa generate file `openapi.json`:

http://localhost:8000/openapi.json


Bisa digunakan untuk Postman Collection, dokumentasi eksternal, atau frontend generator seperti `openapi-typescript`.

