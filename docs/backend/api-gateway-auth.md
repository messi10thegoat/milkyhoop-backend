# 📘 Dokumentasi Teknis — Auth Middleware (Tahap 14)

## 📌 Tujuan
Menerapkan sistem autentikasi ganda:
- 🔐 JWT untuk user publik (frontend, mobile, third party)
- 🔑 API Key untuk komunikasi antar layanan internal (microservices)

---

## 📁 Struktur Terkait

backend/api_gateway/ ├── app/ │ ├── core/ │ │ └── auth.py # Auth Middleware (JWT & API Key) │ └── routers/ │ ├── users.py # Terproteksi oleh JWT │ └── proxy.py # Terproteksi oleh API Key ├── .env # JWT_SECRET_KEY & INTERNAL_API_KEY ├── .env_template # Template env untuk tim dev


---

## 🔧 Dependency

```bash
poetry add python-jose
🔐 File auth.py (JWT + API Key)

import os
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

API_KEY_HEADER_NAME = "x-api-key"
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "super-secret-key")

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-jwt")
ALGORITHM = "HS256"

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

async def get_current_token(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        return {"user_id": user_id, "claims": payload}
    except JWTError:
        raise credentials_exception

async def verify_internal_api_key(request: Request):
    api_key = request.headers.get(API_KEY_HEADER_NAME)
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API Key")
    return True
⚙️ Integrasi Endpoint

/users/ — JWT Protected
@router.get("/", response_model=List[UserResponse])
async def get_users(token: dict = Depends(get_current_token)):
    ...
/proxy/ping — API Key Protected
@router.get("/proxy/ping")
async def proxy_ping(verified: bool = Depends(verify_internal_api_key)):
    ...
📦 .env & .env_template

INTERNAL_API_KEY=<openssl rand -hex 32>
JWT_SECRET_KEY=<openssl rand -hex 32>
🧪 Testing

# Test JWT
curl -X GET http://localhost:8000/users/ \
  -H "Authorization: Bearer <valid_token>"

# Test API Key
curl -X GET http://localhost:8000/proxy/ping \
  -H "x-api-key: <internal_key>"
✅ Status Akhir

JWT & API Key sudah aktif dan diverifikasi
Struktur reusable, scalable, dan aman
Bisa diintegrasi dengan OAuth/Firebase di masa depan
Dokumentasi tersimpan di repo
