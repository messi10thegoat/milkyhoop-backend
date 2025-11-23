"""
Rule Engine Service - Main Entry Point
MilkyHoop 4.0
"""

import sys
import os

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

if __name__ == '__main__':
    from app.grpc_server import serve
    import asyncio

    asyncio.run(serve())
