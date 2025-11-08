import asyncio
import grpc

from app import order_service_pb2 as pb
from app import order_service_pb2_grpc as pb_grpc

async def test_create_order():
    async with grpc.aio.insecure_channel("localhost:5000") as channel:
        stub = pb_grpc.OrderServiceStub(channel)
        request = pb.CreateOrderRequest(
            customer_name="Test Customer",
            items="Item 1, Item 2",
            total_price=50000.0
        )
        response = await stub.CreateOrder(request)
        print("✅ CreateOrder response:", response)

if __name__ == "__main__":
    asyncio.run(test_create_order())
