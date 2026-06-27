-- =============================================================================
-- Migration 008: user_profiles survey state + last-pack timestamp
-- =============================================================================
-- Persist a user's survey answers + the timestamp of their last pack open.
-- These let us re-generate today's daily pack from the same seeds and gate
-- the 24-hour "show pack vs. go straight to hub" decision.
-- =============================================================================

ALTER TABLE public.user_profiles
    ADD COLUMN IF NOT EXISTS survey_seeds         TEXT[]      NOT NULL DEFAULT '{}';

ALTER TABLE public.user_profiles
    ADD COLUMN IF NOT EXISTS survey_vibe          TEXT;

ALTER TABLE public.user_profiles
    ADD COLUMN IF NOT EXISTS survey_niche         REAL;

ALTER TABLE public.user_profiles
    ADD COLUMN IF NOT EXISTS survey_genres        TEXT[]      NOT NULL DEFAULT '{}';

ALTER TABLE public.user_profiles
    ADD COLUMN IF NOT EXISTS last_pack_opened_at  TIMESTAMPTZ;
