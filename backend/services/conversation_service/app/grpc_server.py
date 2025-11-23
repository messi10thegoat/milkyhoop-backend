import asyncio
import signal
import logging
import os
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from datetime import datetime
import json
from app.config import settings
from app import conversation_service_pb2_grpc as pb_grpc
from app import conversation_service_pb2 as pb
from app.prisma_client import prisma

# ============================================
# LOGGING CONFIG
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)

# ============================================
# CONVERSATION SERVICE - CHAT PERSISTENCE
# ============================================
class ConversationServiceServicer(pb_grpc.ConversationServiceServicer):
    
    async def SaveMessage(self, request, context):
        """
        Save chat message with response to database
        """
        try:
            logger.info(f"💾 SaveMessage: user={request.user_id}, tenant={request.tenant_id}")
            
            # Parse metadata JSON
            metadata = None
            if request.metadata_json:
                try:
                    parsed = json.loads(request.metadata_json)
                    if parsed:  # Only set if not empty
                        metadata = parsed
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ Invalid metadata JSON, using None")
            
            # Save to database
            chat_message = await prisma.chatmessage.create(
                data={
                    "message": request.message,
                    "response": request.response if request.response else None,
                    "intent": request.intent if request.intent else None,
                    "metadata": json.dumps(metadata) if metadata else None,
                    "user": {"connect": {"id": request.user_id}},
                    "tenant": {"connect": {"id": request.tenant_id}}
                }
            )
            
            # Convert datetime to Unix timestamp
            created_timestamp = int(chat_message.createdAt.timestamp())
            
            logger.info(f"✅ Message saved: id={chat_message.id}")
            
            return pb.SaveMessageResponse(
                status="success",
                message_id=chat_message.id,
                created_at=created_timestamp
            )
            
        except Exception as e:
            logger.error(f"❌ SaveMessage error: {str(e)}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to save message: {str(e)}")
            return pb.SaveMessageResponse(
                status="error",
                message_id="",
                created_at=0
            )
    
    async def GetChatHistory(self, request, context):
        """
        Get paginated chat history for user
        """
        try:
            logger.info(f"📖 GetChatHistory: user={request.user_id}, tenant={request.tenant_id}, limit={request.limit}, offset={request.offset}")
            
            # Default pagination
            limit = request.limit if request.limit > 0 else 30
            offset = request.offset if request.offset >= 0 else 0
            
            # Get total count
            total_count = await prisma.chatmessage.count(
                where={
                    "userId": request.user_id,
                    "tenantId": request.tenant_id
                }
            )
            
            # Get messages (ordered by created_at DESC for most recent first)
            messages = await prisma.chatmessage.find_many(
                where={
                    "userId": request.user_id,
                    "tenantId": request.tenant_id
                },
                order={
                    "createdAt": "desc"
                },
                skip=offset,
                take=limit
            )
            
            # Convert to proto messages
            chat_messages = []
            for msg in messages:
                metadata_json = json.dumps(msg.metadata) if msg.metadata else "{}"
                created_timestamp = int(msg.createdAt.timestamp())
                
                chat_messages.append(pb.ChatMessage(
                    id=msg.id,
                    user_id=msg.userId,
                    tenant_id=msg.tenantId,
                    message=msg.message,
                    response=msg.response if msg.response else "",
                    intent=msg.intent if msg.intent else "",
                    metadata_json=metadata_json,
                    created_at=created_timestamp
                ))
            
            has_more = (offset + limit) < total_count
            
            logger.info(f"✅ Retrieved {len(chat_messages)} messages (total: {total_count})")
            
            return pb.GetChatHistoryResponse(
                status="success",
                messages=chat_messages,
                total_count=total_count,
                has_more=has_more
            )
            
        except Exception as e:
            logger.error(f"❌ GetChatHistory error: {str(e)}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to get chat history: {str(e)}")
            return pb.GetChatHistoryResponse(
                status="error",
                messages=[],
                total_count=0,
                has_more=False
            )
    
    async def GetMessagesSince(self, request, context):
        """
        Get messages since timestamp (for real-time sync)
        """
        try:
            logger.info(f"🔄 GetMessagesSince: user={request.user_id}, tenant={request.tenant_id}, since={request.since_timestamp}")
            
            # Convert Unix timestamp to datetime
            since_dt = datetime.fromtimestamp(request.since_timestamp)
            
            # Get messages after timestamp
            messages = await prisma.chatmessage.find_many(
                where={
                    "userId": request.user_id,
                    "tenantId": request.tenant_id,
                    "createdAt": {
                        "gt": since_dt
                    }
                },
                order={
                    "createdAt": "asc"
                }
            )
            
            # Convert to proto messages
            chat_messages = []
            latest_timestamp = request.since_timestamp
            
            for msg in messages:
                metadata_json = json.dumps(msg.metadata) if msg.metadata else "{}"
                created_timestamp = int(msg.createdAt.timestamp())
                
                if created_timestamp > latest_timestamp:
                    latest_timestamp = created_timestamp
                
                chat_messages.append(pb.ChatMessage(
                    id=msg.id,
                    user_id=msg.userId,
                    tenant_id=msg.tenantId,
                    message=msg.message,
                    response=msg.response if msg.response else "",
                    intent=msg.intent if msg.intent else "",
                    metadata_json=metadata_json,
                    created_at=created_timestamp
                ))
            
            logger.info(f"✅ Retrieved {len(chat_messages)} new messages")
            
            return pb.GetMessagesSinceResponse(
                status="success",
                messages=chat_messages,
                latest_timestamp=latest_timestamp
            )
            
        except Exception as e:
            logger.error(f"❌ GetMessagesSince error: {str(e)}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Failed to get messages: {str(e)}")
            return pb.GetMessagesSinceResponse(
                status="error",
                messages=[],
                latest_timestamp=request.since_timestamp
            )
    
    async def HealthCheck(self, request, context):
        """Health check endpoint"""
        logger.debug("🏥 HealthCheck called")
        return request

# ============================================
# SERVER STARTUP
# ============================================
async def serve() -> None:
    # Connect to Prisma
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await prisma.connect()
        logger.info("✅ Prisma connected")
    
    server = aio.server()
    pb_grpc.add_ConversationServiceServicer_to_server(ConversationServiceServicer(), server)
    
    # Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)
    
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} gRPC server listening on port {settings.GRPC_PORT}")
    
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
        logger.info("🧹 Shutting down gRPC server...")
        await server.stop(5)
        
        if "DATABASE_URL" in os.environ:
            logger.info("🧹 Disconnecting Prisma...")
            await prisma.disconnect()
            logger.info("✅ Prisma disconnected")
        
        logger.info("✅ gRPC server shut down cleanly.")

if __name__ == "__main__":
    asyncio.run(serve())