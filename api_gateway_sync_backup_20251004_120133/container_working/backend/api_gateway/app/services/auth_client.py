import grpc
from grpc import aio
import asyncio
from typing import Dict, List, Any, Optional
import os
import logging

# Use exact verified attributes
import auth_service_pb2_grpc 
import auth_service_pb2 as auth_pb2

logger = logging.getLogger(__name__)

class AuthClient:
    def __init__(self, host: str = "auth_service", port: int = 5004, timeout: float = 60.0):
        self.target = f"{host}:{port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        # Use verified stub class name
        self.stub: Optional[auth_service_pb2_grpc.AuthServiceStub] = None

    async def connect(self):
        """Connect to auth service"""
        if self.channel is None:
            self.channel = grpc.aio.insecure_channel(self.target)
            self.stub = auth_service_pb2_grpc.AuthServiceStub(self.channel)
            logger.info(f"Connected to Auth gRPC service at {self.target}")

    async def close(self):
        """Close connection"""
        if self.channel:
            await self.channel.close()

    async def register_user(self, email: str, password: str, name: str, username: str) -> Dict[str, Any]:
        """Register new user - use verified message types"""
        try:
            if not self.stub:
                await self.connect()
                
            # Use verified RegisterRequest
            request = auth_pb2.RegisterRequest(
                email=email,
                password=password,
                name=name,
                username=username
            )
            
            # Call verified Register method
            response = await self.stub.Register(request)
            
            return {
                "success": response.success,
                "message": response.message,
                "user_id": response.user_id if response.success else None
            }
            
        except Exception as e:
            logger.error(f"Registration error: {str(e)}")
            return {"success": False, "message": f"Registration error: {str(e)}"}

    async def login_user(self, email: str, password: str) -> Dict[str, Any]:
        """Login user - use verified message types"""
        try:
            if not self.stub:
                await self.connect()
                
            # Use verified LoginRequest
            request = auth_pb2.LoginRequest(
                email=email,
                password=password
            )
            
            # Call verified Login method
            response = await self.stub.Login(request)
            
            return {
                "success": response.success if hasattr(response, 'success') else True,
                "access_token": response.access_token if hasattr(response, 'access_token') else None,
                "user_id": response.user_id if hasattr(response, 'user_id') else None
            }
            
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return {"success": False, "message": f"Login error: {str(e)}"}

    async def disconnect(self):
        """Disconnect from auth service - alias for close()"""
        await self.close()
        
    async def validate_token(self, token: str) -> Dict[str, Any]:
        """Validate JWT token"""
        try:
            if not self.stub:
                await self.connect()
                
            request = auth_pb2.VerifyRequest(token=token)
            response = await self.stub.VerifyToken(request)
            
            return {
                "valid": response.valid if hasattr(response, 'valid') else False,
                "user_id": response.user_id if hasattr(response, 'user_id') else None
            }
            
        except Exception as e:
            logger.error(f"Token validation error: {str(e)}")
            return {"valid": False, "message": f"Validation error: {str(e)}"}

    async def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        """Get user profile"""
        try:
            if not self.stub:
                await self.connect()
                
            request = auth_pb2.UserInfoRequest(user_id=user_id)
            response = await self.stub.GetUserInfo(request)
            
            return {
                "success": True,
                "user_id": response.user_id if hasattr(response, 'user_id') else None,
                "email": response.email if hasattr(response, 'email') else None
            }
            
        except Exception as e:
            logger.error(f"Profile error: {str(e)}")
            return {"success": False, "message": f"Profile error: {str(e)}"}
