import asyncio
import signal
import logging
import os
import yaml
import datetime

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.config import settings
from app import hoop_registry_pb2_grpc as pb_grpc
from app import hoop_registry_pb2 as pb

# ✅ Logging config
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(settings.SERVICE_NAME)

# ✅ gRPC handler implementasi
class HoopRegistryServicer(pb_grpc.HoopRegistryServicer):
    async def RegisterHoop(self, request, context):
        logger.info("📥 RegisterHoop request received: %s", request)

        registry_file = os.path.join(os.path.dirname(__file__), "registry.yaml")
        new_entry = {
            "name": request.name,
            "description": request.description,
            "input_schema": request.input_schema,
            "output_schema": request.output_schema,
            "version": request.version,
            "target_service": request.target_service,
            "owner": request.owner,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z"
        }

        if os.path.exists(registry_file):
            with open(registry_file, "r") as f:
                try:
                    registry_data = yaml.safe_load(f) or []
                except yaml.YAMLError as e:
                    logger.error("❌ Failed to read registry.yaml: %s", e)
                    registry_data = []
        else:
            registry_data = []

        registry_data.append(new_entry)

        with open(registry_file, "w") as f:
            yaml.safe_dump(registry_data, f)

        logger.info("✅ Hoop '%s' registered and saved.", request.name)

        return pb.RegisterHoopResponse(
            status="success",
            message=f"Hoop '{request.name}' registered successfully."
        )

    async def GetHoopMetadata(self, request, context):
        logger.info("🔎 GetHoopMetadata request received for: %s", request.name)

        registry_file = os.path.join(os.path.dirname(__file__), "registry.yaml")
        if not os.path.exists(registry_file):
            logger.warning("⚠️ registry.yaml not found!")
            await context.abort(grpc.StatusCode.NOT_FOUND, "Hoop registry not found.")

        with open(registry_file, "r") as f:
            try:
                registry_data = yaml.safe_load(f) or []
            except yaml.YAMLError as e:
                logger.error("❌ Failed to read registry.yaml: %s", e)
                await context.abort(grpc.StatusCode.INTERNAL, "Failed to read registry file.")

        for hoop in registry_data:
            if hoop["name"] == request.name:
                logger.info("✅ Metadata found for: %s", request.name)
                return pb.GetHoopMetadataResponse(
                    name=hoop["name"],
                    description=hoop["description"],
                    input_schema=hoop["input_schema"],
                    output_schema=hoop["output_schema"],
                    version=hoop["version"],
                    target_service=hoop["target_service"],
                    owner=hoop["owner"],
                    created_at=hoop["created_at"]
                )

        logger.warning("⚠️ Metadata for '%s' not found.", request.name)
        await context.abort(grpc.StatusCode.NOT_FOUND, f"Hoop '{request.name}' not found.")

    async def ListHoop(self, request, context):
        logger.info("📋 ListHoop request received")

        registry_file = os.path.join(os.path.dirname(__file__), "registry.yaml")
        if not os.path.exists(registry_file):
            logger.warning("⚠️ registry.yaml not found! Returning empty list.")
            return pb.ListHoopResponse(hoops=[])

        with open(registry_file, "r") as f:
            try:
                registry_data = yaml.safe_load(f) or []
            except yaml.YAMLError as e:
                logger.error("❌ Failed to read registry.yaml: %s", e)
                await context.abort(grpc.StatusCode.INTERNAL, "Failed to read registry file.")

        hoops = []
        for hoop in registry_data:
            hoops.append(pb.GetHoopMetadataResponse(
                name=hoop["name"],
                description=hoop["description"],
                input_schema=hoop["input_schema"],
                output_schema=hoop["output_schema"],
                version=hoop["version"],
                target_service=hoop["target_service"],
                owner=hoop["owner"],
                created_at=hoop["created_at"]
            ))

        logger.info("✅ Returning %d hoops", len(hoops))
        return pb.ListHoopResponse(hoops=hoops)
    

    async def UpdateHoopMetadata(self, request, context):
        logger.info("✏️ UpdateHoopMetadata request received for: %s", request.name)

        registry_file = os.path.join(os.path.dirname(__file__), "registry.yaml")
        if not os.path.exists(registry_file):
            logger.warning("⚠️ registry.yaml not found!")
            await context.abort(grpc.StatusCode.NOT_FOUND, "Hoop registry not found.")

        with open(registry_file, "r") as f:
            try:
                registry_data = yaml.safe_load(f) or []
            except yaml.YAMLError as e:
                logger.error("❌ Failed to read registry.yaml: %s", e)
                await context.abort(grpc.StatusCode.INTERNAL, "Failed to read registry file.")

        # Cari hoop dan update data
        for hoop in registry_data:
            if hoop["name"] == request.name:
                hoop["description"] = request.description or hoop["description"]
                hoop["input_schema"] = request.input_schema or hoop["input_schema"]
                hoop["output_schema"] = request.output_schema or hoop["output_schema"]
                hoop["version"] = request.version or hoop["version"]
                hoop["target_service"] = request.target_service or hoop["target_service"]
                hoop["owner"] = request.owner or hoop["owner"]
                logger.info("✅ Metadata for '%s' updated.", request.name)
                break
        else:
            logger.warning("⚠️ Metadata for '%s' not found.", request.name)
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Hoop '{request.name}' not found.")

        with open(registry_file, "w") as f:
            yaml.safe_dump(registry_data, f)

        return pb.UpdateHoopMetadataResponse(
            status="success",
            message=f"Hoop '{request.name}' updated successfully."
        )



    async def DeleteHoop(self, request, context):
        logger.info("🗑️ DeleteHoop request received for: %s", request.name)

        registry_file = os.path.join(os.path.dirname(__file__), "registry.yaml")
        if not os.path.exists(registry_file):
            logger.warning("⚠️ registry.yaml not found!")
            await context.abort(grpc.StatusCode.NOT_FOUND, "Hoop registry not found.")

        with open(registry_file, "r") as f:
            try:
                registry_data = yaml.safe_load(f) or []
            except yaml.YAMLError as e:
                logger.error("❌ Failed to read registry.yaml: %s", e)
                await context.abort(grpc.StatusCode.INTERNAL, "Failed to read registry file.")

        # Filter out hoop to delete
        updated_registry = [hoop for hoop in registry_data if hoop["name"] != request.name]
        if len(updated_registry) == len(registry_data):
            logger.warning("⚠️ Hoop '%s' not found.", request.name)
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Hoop '{request.name}' not found.")

        with open(registry_file, "w") as f:
            yaml.safe_dump(updated_registry, f)

        logger.info("✅ Hoop '%s' deleted.", request.name)
        return pb.DeleteHoopResponse(
            status="success",
            message=f"Hoop '{request.name}' deleted successfully."
        )




    async def SearchHoop(self, request, context):
        logger.info("🔎 SearchHoop request received for keyword: '%s'", request.keyword)

        registry_file = os.path.join(os.path.dirname(__file__), "registry.yaml")
        if not os.path.exists(registry_file):
            logger.warning("⚠️ registry.yaml not found! Returning empty list.")
            return pb.ListHoopResponse(hoops=[])

        with open(registry_file, "r") as f:
            try:
                registry_data = yaml.safe_load(f) or []
            except yaml.YAMLError as e:
                logger.error("❌ Failed to read registry.yaml: %s", e)
                await context.abort(grpc.StatusCode.INTERNAL, "Failed to read registry file.")

        # Filter by keyword in name, description, or owner
        keyword = request.keyword.lower()
        result = []
        for hoop in registry_data:
            if (keyword in hoop["name"].lower() or
                keyword in hoop["description"].lower() or
                keyword in hoop["owner"].lower()):
                result.append(pb.GetHoopMetadataResponse(
                    name=hoop["name"],
                    description=hoop["description"],
                    input_schema=hoop["input_schema"],
                    output_schema=hoop["output_schema"],
                    version=hoop["version"],
                    target_service=hoop["target_service"],
                    owner=hoop["owner"],
                    created_at=hoop["created_at"]
                ))

        logger.info("✅ Found %d hoops matching '%s'", len(result), request.keyword)
        return pb.ListHoopResponse(hoops=result)







async def serve() -> None:
    server = aio.server()
    pb_grpc.add_HoopRegistryServicer_to_server(HoopRegistryServicer(), server)

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 HoopRegistry gRPC server listening on port {settings.GRPC_PORT}")

    stop_event = asyncio.Event()

    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        await server.start()
        await stop_event.wait()
    finally:
        await server.stop(5)
        logger.info("✅ gRPC server shut down cleanly.")







if __name__ == "__main__":
    asyncio.run(serve())
