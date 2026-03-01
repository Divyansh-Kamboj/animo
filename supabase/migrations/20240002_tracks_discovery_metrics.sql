-- =============================================================================
-- Migration: Add discovery metrics to tracks table
-- Stores YouTube view count and artist subscriber count captured at
-- discovery time so the AI vibe agent can reference how undiscovered a
-- track is when generating its living description.
-- =============================================================================

ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS view_count       BIGINT,
    ADD COLUMN IF NOT EXISTS subscriber_count BIGINT;
