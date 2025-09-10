"""
Customer Reference Resolution Service - Main Entry Point
"""
import asyncio
import logging
from grpc_server import serve

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

async def main():
    """Main entry point for the service"""
    logger.info("🚀 Starting Customer Reference Resolution Service")
    
    try:
        await serve()
    except Exception as e:
        logger.error(f"💥 Service startup failed: {str(e)}")
        raise

if __name__ == '__main__':
    asyncio.run(main())
