from datetime import datetime
import uuid
from prisma.errors import PrismaError

from app.grpc_server import prisma  # pakai instance prisma global, no need inisialisasi ulang

# Fungsi create order (contoh, endpoint CreateOrder)
async def create_order(request):
    if not prisma.is_connected():
        raise RuntimeError("Prisma client is not connected! Call connect_prisma() before querying.")

    # Data order
    data = {
        "id": str(uuid.uuid4()),
        "customer_name": request.customer_name,
        "items": request.items,
        "total_price": request.total_price,
        "status": "PENDING",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }

    try:
        order = await prisma.order.create(data=data)
    except PrismaError as e:
        raise RuntimeError(f"Prisma query failed: {e}")

    return order
