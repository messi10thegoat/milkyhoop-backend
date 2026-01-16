"""
Consolidation Router - Report Consolidation Management

Manages consolidation groups, entities, account mappings, and consolidation runs
for multi-entity financial report consolidation.

NOTE: Consolidation is reporting only - no journal entries are posted.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
from datetime import date
import logging
import asyncpg
import json

from ..schemas.consolidation import (
    CreateConsolidationGroupRequest,
    UpdateConsolidationGroupRequest,
    ConsolidationGroupListResponse,
    ConsolidationGroupDetailResponse,
    CreateConsolidationEntityRequest,
    UpdateConsolidationEntityRequest,
    ConsolidationEntityResponse,
    CreateAccountMappingsRequest,
    AccountMappingListResponse,
    AutoMapRequest,
    CreateIntercompanyRelationshipRequest,
    IntercompanyRelationshipListResponse,
    CreateConsolidationRunRequest,
    ConsolidationRunListResponse,
    ConsolidationRunDetailResponse,
    ProcessConsolidationRequest,
    ConsolidatedTrialBalanceResponse,
    ConsolidatedBalanceSheetResponse,
    ConsolidatedIncomeStatementResponse,
    EliminationEntriesResponse,
    ConsolidationResponse,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=2,
            max_size=10,
            command_timeout=60
        )
    return _pool


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None
    }


# =============================================================================
# HEALTH CHECK
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "consolidation"}


# =============================================================================
# CONSOLIDATION GROUPS
# =============================================================================
@router.get("/groups", response_model=ConsolidationGroupListResponse)
async def list_groups(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    sort_by: Literal["name", "code", "created_at"] = Query("name"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
):
    """List consolidation groups with pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["cg.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                conditions.append(f"(cg.name ILIKE ${param_idx} OR cg.code ILIKE ${param_idx})")
                params.append(f"%{search}%")
                param_idx += 1

            if is_active is not None:
                conditions.append(f"cg.is_active = ${param_idx}")
                params.append(is_active)
                param_idx += 1

            where_clause = " AND ".join(conditions)
            sort_column = {"name": "cg.name", "code": "cg.code", "created_at": "cg.created_at"}[sort_by]

            # Count total
            count_query = f"SELECT COUNT(*) FROM consolidation_groups cg WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Fetch groups with entity count
            query = f"""
                SELECT cg.*,
                       (SELECT COUNT(*) FROM consolidation_entities ce WHERE ce.group_id = cg.id) as entity_count
                FROM consolidation_groups cg
                WHERE {where_clause}
                ORDER BY {sort_column} {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "elimination_method": row["elimination_method"],
                    "entity_count": row["entity_count"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing consolidation groups: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list consolidation groups")


@router.post("/groups", response_model=ConsolidationResponse, status_code=201)
async def create_group(request: Request, body: CreateConsolidationGroupRequest):
    """Create a new consolidation group."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check duplicate code
            exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND code = $2",
                ctx["tenant_id"], body.code
            )
            if exists:
                raise HTTPException(status_code=400, detail=f"Consolidation group with code '{body.code}' already exists")

            # Insert group
            query = """
                INSERT INTO consolidation_groups (
                    tenant_id, code, name, description, consolidation_currency_id,
                    elimination_method, fiscal_year_end_month, fiscal_year_end_day, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id
            """
            group_id = await conn.fetchval(
                query,
                ctx["tenant_id"], body.code, body.name, body.description,
                body.consolidation_currency_id, body.elimination_method,
                body.fiscal_year_end_month, body.fiscal_year_end_day, ctx["user_id"]
            )

            return {
                "success": True,
                "message": "Consolidation group created successfully",
                "data": {"id": str(group_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating consolidation group: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create consolidation group")


@router.get("/groups/{group_id}", response_model=ConsolidationGroupDetailResponse)
async def get_group(request: Request, group_id: UUID):
    """Get consolidation group detail with entities."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get group
            query = """
                SELECT cg.*, c.code as currency_code
                FROM consolidation_groups cg
                LEFT JOIN currencies c ON c.id = cg.consolidation_currency_id
                WHERE cg.tenant_id = $1 AND cg.id = $2
            """
            row = await conn.fetchrow(query, ctx["tenant_id"], group_id)
            if not row:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            # Get entities
            entities_query = """
                SELECT ce.*,
                       pe.entity_name as parent_entity_name,
                       c.code as functional_currency_code
                FROM consolidation_entities ce
                LEFT JOIN consolidation_entities pe ON pe.id = ce.parent_entity_id
                LEFT JOIN currencies c ON c.id = ce.functional_currency_id
                WHERE ce.group_id = $1
                ORDER BY ce.is_parent DESC, ce.entity_name
            """
            entity_rows = await conn.fetch(entities_query, group_id)

            entities = [
                {
                    "id": str(e["id"]),
                    "group_id": str(e["group_id"]),
                    "entity_tenant_id": e["entity_tenant_id"],
                    "entity_name": e["entity_name"],
                    "entity_code": e["entity_code"],
                    "ownership_percent": e["ownership_percent"],
                    "is_parent": e["is_parent"],
                    "parent_entity_id": str(e["parent_entity_id"]) if e["parent_entity_id"] else None,
                    "parent_entity_name": e["parent_entity_name"],
                    "functional_currency_id": str(e["functional_currency_id"]) if e["functional_currency_id"] else None,
                    "functional_currency_code": e["functional_currency_code"],
                    "consolidation_type": e["consolidation_type"],
                    "is_active": e["is_active"],
                    "effective_date": e["effective_date"],
                }
                for e in entity_rows
            ]

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "description": row["description"],
                    "consolidation_currency_id": str(row["consolidation_currency_id"]) if row["consolidation_currency_id"] else None,
                    "consolidation_currency_code": row["currency_code"],
                    "elimination_method": row["elimination_method"],
                    "fiscal_year_end_month": row["fiscal_year_end_month"],
                    "fiscal_year_end_day": row["fiscal_year_end_day"],
                    "is_active": row["is_active"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "entities": entities,
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting consolidation group: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get consolidation group")


@router.patch("/groups/{group_id}", response_model=ConsolidationResponse)
async def update_group(request: Request, group_id: UUID, body: UpdateConsolidationGroupRequest):
    """Update consolidation group."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check exists
            exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], group_id
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            # Build update
            updates = []
            params = []
            param_idx = 1

            update_data = body.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            if not updates:
                return {"success": True, "message": "No changes to update"}

            updates.append(f"updated_at = NOW()")
            params.extend([ctx["tenant_id"], group_id])

            query = f"""
                UPDATE consolidation_groups
                SET {', '.join(updates)}
                WHERE tenant_id = ${param_idx} AND id = ${param_idx + 1}
            """
            await conn.execute(query, *params)

            return {"success": True, "message": "Consolidation group updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating consolidation group: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update consolidation group")


@router.delete("/groups/{group_id}", response_model=ConsolidationResponse)
async def delete_group(request: Request, group_id: UUID):
    """Deactivate consolidation group."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE consolidation_groups
                SET is_active = false, updated_at = NOW()
                WHERE tenant_id = $1 AND id = $2
                """,
                ctx["tenant_id"], group_id
            )
            if result == "UPDATE 0":
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            return {"success": True, "message": "Consolidation group deactivated"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting consolidation group: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete consolidation group")


# =============================================================================
# CONSOLIDATION ENTITIES
# =============================================================================
@router.post("/groups/{group_id}/entities", response_model=ConsolidationEntityResponse, status_code=201)
async def add_entity(request: Request, group_id: UUID, body: CreateConsolidationEntityRequest):
    """Add entity to consolidation group."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check group exists
            group_exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], group_id
            )
            if not group_exists:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            # Check duplicate entity
            exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_entities WHERE group_id = $1 AND entity_tenant_id = $2",
                group_id, body.entity_tenant_id
            )
            if exists:
                raise HTTPException(status_code=400, detail="Entity already exists in this group")

            # Insert entity
            query = """
                INSERT INTO consolidation_entities (
                    group_id, entity_tenant_id, entity_name, entity_code,
                    ownership_percent, is_parent, parent_entity_id,
                    functional_currency_id, consolidation_type, effective_date
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
            """
            entity_id = await conn.fetchval(
                query,
                group_id, body.entity_tenant_id, body.entity_name, body.entity_code,
                body.ownership_percent, body.is_parent, body.parent_entity_id,
                body.functional_currency_id, body.consolidation_type, body.effective_date
            )

            return {
                "success": True,
                "message": "Entity added to consolidation group",
                "data": {"id": str(entity_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding consolidation entity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add entity")


@router.patch("/entities/{entity_id}", response_model=ConsolidationEntityResponse)
async def update_entity(request: Request, entity_id: UUID, body: UpdateConsolidationEntityRequest):
    """Update consolidation entity."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check exists with tenant verification via group
            exists = await conn.fetchval(
                """
                SELECT 1 FROM consolidation_entities ce
                JOIN consolidation_groups cg ON cg.id = ce.group_id
                WHERE cg.tenant_id = $1 AND ce.id = $2
                """,
                ctx["tenant_id"], entity_id
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Entity not found")

            # Build update
            updates = []
            params = []
            param_idx = 1

            update_data = body.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                updates.append(f"{field} = ${param_idx}")
                params.append(value)
                param_idx += 1

            if not updates:
                return {"success": True, "message": "No changes to update"}

            params.append(entity_id)
            query = f"UPDATE consolidation_entities SET {', '.join(updates)} WHERE id = ${param_idx}"
            await conn.execute(query, *params)

            return {"success": True, "message": "Entity updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating consolidation entity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update entity")


@router.delete("/entities/{entity_id}", response_model=ConsolidationEntityResponse)
async def remove_entity(request: Request, entity_id: UUID):
    """Remove entity from consolidation group."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM consolidation_entities ce
                USING consolidation_groups cg
                WHERE ce.group_id = cg.id AND cg.tenant_id = $1 AND ce.id = $2
                """,
                ctx["tenant_id"], entity_id
            )
            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Entity not found")

            return {"success": True, "message": "Entity removed from group"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing consolidation entity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove entity")


# =============================================================================
# ACCOUNT MAPPINGS
# =============================================================================
@router.get("/groups/{group_id}/mappings", response_model=AccountMappingListResponse)
async def list_mappings(request: Request, group_id: UUID):
    """List account mappings for a group."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify group access
            group_exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], group_id
            )
            if not group_exists:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            query = """
                SELECT cam.*, ce.entity_name as source_entity_name
                FROM consolidation_account_mappings cam
                JOIN consolidation_entities ce ON ce.id = cam.source_entity_id
                WHERE cam.group_id = $1
                ORDER BY ce.entity_name, cam.source_account_code
            """
            rows = await conn.fetch(query, group_id)

            items = [
                {
                    "id": str(row["id"]),
                    "source_entity_id": str(row["source_entity_id"]),
                    "source_entity_name": row["source_entity_name"],
                    "source_account_code": row["source_account_code"],
                    "target_account_code": row["target_account_code"],
                    "sign_flip": row["sign_flip"],
                    "elimination_account": row["elimination_account"],
                }
                for row in rows
            ]

            return {"success": True, "items": items, "total": len(items)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing account mappings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list account mappings")


@router.post("/groups/{group_id}/mappings", response_model=ConsolidationResponse)
async def create_mappings(request: Request, group_id: UUID, body: CreateAccountMappingsRequest):
    """Create or update account mappings."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify group access
            group_exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], group_id
            )
            if not group_exists:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            created = 0
            updated = 0

            for mapping in body.mappings:
                # Upsert mapping
                result = await conn.execute(
                    """
                    INSERT INTO consolidation_account_mappings (
                        group_id, source_entity_id, source_account_code,
                        target_account_code, sign_flip, elimination_account
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (group_id, source_entity_id, source_account_code)
                    DO UPDATE SET
                        target_account_code = $4,
                        sign_flip = $5,
                        elimination_account = $6
                    """,
                    group_id, mapping.source_entity_id, mapping.source_account_code,
                    mapping.target_account_code, mapping.sign_flip, mapping.elimination_account
                )
                if "INSERT" in result:
                    created += 1
                else:
                    updated += 1

            return {
                "success": True,
                "message": f"Account mappings saved: {created} created, {updated} updated"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating account mappings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create account mappings")


@router.post("/groups/{group_id}/auto-map", response_model=ConsolidationResponse)
async def auto_map_accounts(request: Request, group_id: UUID, body: AutoMapRequest):
    """Auto-generate account mappings based on strategy."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify group and entity access
            entity_exists = await conn.fetchval(
                """
                SELECT 1 FROM consolidation_entities ce
                JOIN consolidation_groups cg ON cg.id = ce.group_id
                WHERE cg.tenant_id = $1 AND cg.id = $2 AND ce.id = $3
                """,
                ctx["tenant_id"], group_id, body.source_entity_id
            )
            if not entity_exists:
                raise HTTPException(status_code=404, detail="Entity not found in group")

            # Get entity's tenant accounts
            entity = await conn.fetchrow(
                "SELECT entity_tenant_id FROM consolidation_entities WHERE id = $1",
                body.source_entity_id
            )

            # Get accounts from entity's tenant
            accounts = await conn.fetch(
                "SELECT code FROM chart_of_accounts WHERE tenant_id = $1 AND is_active = true",
                entity["entity_tenant_id"]
            )

            # Create mappings (1:1 for exact strategy)
            created = 0
            for acc in accounts:
                if body.mapping_strategy == "exact":
                    target_code = acc["code"]
                elif body.mapping_strategy == "prefix":
                    target_code = f"C-{acc['code']}"  # Consolidated prefix
                else:  # suffix
                    target_code = f"{acc['code']}-C"

                await conn.execute(
                    """
                    INSERT INTO consolidation_account_mappings (
                        group_id, source_entity_id, source_account_code, target_account_code
                    ) VALUES ($1, $2, $3, $4)
                    ON CONFLICT (group_id, source_entity_id, source_account_code) DO NOTHING
                    """,
                    group_id, body.source_entity_id, acc["code"], target_code
                )
                created += 1

            return {
                "success": True,
                "message": f"Auto-mapping completed: {created} accounts processed"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error auto-mapping accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to auto-map accounts")


# =============================================================================
# INTERCOMPANY RELATIONSHIPS
# =============================================================================
@router.get("/groups/{group_id}/intercompany", response_model=IntercompanyRelationshipListResponse)
async def list_intercompany(request: Request, group_id: UUID):
    """List intercompany relationships."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            group_exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], group_id
            )
            if not group_exists:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            query = """
                SELECT ir.*,
                       ea.entity_name as entity_a_name,
                       eb.entity_name as entity_b_name
                FROM intercompany_relationships ir
                JOIN consolidation_entities ea ON ea.id = ir.entity_a_id
                JOIN consolidation_entities eb ON eb.id = ir.entity_b_id
                WHERE ir.group_id = $1
                ORDER BY ea.entity_name, eb.entity_name
            """
            rows = await conn.fetch(query, group_id)

            items = [
                {
                    "id": str(row["id"]),
                    "entity_a_id": str(row["entity_a_id"]),
                    "entity_a_name": row["entity_a_name"],
                    "entity_b_id": str(row["entity_b_id"]),
                    "entity_b_name": row["entity_b_name"],
                    "relationship_type": row["relationship_type"],
                    "ar_account_code": row["ar_account_code"],
                    "ap_account_code": row["ap_account_code"],
                    "is_active": row["is_active"],
                }
                for row in rows
            ]

            return {"success": True, "items": items, "total": len(items)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing intercompany relationships: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list intercompany relationships")


@router.post("/groups/{group_id}/intercompany", response_model=ConsolidationResponse, status_code=201)
async def add_intercompany(request: Request, group_id: UUID, body: CreateIntercompanyRelationshipRequest):
    """Add intercompany relationship."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            group_exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], group_id
            )
            if not group_exists:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            # Insert relationship
            rel_id = await conn.fetchval(
                """
                INSERT INTO intercompany_relationships (
                    group_id, entity_a_id, entity_b_id, relationship_type,
                    ar_account_code, ap_account_code
                ) VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                group_id, body.entity_a_id, body.entity_b_id, body.relationship_type,
                body.ar_account_code, body.ap_account_code
            )

            return {
                "success": True,
                "message": "Intercompany relationship created",
                "data": {"id": str(rel_id)}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating intercompany relationship: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create intercompany relationship")


# =============================================================================
# CONSOLIDATION RUNS
# =============================================================================
@router.get("/runs", response_model=ConsolidationRunListResponse)
async def list_runs(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    group_id: Optional[UUID] = Query(None),
    status: Optional[str] = Query(None),
    period_year: Optional[int] = Query(None),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List consolidation runs."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["cr.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if group_id:
                conditions.append(f"cr.group_id = ${param_idx}")
                params.append(group_id)
                param_idx += 1

            if status:
                conditions.append(f"cr.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if period_year:
                conditions.append(f"cr.period_year = ${param_idx}")
                params.append(period_year)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM consolidation_runs cr WHERE {where_clause}",
                *params
            )

            query = f"""
                SELECT cr.*, cg.name as group_name
                FROM consolidation_runs cr
                JOIN consolidation_groups cg ON cg.id = cr.group_id
                WHERE {where_clause}
                ORDER BY cr.created_at {sort_order}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "group_id": str(row["group_id"]),
                    "group_name": row["group_name"],
                    "period_type": row["period_type"],
                    "period_year": row["period_year"],
                    "period_month": row["period_month"],
                    "period_quarter": row["period_quarter"],
                    "as_of_date": row["as_of_date"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "completed_at": row["completed_at"],
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing consolidation runs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list consolidation runs")


@router.post("/runs", response_model=ConsolidationResponse, status_code=201)
async def create_run(request: Request, body: CreateConsolidationRunRequest):
    """Create a new consolidation run."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify group
            group_exists = await conn.fetchval(
                "SELECT 1 FROM consolidation_groups WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], body.group_id
            )
            if not group_exists:
                raise HTTPException(status_code=404, detail="Consolidation group not found")

            # Insert run
            run_id = await conn.fetchval(
                """
                INSERT INTO consolidation_runs (
                    tenant_id, group_id, period_type, period_year,
                    period_month, period_quarter, as_of_date, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                ctx["tenant_id"], body.group_id, body.period_type, body.period_year,
                body.period_month, body.period_quarter, body.as_of_date, ctx["user_id"]
            )

            return {
                "success": True,
                "message": "Consolidation run created",
                "data": {"id": str(run_id)}
            }

    except HTTPException:
        raise
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="A run for this period already exists")
    except Exception as e:
        logger.error(f"Error creating consolidation run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create consolidation run")


@router.get("/runs/{run_id}", response_model=ConsolidationRunDetailResponse)
async def get_run(request: Request, run_id: UUID):
    """Get consolidation run detail with results."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT cr.*, cg.name as group_name
                FROM consolidation_runs cr
                JOIN consolidation_groups cg ON cg.id = cr.group_id
                WHERE cr.tenant_id = $1 AND cr.id = $2
            """
            row = await conn.fetchrow(query, ctx["tenant_id"], run_id)
            if not row:
                raise HTTPException(status_code=404, detail="Consolidation run not found")

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "group_id": str(row["group_id"]),
                    "group_name": row["group_name"],
                    "period_type": row["period_type"],
                    "period_year": row["period_year"],
                    "period_month": row["period_month"],
                    "period_quarter": row["period_quarter"],
                    "as_of_date": row["as_of_date"],
                    "status": row["status"],
                    "error_message": row["error_message"],
                    "consolidated_trial_balance": row["consolidated_trial_balance"],
                    "consolidated_balance_sheet": row["consolidated_balance_sheet"],
                    "consolidated_income_statement": row["consolidated_income_statement"],
                    "elimination_entries": row["elimination_entries"],
                    "exchange_rates_snapshot": row["exchange_rates_snapshot"],
                    "created_at": row["created_at"],
                    "completed_at": row["completed_at"],
                }
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting consolidation run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get consolidation run")


@router.post("/runs/{run_id}/process", response_model=ConsolidationResponse)
async def process_run(request: Request, run_id: UUID, body: ProcessConsolidationRequest = None):
    """Process consolidation - gather data from entities and create consolidated reports."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get run and group
            run = await conn.fetchrow(
                """
                SELECT cr.*, cg.consolidation_currency_id, cg.elimination_method
                FROM consolidation_runs cr
                JOIN consolidation_groups cg ON cg.id = cr.group_id
                WHERE cr.tenant_id = $1 AND cr.id = $2
                """,
                ctx["tenant_id"], run_id
            )
            if not run:
                raise HTTPException(status_code=404, detail="Consolidation run not found")

            if run["status"] == "completed":
                raise HTTPException(status_code=400, detail="Run already completed")

            # Update status to processing
            await conn.execute(
                "UPDATE consolidation_runs SET status = 'processing' WHERE id = $1",
                run_id
            )

            try:
                # Get entities
                entities = await conn.fetch(
                    """
                    SELECT * FROM consolidation_entities
                    WHERE group_id = $1 AND is_active = true
                    """,
                    run["group_id"]
                )

                # Gather trial balances from each entity
                consolidated_tb = []
                entity_balances = {}

                for entity in entities:
                    # Get trial balance from entity's tenant
                    tb = await conn.fetch(
                        """
                        SELECT
                            coa.code as account_code,
                            coa.name as account_name,
                            COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0) as balance
                        FROM chart_of_accounts coa
                        LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                        LEFT JOIN journal_entries je ON je.id = jl.journal_id
                        WHERE coa.tenant_id = $1
                          AND (je.entry_date <= $2 OR je.id IS NULL)
                          AND (je.status = 'posted' OR je.id IS NULL)
                        GROUP BY coa.code, coa.name
                        ORDER BY coa.code
                        """,
                        entity["entity_tenant_id"], run["as_of_date"]
                    )

                    entity_code = entity["entity_code"]
                    ownership = float(entity["ownership_percent"]) / 100

                    for row in tb:
                        account_code = row["account_code"]
                        balance = int(row["balance"] * ownership)  # Apply ownership %

                        if account_code not in entity_balances:
                            entity_balances[account_code] = {
                                "account_name": row["account_name"],
                                "entities": {},
                                "total": 0
                            }

                        entity_balances[account_code]["entities"][entity_code] = balance
                        entity_balances[account_code]["total"] += balance

                # Calculate elimination entries
                eliminations = []
                intercompany_rels = await conn.fetch(
                    "SELECT * FROM intercompany_relationships WHERE group_id = $1 AND is_active = true",
                    run["group_id"]
                )

                for rel in intercompany_rels:
                    if rel["ar_account_code"] and rel["ap_account_code"]:
                        ar_balance = entity_balances.get(rel["ar_account_code"], {}).get("total", 0)
                        ap_balance = entity_balances.get(rel["ap_account_code"], {}).get("total", 0)

                        # Create elimination entry
                        elimination_amount = min(abs(ar_balance), abs(ap_balance))
                        if elimination_amount > 0:
                            eliminations.append({
                                "description": "Intercompany elimination",
                                "ar_account": rel["ar_account_code"],
                                "ap_account": rel["ap_account_code"],
                                "amount": elimination_amount
                            })

                # Format trial balance
                for code, data in entity_balances.items():
                    elim_amount = 0
                    for elim in eliminations:
                        if elim["ar_account"] == code or elim["ap_account"] == code:
                            elim_amount = elim["amount"]

                    consolidated_tb.append({
                        "account_code": code,
                        "account_name": data["account_name"],
                        "entity_balances": data["entities"],
                        "eliminations": elim_amount,
                        "consolidated_balance": data["total"] - elim_amount
                    })

                # Save results
                await conn.execute(
                    """
                    UPDATE consolidation_runs SET
                        status = 'completed',
                        consolidated_trial_balance = $1,
                        elimination_entries = $2,
                        completed_at = NOW()
                    WHERE id = $3
                    """,
                    json.dumps(consolidated_tb),
                    json.dumps(eliminations),
                    run_id
                )

                return {
                    "success": True,
                    "message": f"Consolidation completed for {len(entities)} entities"
                }

            except Exception as process_error:
                # Mark as error
                await conn.execute(
                    "UPDATE consolidation_runs SET status = 'error', error_message = $1 WHERE id = $2",
                    str(process_error), run_id
                )
                raise

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing consolidation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process consolidation")


@router.get("/runs/{run_id}/eliminations", response_model=EliminationEntriesResponse)
async def get_eliminations(request: Request, run_id: UUID):
    """Get elimination entries for a run."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT elimination_entries FROM consolidation_runs WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], run_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")

            entries = row["elimination_entries"] or []
            formatted = []
            total = 0

            for entry in entries:
                formatted.append({
                    "description": entry.get("description", ""),
                    "account_code": entry.get("ar_account", ""),
                    "account_name": "",
                    "debit": entry.get("amount", 0),
                    "credit": 0,
                    "entity_a": "",
                    "entity_b": ""
                })
                formatted.append({
                    "description": entry.get("description", ""),
                    "account_code": entry.get("ap_account", ""),
                    "account_name": "",
                    "debit": 0,
                    "credit": entry.get("amount", 0),
                    "entity_a": "",
                    "entity_b": ""
                })
                total += entry.get("amount", 0)

            return {"success": True, "entries": formatted, "total_eliminations": total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting eliminations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get eliminations")


# =============================================================================
# REPORTS
# =============================================================================
@router.get("/runs/{run_id}/trial-balance")
async def get_trial_balance(request: Request, run_id: UUID):
    """Get consolidated trial balance."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT cr.*, cg.name as group_name, c.code as currency_code
                FROM consolidation_runs cr
                JOIN consolidation_groups cg ON cg.id = cr.group_id
                LEFT JOIN currencies c ON c.id = cg.consolidation_currency_id
                WHERE cr.tenant_id = $1 AND cr.id = $2
                """,
                ctx["tenant_id"], run_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")

            tb = row["consolidated_trial_balance"] or []
            period_desc = f"{row['period_type'].title()} {row['period_year']}"
            if row["period_month"]:
                period_desc = f"Month {row['period_month']} {row['period_year']}"
            elif row["period_quarter"]:
                period_desc = f"Q{row['period_quarter']} {row['period_year']}"

            total_debit = sum(r.get("consolidated_balance", 0) for r in tb if r.get("consolidated_balance", 0) > 0)
            total_credit = abs(sum(r.get("consolidated_balance", 0) for r in tb if r.get("consolidated_balance", 0) < 0))

            return {
                "success": True,
                "group_name": row["group_name"],
                "as_of_date": row["as_of_date"],
                "period_description": period_desc,
                "currency_code": row["currency_code"] or "IDR",
                "rows": tb,
                "total_debit": total_debit,
                "total_credit": total_credit
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trial balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get trial balance")


@router.get("/runs/{run_id}/balance-sheet")
async def get_balance_sheet(request: Request, run_id: UUID):
    """Get consolidated balance sheet."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT cr.*, cg.name as group_name, c.code as currency_code
                FROM consolidation_runs cr
                JOIN consolidation_groups cg ON cg.id = cr.group_id
                LEFT JOIN currencies c ON c.id = cg.consolidation_currency_id
                WHERE cr.tenant_id = $1 AND cr.id = $2
                """,
                ctx["tenant_id"], run_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")

            bs = row["consolidated_balance_sheet"] or {}

            return {
                "success": True,
                "group_name": row["group_name"],
                "as_of_date": row["as_of_date"],
                "currency_code": row["currency_code"] or "IDR",
                "assets": bs.get("assets", {}),
                "liabilities": bs.get("liabilities", {}),
                "equity": bs.get("equity", {}),
                "total_assets": bs.get("total_assets", 0),
                "total_liabilities_equity": bs.get("total_liabilities_equity", 0)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting balance sheet: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get balance sheet")


@router.get("/runs/{run_id}/income-statement")
async def get_income_statement(request: Request, run_id: UUID):
    """Get consolidated income statement."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT cr.*, cg.name as group_name, c.code as currency_code
                FROM consolidation_runs cr
                JOIN consolidation_groups cg ON cg.id = cr.group_id
                LEFT JOIN currencies c ON c.id = cg.consolidation_currency_id
                WHERE cr.tenant_id = $1 AND cr.id = $2
                """,
                ctx["tenant_id"], run_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")

            income = row["consolidated_income_statement"] or {}

            return {
                "success": True,
                "group_name": row["group_name"],
                "period_start": row["as_of_date"],
                "period_end": row["as_of_date"],
                "currency_code": row["currency_code"] or "IDR",
                "revenue": income.get("revenue", {}),
                "expenses": income.get("expenses", {}),
                "total_revenue": income.get("total_revenue", 0),
                "total_expenses": income.get("total_expenses", 0),
                "net_income": income.get("net_income", 0)
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting income statement: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get income statement")


@router.get("/runs/{run_id}/export")
async def export_run(request: Request, run_id: UUID, format: Literal["json", "csv"] = Query("json")):
    """Export consolidation run data."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM consolidation_runs WHERE tenant_id = $1 AND id = $2",
                ctx["tenant_id"], run_id
            )
            if not row:
                raise HTTPException(status_code=404, detail="Run not found")

            if format == "json":
                return {
                    "trial_balance": row["consolidated_trial_balance"],
                    "balance_sheet": row["consolidated_balance_sheet"],
                    "income_statement": row["consolidated_income_statement"],
                    "eliminations": row["elimination_entries"]
                }
            else:
                # CSV format - return trial balance as CSV
                import io
                import csv

                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["Account Code", "Account Name", "Consolidated Balance"])

                tb = row["consolidated_trial_balance"] or []
                for item in tb:
                    writer.writerow([
                        item.get("account_code", ""),
                        item.get("account_name", ""),
                        item.get("consolidated_balance", 0)
                    ])

                return {
                    "content_type": "text/csv",
                    "data": output.getvalue()
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting consolidation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export consolidation")
