-- =============================================================================
-- Migration 005: Catch-up & Test-User Seed
-- =============================================================================
-- Run this once in the Supabase SQL Editor (Dashboard -> SQL Editor -> Run).
-- Every statement is idempotent -- safe to run multiple times.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. tracks -- add missing columns (one statement each to avoid parser issues)
-- ---------------------------------------------------------------------------
ALTER TABLE public.tracks ADD COLUMN IF NOT EXISTS view_count       BIGINT;
ALTER TABLE public.tracks ADD COLUMN IF NOT EXISTS subscriber_count BIGINT;
ALTER TABLE public.tracks ADD COLUMN IF NOT EXISTS depth_level      SMALLINT;
ALTER TABLE public.tracks ADD COLUMN IF NOT EXISTS niche_score      INTEGER;
ALTER TABLE public.tracks ADD COLUMN IF NOT EXISTS vibe_description TEXT;


-- ---------------------------------------------------------------------------
-- 2. user_profiles
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_profiles (
    id                  UUID        PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    favorite_genres     TEXT[]      NOT NULL DEFAULT '{}',
    total_vouches       INTEGER     NOT NULL DEFAULT 0,
    onboarding_complete BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_profiles' AND policyname = 'user_profiles: select own'
    ) THEN
        CREATE POLICY "user_profiles: select own"
            ON public.user_profiles FOR SELECT USING (auth.uid() = id);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_profiles' AND policyname = 'user_profiles: insert own'
    ) THEN
        CREATE POLICY "user_profiles: insert own"
            ON public.user_profiles FOR INSERT WITH CHECK (auth.uid() = id);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_profiles' AND policyname = 'user_profiles: update own'
    ) THEN
        CREATE POLICY "user_profiles: update own"
            ON public.user_profiles FOR UPDATE
            USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_profiles' AND policyname = 'user_profiles: service read'
    ) THEN
        CREATE POLICY "user_profiles: service read"
            ON public.user_profiles FOR SELECT USING (true);
    END IF;
END;
$$;


-- ---------------------------------------------------------------------------
-- 3. user_interactions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.user_interactions (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES auth.users (id)    ON DELETE CASCADE,
    track_id         UUID        NOT NULL REFERENCES public.tracks (id) ON DELETE CASCADE,
    interaction_type TEXT        NOT NULL CHECK (interaction_type IN ('vouch', 'comment')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, track_id, interaction_type)
);

CREATE INDEX IF NOT EXISTS user_interactions_user_id_idx
    ON public.user_interactions (user_id);

ALTER TABLE public.user_interactions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_interactions' AND policyname = 'user_interactions: select own'
    ) THEN
        CREATE POLICY "user_interactions: select own"
            ON public.user_interactions FOR SELECT USING (auth.uid() = user_id);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'user_interactions' AND policyname = 'user_interactions: insert own'
    ) THEN
        CREATE POLICY "user_interactions: insert own"
            ON public.user_interactions FOR INSERT WITH CHECK (auth.uid() = user_id);
    END IF;
END;
$$;


-- ---------------------------------------------------------------------------
-- 4. increment_vouch RPC
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.increment_vouch(row_id uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $func$
    UPDATE public.tracks
    SET vouch_count = vouch_count + 1
    WHERE id = row_id;
$func$;


-- ---------------------------------------------------------------------------
-- 5. tracks RLS policies
-- ---------------------------------------------------------------------------
ALTER TABLE public.tracks ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tracks' AND policyname = 'tracks: public read'
    ) THEN
        CREATE POLICY "tracks: public read"
            ON public.tracks FOR SELECT USING (true);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tracks' AND policyname = 'tracks: service insert'
    ) THEN
        CREATE POLICY "tracks: service insert"
            ON public.tracks FOR INSERT WITH CHECK (true);
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tracks' AND policyname = 'tracks: service update'
    ) THEN
        CREATE POLICY "tracks: service update"
            ON public.tracks FOR UPDATE USING (true) WITH CHECK (true);
    END IF;
END;
$$;


-- ---------------------------------------------------------------------------
-- 6. Test-bypass user seed
-- ---------------------------------------------------------------------------
INSERT INTO auth.users (
    id, instance_id, email, encrypted_password, email_confirmed_at,
    aud, role, raw_app_meta_data, raw_user_meta_data, created_at, updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000000',
    'test-bypass@animo.local',
    '$2a$10$abcdefghijklmnopqrstuuVGmC6.9q2JKTS3A7O5c5y2d8y3U6mZa',
    NOW(), 'authenticated', 'authenticated',
    '{"provider": "email", "providers": ["email"]}',
    '{}', NOW(), NOW()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.user_profiles (id, favorite_genres, onboarding_complete)
VALUES ('00000000-0000-0000-0000-000000000001', '{}', false)
ON CONFLICT (id) DO NOTHING;
