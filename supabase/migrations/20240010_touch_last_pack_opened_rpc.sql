-- =============================================================================
-- Migration 010: touch_last_pack_opened RPC
-- =============================================================================
-- Server-side helper to stamp last_pack_opened_at = NOW() for a user.
-- Avoids clock-skew issues from sending a Python-side timestamp.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.touch_last_pack_opened(uid uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $func$
    UPDATE public.user_profiles
    SET last_pack_opened_at = NOW()
    WHERE id = uid;
$func$;
