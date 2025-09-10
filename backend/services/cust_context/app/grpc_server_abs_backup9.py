import asyncio
import signal
import logging
import os
import sys
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
import json

# Add current directory to Python path for absolute imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Import generated stubs (absolute imports)
import cust_context_pb2 as pb
import cust_context_pb2_grpc as pb_grpc

# Import our services (absolute imports)
from services.context_manager import CustomerContextManager
from services.entity_extractor import CustomerEntityExtractor
from models.conversation import ConversationContext, ConversationEntity

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Cust_contextPythonPrisma")

class CustContextServicer(pb_grpc.CustContextServiceServicer):
    """Customer Context gRPC Service Implementation"""
    
    def __init__(self):
        self.context_manager = CustomerContextManager()
        self.entity_extractor = CustomerEntityExtractor()
        logger.info("🧠 Customer Context Service initialized")
    
    def _context_to_pb(self, context: ConversationContext) -> pb.ConversationContext:
        """Convert ConversationContext to protobuf message"""
        entities_pb = []
        for entity in context.entities:
            entity_pb = pb.ConversationEntity(
                entity_type=entity.entity_type,
                entity_name=entity.entity_name,
                entity_details_json=json.dumps(entity.entity_details),
                mentioned_turn=entity.mentioned_turn,
                focus_score=entity.focus_score
            )
            entities_pb.append(entity_pb)
        
        return pb.ConversationContext(
            session_id=context.session_id,
            tenant_id=context.tenant_id,
            entities=entities_pb,
            current_focus=context.current_focus or "",
            last_query=context.last_query,
            turn_count=context.turn_count,
            created_at=context.created_at.isoformat() if context.created_at else "",
            updated_at=context.updated_at.isoformat() if context.updated_at else "",
            ttl_seconds=context.ttl_seconds
        )
    
    def _entity_to_pb(self, entity: ConversationEntity) -> pb.ConversationEntity:
        """Convert ConversationEntity to protobuf message"""
        return pb.ConversationEntity(
            entity_type=entity.entity_type,
            entity_name=entity.entity_name,
            entity_details_json=json.dumps(entity.entity_details),
            mentioned_turn=entity.mentioned_turn,
            focus_score=entity.focus_score
        )
    
    async def GetContext(self, request: pb.GetContextRequest, context) -> pb.GetContextResponse:
        """Get conversation context for session"""
        try:
            logger.info(f"📥 GetContext request: session={request.session_id}, tenant={request.tenant_id}")
            
            conv_context = await self.context_manager.get_context(
                request.session_id, request.tenant_id
            )
            
            if conv_context:
                return pb.GetContextResponse(
                    success=True,
                    message=f"Context found for session {request.session_id}",
                    context=self._context_to_pb(conv_context)
                )
            else:
                return pb.GetContextResponse(
                    success=False,
                    message=f"No context found for session {request.session_id}",
                    context=pb.ConversationContext()
                )
                
        except Exception as e:
            logger.error(f"❌ Error in GetContext: {e}")
            return pb.GetContextResponse(
                success=False,
                message=f"Error retrieving context: {str(e)}",
                context=pb.ConversationContext()
            )
    
    async def UpdateContext(self, request: pb.UpdateContextRequest, context) -> pb.UpdateContextResponse:
        """Update conversation context with new turn"""
        try:
            logger.info(f"📥 UpdateContext: session={request.session_id}, query='{request.query}'")
            
            # Extract entities from query
            extracted = self.entity_extractor.extract_all_entities(request.query)
            context_entities = self.entity_extractor.prepare_context_entities(extracted)
            
            # Add entities from request
            for entity_data in request.entities:
                context_entities.append({
                    "type": entity_data.type,
                    "name": entity_data.name,
                    "details": json.loads(entity_data.details_json) if entity_data.details_json else {}
                })
            
            # Update context
            conv_context = await self.context_manager.update_context(
                request.session_id, request.tenant_id, request.query, context_entities
            )
            
            logger.info(f"✅ Context updated: turn {conv_context.turn_count}, entities: {len(conv_context.entities)}")
            
            return pb.UpdateContextResponse(
                success=True,
                message=f"Context updated for turn {conv_context.turn_count}",
                context=self._context_to_pb(conv_context)
            )
            
        except Exception as e:
            logger.error(f"❌ Error in UpdateContext: {e}")
            return pb.UpdateContextResponse(
                success=False,
                message=f"Error updating context: {str(e)}",
                context=pb.ConversationContext()
            )
    
    async def CreateContext(self, request: pb.CreateContextRequest, context) -> pb.CreateContextResponse:
        """Create new conversation context"""
        try:
            logger.info(f"📥 CreateContext: session={request.session_id}, tenant={request.tenant_id}")
            
            conv_context = await self.context_manager.create_context(
                request.session_id, request.tenant_id, request.ttl_seconds or 3600
            )
            
            return pb.CreateContextResponse(
                success=True,
                message=f"Context created for session {request.session_id}",
                context=self._context_to_pb(conv_context)
            )
            
        except Exception as e:
            logger.error(f"❌ Error in CreateContext: {e}")
            return pb.CreateContextResponse(
                success=False,
                message=f"Error creating context: {str(e)}",
                context=pb.ConversationContext()
            )
    
    async def DeleteContext(self, request: pb.DeleteContextRequest, context) -> pb.DeleteContextResponse:
        """Delete conversation context"""
        try:
            success = await self.context_manager.delete_context(
                request.session_id, request.tenant_id
            )
            
            return pb.DeleteContextResponse(
                success=success,
                message=f"Context {'deleted' if success else 'deletion failed'} for session {request.session_id}"
            )
            
        except Exception as e:
            logger.error(f"❌ Error in DeleteContext: {e}")
            return pb.DeleteContextResponse(
                success=False,
                message=f"Error deleting context: {str(e)}"
            )
    
    async def GetFocusedEntity(self, request: pb.GetFocusedEntityRequest, context) -> pb.GetFocusedEntityResponse:
        """Get currently focused entity"""
        try:
            conv_context = await self.context_manager.get_context(
                request.session_id, request.tenant_id
            )
            
            if conv_context:
                focused_entity = conv_context.get_focused_entity()
                if focused_entity:
                    return pb.GetFocusedEntityResponse(
                        success=True,
                        message=f"Focused entity: {focused_entity.entity_name}",
                        entity=self._entity_to_pb(focused_entity)
                    )
            
            return pb.GetFocusedEntityResponse(
                success=False,
                message="No focused entity found",
                entity=pb.ConversationEntity()
            )
            
        except Exception as e:
            logger.error(f"❌ Error in GetFocusedEntity: {e}")
            return pb.GetFocusedEntityResponse(
                success=False,
                message=f"Error getting focused entity: {str(e)}",
                entity=pb.ConversationEntity()
            )
    
    async def GetSessionStats(self, request: pb.GetSessionStatsRequest, context) -> pb.GetSessionStatsResponse:
        """Get session statistics for tenant"""
        try:
            stats = await self.context_manager.get_session_stats(request.tenant_id)
            
            return pb.GetSessionStatsResponse(
                success=True,
                message=f"Stats for tenant {request.tenant_id}",
                active_sessions=stats["active_sessions"],
                total_turns=stats["total_turns"],
                avg_turns_per_session=stats["avg_turns_per_session"]
            )
            
        except Exception as e:
            logger.error(f"❌ Error in GetSessionStats: {e}")
            return pb.GetSessionStatsResponse(
                success=False,
                message=f"Error getting stats: {str(e)}",
                active_sessions=0,
                total_turns=0,
                avg_turns_per_session=0.0
            )
    
    async def HealthCheck(self, request: pb.HealthCheckRequest, context) -> pb.HealthCheckResponse:
        """Health check endpoint"""
        return pb.HealthCheckResponse(
            status="healthy",
            message="Customer Context Service is running"
        )

class HealthServicer(health_pb2_grpc.HealthServicer):
    async def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING
        )

async def serve():
    # Initialize server
    server = aio.server()
    
    # Add servicers
    pb_grpc.add_CustContextServiceServicer_to_server(CustContextServicer(), server)
    health_pb2_grpc.add_HealthServicer_to_server(HealthServicer(), server)
    
    # Configure server
    port = int(os.getenv("GRPC_PORT", "5008"))
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)
    
    # Graceful shutdown handler
    def signal_handler():
        logger.info("🛑 Shutting down Customer Context Service...")
        server.stop(grace=5.0)
    
    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    # Start server
    logger.info("🚀 Starting Customer Context Service...")
    await server.start()
    logger.info(f"🎯 Customer Context Service listening on {listen_addr}")
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("🛑 Server interrupted")
    finally:
        await server.stop(grace=5.0)
        logger.info("✅ Customer Context Service stopped")

if __name__ == "__main__":
    asyncio.run(serve())
