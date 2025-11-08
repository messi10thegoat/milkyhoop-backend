"""
Analytics Handler
Handles product analytics queries (top products, low-sell products)

Phase 2 Implementation - Financial Analytics
"""

import logging
from datetime import datetime, timedelta
import grpc

logger = logging.getLogger(__name__)


class AnalyticsHandler:
    """Handler for product analytics operations"""
    
    @staticmethod
    def calculate_time_range(time_range: str, start_ts: int = None, end_ts: int = None):
        """
        Calculate start and end timestamps based on time_range
        
        Args:
            time_range: 'daily', 'weekly', 'monthly'
            start_ts: Optional custom start (unix seconds)
            end_ts: Optional custom end (unix seconds)
        
        Returns:
            (start_timestamp, end_timestamp) in unix seconds
        """
        now = datetime.now()
        
        # Use custom range if provided
        if start_ts and end_ts:
            return (start_ts, end_ts)
        
        # Calculate based on time_range
        if time_range == 'daily':
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif time_range == 'weekly':
            # Last 7 days
            start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0)
            end = now
        elif time_range == 'monthly':
            # Current month
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
        else:
            # Default to monthly
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = now
        
        return (int(start.timestamp()), int(end.timestamp()))
    
    @staticmethod
    async def handle_get_top_products(
        request,
        context: grpc.aio.ServicerContext,
        prisma,
        pb
    ):
        """
        Handle GetTopProducts RPC call
        
        Args:
            request: GetTopProductsRequest proto
            context: gRPC context
            prisma: Prisma client instance
            pb: transaction_service_pb2 module
            
        Returns:
            GetTopProductsResponse
        """
        try:
            # Calculate time range
            start_ts, end_ts = AnalyticsHandler.calculate_time_range(
                request.time_range,
                request.start_timestamp if request.start_timestamp else None,
                request.end_timestamp if request.end_timestamp else None
            )
            
            limit = request.limit if request.limit > 0 else 10
            
            logger.info(f"GetTopProducts: tenant={request.tenant_id}, range={request.time_range}, limit={limit}")
            
            # Import queries module
            from queries.product_analytics import get_top_products
            
            # Query database
            results = await get_top_products(prisma, request.tenant_id, start_ts, end_ts, limit)
            
            # Build response
            products = []
            for row in results:
                product = pb.ProductSales(
                    product_name=row['product_name'],
                    quantity_sold=float(row['total_quantity_sold']),
                    unit=row['unit'],
                    total_revenue=int(row['total_revenue']),
                    transaction_count=int(row['transaction_count']),
                    profit_margin=0.0  # TODO: Calculate from cost data in future
                )
                products.append(product)
            
            return pb.GetTopProductsResponse(
                success=True,
                message=f"Found {len(products)} top products",
                products=products,
                total_count=len(products)
            )
        
        except Exception as e:
            logger.error(f"GetTopProducts failed: {e}", exc_info=True)
            return pb.GetTopProductsResponse(
                success=False,
                message=f"Failed to get top products: {str(e)}",
                products=[],
                total_count=0
            )
    
    @staticmethod
    def generate_suggestion(product_name: str, turnover_pct: float, quantity_sold: int) -> str:
        """Generate actionable suggestion for low-sell products"""
        if turnover_pct < 5:
            return f"Produk hampir tidak laku. Pertimbangkan: (1) Diskon besar 40-50%, (2) Stop produksi, (3) Bundle dengan produk laris"
        elif turnover_pct < 10:
            return f"Turnover rendah. Saran: (1) Flash sale 20-30%, (2) Promosi media sosial, (3) Bundle deal"
        else:
            return f"Penjualan lambat. Coba: (1) Diskon 10-15%, (2) Giveaway di Instagram"
    
    @staticmethod
    async def handle_get_low_sell_products(
        request,
        context: grpc.aio.ServicerContext,
        prisma,
        pb
    ):
        """
        Handle GetLowSellProducts RPC call
        
        Args:
            request: GetLowSellProductsRequest proto
            context: gRPC context
            prisma: Prisma client instance
            pb: transaction_service_pb2 module
            
        Returns:
            GetLowSellProductsResponse
        """
        try:
            # Calculate time range
            start_ts, end_ts = AnalyticsHandler.calculate_time_range(
                request.time_range,
                request.start_timestamp if request.start_timestamp else None,
                request.end_timestamp if request.end_timestamp else None
            )
            
            threshold = request.turnover_threshold if request.turnover_threshold > 0 else 10.0
            limit = request.limit if request.limit > 0 else 10
            
            logger.info(f"GetLowSellProducts: tenant={request.tenant_id}, threshold={threshold}%")
            
            # Import queries module
            from queries.product_analytics import get_low_sell_products
            
            # Query database
            results = await get_low_sell_products(prisma, request.tenant_id, start_ts, end_ts, threshold, limit)
            
            # Build response
            products = []
            for row in results:
                turnover_pct = float(row['turnover_percentage'])
                quantity_sold = int(row['quantity_sold'])
                
                suggestion = AnalyticsHandler.generate_suggestion(
                    row['product_name'], 
                    turnover_pct,
                    quantity_sold
                )
                
                product = pb.ProductLowSell(
                    product_name=row['product_name'],
                    quantity_sold=float(row['quantity_sold']),
                    current_stock=float(row['current_stock']),
                    unit=row['unit'],
                    total_revenue=int(row['revenue']),
                    turnover_percentage=turnover_pct,
                    suggestion=suggestion
                )
                products.append(product)
            
            return pb.GetLowSellProductsResponse(
                success=True,
                message=f"Found {len(products)} low-sell products",
                products=products,
                total_count=len(products)
            )
        
        except Exception as e:
            logger.error(f"GetLowSellProducts failed: {e}", exc_info=True)
            return pb.GetLowSellProductsResponse(
                success=False,
                message=f"Failed to get low-sell products: {str(e)}",
                products=[],
                total_count=0
            )