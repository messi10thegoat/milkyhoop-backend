import grpc
import asyncio
import logging

from milkyhoop_protos import complaint_service_pb2_grpc, complaint_service_pb2

logger = logging.getLogger(__name__)

class ComplaintServiceClient:
    def __init__(self, host: str = "complaint_service", port: int = 5010):
        self.target = f"{host}:{port}"

    async def create_complaint(self, user_id: str, message: str, product: str, source: str, emotion: str):
        async with grpc.aio.insecure_channel(self.target) as channel:
            stub = complaint_service_pb2_grpc.Complaint_serviceStub(channel)

            request = complaint_service_pb2.CreateComplaintRequest(
                user_id=user_id,
                message=message,
                product=product,
                source=source,
                emotion=emotion
            )

            try:
                response = await stub.CreateComplaint(request)

                # 🐞 DEBUG: Cetak respons
                print("=== COMPLAINT SERVICE DEBUG ===")
                print("Status:", response.status)
                print("Complaint ID:", response.complaint_id)
                print("Message:", response.message)

                return response

            except grpc.aio.AioRpcError as e:
                logger.error(f"[ComplaintService] gRPC error: {e.code()} - {e.details()}")
                raise
