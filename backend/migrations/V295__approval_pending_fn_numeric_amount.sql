-- V295: get_pending_approvals_for_user() AND get_approval_request_detail() declared document_amount BIGINT while
-- approval_requests.document_amount is NUMERIC(18,2) -> every call failed with
-- "structure of query does not match function result type" -> GET
-- /api/approval-requests/pending answered 500 for EVERY user (measured 23 Sep, 8/8 users), and
-- GET /api/approval-requests/{id} answered 500 for every existing request.
-- A function's return type cannot be changed by CREATE OR REPLACE, so DROP + CREATE.
-- Bodies identical to the live definitions except the declared type of document_amount.
DROP FUNCTION IF EXISTS get_pending_approvals_for_user(text, uuid, character varying);

CREATE FUNCTION get_pending_approvals_for_user(p_tenant_id text, p_user_id uuid, p_user_role character varying)
 RETURNS TABLE(request_id uuid, workflow_name character varying, document_type character varying, document_id uuid, document_number character varying, document_amount numeric(18,2), current_level integer, level_name character varying, requested_by uuid, requested_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        ar.id as request_id,
        aw.name as workflow_name,
        ar.document_type,
        ar.document_id,
        ar.document_number,
        ar.document_amount,
        ar.current_level,
        al.name as level_name,
        ar.requested_by,
        ar.requested_at
    FROM approval_requests ar
    JOIN approval_workflows aw ON ar.workflow_id = aw.id
    JOIN approval_levels al ON al.workflow_id = aw.id AND al.level_order = ar.current_level
    WHERE ar.tenant_id = p_tenant_id
    AND ar.status = 'pending'
    AND can_user_approve(al.id, p_user_id, p_user_role)
    ORDER BY ar.requested_at ASC;
END;
$function$;

DROP FUNCTION IF EXISTS get_approval_request_detail(uuid);

CREATE FUNCTION get_approval_request_detail(p_request_id uuid)
 RETURNS TABLE(request_id uuid, workflow_id uuid, workflow_name character varying, document_type character varying, document_id uuid, document_number character varying, document_amount numeric(18,2), current_level integer, status character varying, requested_by uuid, requested_at timestamp with time zone, completed_at timestamp with time zone, actions jsonb)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        ar.id as request_id,
        ar.workflow_id,
        aw.name as workflow_name,
        ar.document_type,
        ar.document_id,
        ar.document_number,
        ar.document_amount,
        ar.current_level,
        ar.status,
        ar.requested_by,
        ar.requested_at,
        ar.completed_at,
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'level_order', al.level_order,
                    'level_name', al.name,
                    'action', aa.action,
                    'action_by', aa.action_by,
                    'action_at', aa.action_at,
                    'comments', aa.comments
                ) ORDER BY aa.action_at
            )
            FROM approval_actions aa
            JOIN approval_levels al ON aa.level_id = al.id
            WHERE aa.request_id = ar.id
        ) as actions
    FROM approval_requests ar
    JOIN approval_workflows aw ON ar.workflow_id = aw.id
    WHERE ar.id = p_request_id;
END;
$function$;
