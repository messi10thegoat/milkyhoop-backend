from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import re

class CacheControlMiddleware(BaseHTTPMiddleware):
    """
    RFC 7234 compliant cache control middleware.
    
    Cache policies:
    - Transactional data (invoices, payments, chat): no-store
    - Reference data (accounts, items): private, 5min
    - Public static: public, 24h
    - Auth: no-store
    """
    
    # Patterns for different cache policies
    NO_CACHE_PATTERNS = [
        r'^/api/auth/',
        r'^/chat/',
        r'^/api/action-chat/',
        r'^/api/invoices',
        r'^/api/bills',
        r'^/api/payments',
        r'^/api/bill-payments',
        r'^/api/expenses',
        r'^/api/journals',
        r'^/api/bank-transfers',
    ]
    
    SHORT_CACHE_PATTERNS = [
        r'^/api/accounts',
        r'^/api/items',
        r'^/api/customers',
        r'^/api/vendors',
        r'^/api/bank-accounts',
    ]
    
    MEDIUM_CACHE_PATTERNS = [
        r'^/api/reports/',
        r'^/api/dashboard/',
    ]
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.no_cache_regex = re.compile('|'.join(self.NO_CACHE_PATTERNS))
        self.short_cache_regex = re.compile('|'.join(self.SHORT_CACHE_PATTERNS))
        self.medium_cache_regex = re.compile('|'.join(self.MEDIUM_CACHE_PATTERNS))
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Only apply to successful responses
        if response.status_code < 400:
            path = request.url.path
            
            if self.no_cache_regex.search(path):
                # Transactional/auth data: never cache
                response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'
            
            elif self.short_cache_regex.search(path):
                # Reference data: cache 5 minutes, private
                response.headers['Cache-Control'] = 'private, max-age=300, must-revalidate'
            
            elif self.medium_cache_regex.search(path):
                # Reports/dashboard: cache 2 minutes, private
                response.headers['Cache-Control'] = 'private, max-age=120, must-revalidate'
            
            else:
                # Default: no cache for safety
                response.headers['Cache-Control'] = 'no-store, must-revalidate'
        
        return response
