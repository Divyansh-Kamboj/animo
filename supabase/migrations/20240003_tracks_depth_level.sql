-- =============================================================================
-- Migration: Add depth_level to tracks table
-- Stores the recursive discovery depth at which the track was found.
-- depth 0 = direct related artists; depth 2 = deepest allowed level.
-- Used by the AI agent to adjust its "hidden gem" language.
-- =============================================================================

ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS depth_level SMALLINT;
