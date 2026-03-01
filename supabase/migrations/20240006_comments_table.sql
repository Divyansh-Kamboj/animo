-- =============================================================================
-- Migration 006: Comments Table
-- =============================================================================
-- Run this in the Supabase SQL Editor after migration 005.
-- Safe to run multiple times (idempotent).
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.comments (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id   UUID        NOT NULL REFERENCES public.tracks (id) ON DELETE CASCADE,
    text       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS comments_track_id_idx
    ON public.comments (track_id);

ALTER TABLE public.comments ENABLE ROW LEVEL SECURITY;

-- Anyone can read comments
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'comments' AND policyname = 'comments: public read'
    ) THEN
        CREATE POLICY "comments: public read"
            ON public.comments FOR SELECT USING (true);
    END IF;
END;
$$;

-- Backend (service role) can insert comments
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'comments' AND policyname = 'comments: service insert'
    ) THEN
        CREATE POLICY "comments: service insert"
            ON public.comments FOR INSERT WITH CHECK (true);
    END IF;
END;
$$;
