#!/usr/bin/env python3
"""
Generate test JWT token for testing
"""
import jwt
from datetime import datetime, timedelta

# JWT secret from auth service (check .env or config)
SECRET_KEY = "fe9e6b579ad0d194aea51b325e6f0790ace8b2bd3d91c059a016abd2c0e54de2"  # JWT_SECRET from auth service

payload = {
    "user_id": "d780b7fe-8b53-47e4-8ef1-aad067de0d58",
    "tenant_id": "evlogia",
    "role": "FREE",
    "email": "grapmanado@gmail.com",
    "username": "evlogia",
    "token_type": "access",
    "iat": int(datetime.now().timestamp()),
    "exp": int((datetime.now() + timedelta(hours=24)).timestamp()),
    "nbf": int(datetime.now().timestamp())
}

token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
print(token)
