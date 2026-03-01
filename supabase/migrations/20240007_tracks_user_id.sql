-- =============================================================================
-- Migration 007: Add user_id to tracks table
-- =============================================================================
-- Lets each track record be owned by the user who discovered it, enabling
-- per-user track filtering via GET /my-tracks.
-- =============================================================================

ALTER TABLE public.tracks
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS tracks_user_id_idx ON public.tracks (user_id);

-- Allow users to read their own tracks (in addition to the existing public read policy)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tracks' AND policyname = 'tracks: owner read'
    ) THEN
        CREATE POLICY "tracks: owner read"
            ON public.tracks FOR SELECT
            USING (auth.uid() = user_id);
    END IF;
END;
$$;
