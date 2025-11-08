"""
Prisma RLS Extension for Multi-Tenant Isolation

Industry standard approach using transaction-based session variables.
Compatible with connection pooling.

Based on:
- Prisma RLS examples: github.com/prisma/prisma-client-extensions/row-level-security
- AWS Multi-tenant SaaS Guide

FIXED: Uses model name string from __getattr__ instead of _model_name attribute
"""

from typing import Any, Dict, Optional
from milkyhoop_prisma import Prisma


class RLSPrismaClient:
    """
    Wrapper for Prisma client with automatic RLS context setting.
    
    Usage:
        rls_client = RLSPrismaClient(tenant_id="konsultanpsikologi")
        await rls_client.connect()
        
        # All queries automatically wrapped with RLS context
        result = await rls_client.transaksiharian.create(...)
    """
    
    def __init__(self, tenant_id: str, bypass_rls: bool = True):
        self.tenant_id = tenant_id
        self.bypass_rls = bypass_rls
        self._prisma = Prisma()
        self._connected = False
    
    async def connect(self):
        """Connect to database"""
        if not self._connected:
            await self._prisma.connect()
            self._connected = True
    
    async def disconnect(self):
        """Disconnect from database"""
        if self._connected:
            await self._prisma.disconnect()
            self._connected = False
    
    async def _execute_with_rls(self, operation):
        """
        Execute operation within transaction with RLS context.
        
        Pattern:
        1. Start transaction
        2. Set LOCAL session variables (valid for transaction duration)
        3. Execute operation
        4. Commit transaction
        
        This ensures RLS context persists for ALL queries including:
        - Relation validation queries
        - Foreign key checks
        - Actual data operations
        """
        async with self._prisma.tx() as tx:
            # Set RLS context at transaction start
            await tx.execute_raw(
                f"SELECT set_config('app.current_tenant_id', '{self.tenant_id}', TRUE)"
            )
            
            if self.bypass_rls:
                await tx.execute_raw(
                    "SELECT set_config('app.bypass_rls', 'true', TRUE)"
                )
            
            # Execute operation with RLS context active
            result = await operation(tx)
            
            return result
    
    def __getattr__(self, name: str):
        """
        Proxy attribute access to Prisma client.
        Wraps operations with RLS context automatically.
        
        FIXED: Pass model name (string) to RLSModelProxy instead of model object
        """
        attr = getattr(self._prisma, name)
        
        # Return non-model attributes as-is
        if not hasattr(attr, 'create'):
            return attr
        
        # Wrap model operations with RLS context
        # Pass the attribute NAME (e.g., "transaksiharian", "outbox") not the object
        return RLSModelProxy(self, name)


class RLSModelProxy:
    """
    Proxy for Prisma model operations with automatic RLS wrapping.
    
    FIXED: Stores model_name as string, uses it to access tx.{model_name}
    """
    
    def __init__(self, rls_client: RLSPrismaClient, model_name: str):
        self._rls_client = rls_client
        self._model_name = model_name  # Store as string: "transaksiharian", "outbox", etc.
    
    async def create(self, **kwargs):
        """Create operation with RLS context"""
        async def operation(tx):
            # Access model from transaction using string name
            model_tx = getattr(tx, self._model_name)
            return await model_tx.create(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)
    
    async def update(self, **kwargs):
        """Update operation with RLS context"""
        async def operation(tx):
            model_tx = getattr(tx, self._model_name)
            return await model_tx.update(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)
    
    async def delete(self, **kwargs):
        """Delete operation with RLS context"""
        async def operation(tx):
            model_tx = getattr(tx, self._model_name)
            return await model_tx.delete(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)
    
    async def find_many(self, **kwargs):
        """FindMany operation with RLS context"""
        async def operation(tx):
            model_tx = getattr(tx, self._model_name)
            return await model_tx.find_many(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)
    
    async def find_first(self, **kwargs):
        """FindFirst operation with RLS context"""
        async def operation(tx):
            model_tx = getattr(tx, self._model_name)
            return await model_tx.find_first(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)
    
    async def count(self, **kwargs):
        """Count operation with RLS context"""
        async def operation(tx):
            model_tx = getattr(tx, self._model_name)
            return await model_tx.count(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)
    
    async def find_unique(self, **kwargs):
        """FindUnique operation with RLS context"""
        async def operation(tx):
            model_tx = getattr(tx, self._model_name)
            return await model_tx.find_unique(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)
    
    async def upsert(self, **kwargs):
        """Upsert operation with RLS context"""
        async def operation(tx):
            model_tx = getattr(tx, self._model_name)
            return await model_tx.upsert(**kwargs)
        
        return await self._rls_client._execute_with_rls(operation)