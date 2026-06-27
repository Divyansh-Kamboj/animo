-- =============================================================================
-- Migration 009: comments.user_id + RLS
-- =============================================================================
-- Comments now carry the commenter's auth.users.id so the UI can show real
-- attribution. Backfilled rows stay NULL (anonymous legacy entries).
-- Also enables RLS on the comments table -- it was completely open before.
-- =============================================================================

ALTER TABLE public.comments
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES auth.users (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS comments_track_created_idx
    ON public.comments (track_id, created_at DESC);

ALTER TABLE public.comments ENABLE ROW LEVEL SECURITY;

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

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'comments' AND policyname = 'comments: insert own'
    ) THEN
        CREATE POLICY "comments: insert own"
            ON public.comments FOR INSERT
            WITH CHECK (auth.uid() = user_id);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'comments' AND policyname = 'comments: service insert'
    ) THEN
        CREATE POLICY "comments: service insert"
            ON public.comments FOR INSERT
            WITH CHECK (true);
    END IF;
END;
$$;
