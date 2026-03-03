"""
Onboarding Service — New Tenant + User Creation
==================================================
Atomic creation of tenant + user + CoA + session.
Iron Laws compliant: CoA from seed_default_coa, hash chain self-initializing.
"""
import uuid
import re
import logging
import bcrypt
from datetime import datetime, timezone

from .db_pool import get_db_pool

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Convert business name to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')
    return text or 'bisnis'


def generate_random_suffix(length: int = 4) -> str:
    """Generate random alphanumeric suffix for slug collision."""
    import secrets
    return secrets.token_hex(length // 2)


async def generate_unique_slug(pool, business_name: str) -> str:
    """Generate unique tenant slug with collision handling."""
    base_slug = slugify(business_name)

    async with pool.acquire() as conn:
        # Try base slug first
        exists = await conn.fetchval(
            'SELECT EXISTS(SELECT 1 FROM "Tenant" WHERE alias = $1)',
            base_slug
        )
        if not exists:
            return base_slug

        # Collision: try with random suffix (max 3 retries)
        for _ in range(3):
            slug = f"{base_slug}-{generate_random_suffix()}"
            exists = await conn.fetchval(
                'SELECT EXISTS(SELECT 1 FROM "Tenant" WHERE alias = $1)',
                slug
            )
            if not exists:
                return slug

        # Fallback: use UUID
        return f"{base_slug}-{uuid.uuid4().hex[:8]}"


def hash_password(password: str) -> str:
    """Hash password with bcrypt (12 rounds) — consistent with auth_service."""
    return bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt(rounds=12)
    ).decode('utf-8')


async def create_tenant_and_user(
    email: str,
    password: str,
    business_name: str,
    browser_id: str | None = None,
    device_fingerprint: str | None = None,
) -> dict:
    """
    Atomic: create tenant + user + CoA in single transaction.
    Hash chain is self-initializing (first POSTED journal gets chain_sequence=1).

    Returns: { user_id, tenant_id, access_token, refresh_token, device_id }
    """
    pool = await get_db_pool()
    slug = await generate_unique_slug(pool, business_name)
    user_id = str(uuid.uuid4())
    tenant_id = slug  # Tenant ID = slug (consistent with existing tenants)
    device_id = str(uuid.uuid4())
    password_hash = hash_password(password)
    now_naive = datetime.utcnow()  # naive UTC for Tenant/User (timestamp without tz)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Create tenant
            await conn.execute(
                """
                INSERT INTO "Tenant" (
                    id, alias, display_name, menu_items, status,
                    plan_tier, currency, timezone, fiscal_year_start,
                    country, max_users, max_transactions_per_month,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4::jsonb, 'ACTIVE',
                    'BASE', 'IDR', 'Asia/Jakarta', 1,
                    'ID', 3, 1000,
                    $5, $5
                )
                """,
                tenant_id, slug, business_name, '[]', now_naive
            )
            logger.info(f"Tenant created: {tenant_id} ({business_name})")

            # 2. Create user
            await conn.execute(
                """
                INSERT INTO "User" (
                    id, email, name, role, "passwordHash",
                    "tenantId", "isVerified", "createdAt", "updatedAt",
                    "lastInteraction"
                ) VALUES (
                    $1, $2, $3, 'ADMIN'::"Role", $4,
                    $5, true, $6, $6,
                    $6
                )
                """,
                user_id, email, business_name, password_hash,
                tenant_id, now_naive
            )
            logger.info(f"User created: {user_id[:8]}... ({email})")

            # 3. Initialize CoA using existing DB function
            coa_count = await conn.fetchval(
                "SELECT seed_default_coa($1)",
                tenant_id
            )
            logger.info(f"CoA seeded: {coa_count} accounts for tenant {tenant_id}")

    # 5. Generate tokens locally (same as QR login flow)
    from .auth_instance import auth_client
    token_response = await auth_client._generate_tokens_locally(
        user_id=user_id,
        tenant_id=tenant_id,
        email=email,
        role="ADMIN",
        username=email.split("@")[0],
        device_info="Signup - Mobile Web",
        device_id=device_id,
        device_type="mobile",
    )

    # 6. Register device in DB
    try:
        from backend.api_gateway.libs.milkyhoop_prisma import Prisma
        prisma = Prisma()
        await prisma.connect()
        from .device_service import DeviceService
        device_service = DeviceService(prisma)
        await device_service.register_device(
            user_id=user_id,
            tenant_id=tenant_id,
            device_type="mobile",
            browser_id=browser_id or str(uuid.uuid4()),
            device_fingerprint=device_fingerprint,
            user_agent="Signup Flow",
            refresh_token_hash=DeviceService.hash_refresh_token(token_response.refresh_token),
            ip_address="signup",
            device_id=device_id,
        )
        await prisma.disconnect()
    except Exception as e:
        logger.warning(f"Device registration during signup failed (non-blocking): {e}")

    # 7. Set session authority in Redis
    try:
        from .session_manager import session_manager
        session_manager.activate_mobile_device(user_id=user_id, device_id=device_id)
    except Exception as e:
        logger.warning(f"Session activation during signup failed (non-blocking): {e}")

    logger.info(f"Onboarding complete: tenant={tenant_id}, user={user_id[:8]}...")

    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "email": email,
        "name": business_name,
        "role": "ADMIN",
        "access_token": token_response.access_token,
        "refresh_token": token_response.refresh_token,
        "device_id": device_id,
        "device_type": "mobile",
    }
