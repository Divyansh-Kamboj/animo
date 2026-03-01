-- =============================================================================
-- Migration: User Personalization
-- Tables: user_profiles, user_interactions
-- RLS policies for both tables
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. user_profiles
--    One row per auth user. id is both PK and FK to auth.users so a profile
--    is automatically deleted when the auth user is removed.
-- -----------------------------------------------------------------------------
CREATE TABLE user_profiles (
    id                  UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    favorite_genres     TEXT[]      NOT NULL DEFAULT '{}',
    total_vouches       INTEGER     NOT NULL DEFAULT 0,
    onboarding_complete BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS -------------------------------------------------------------------------
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;

-- Each user may only read their own profile row.
CREATE POLICY "user_profiles: select own"
    ON user_profiles
    FOR SELECT
    USING (auth.uid() = id);

-- Each user may only create a profile for themselves.
CREATE POLICY "user_profiles: insert own"
    ON user_profiles
    FOR INSERT
    WITH CHECK (auth.uid() = id);

-- Each user may only update their own profile row.
CREATE POLICY "user_profiles: update own"
    ON user_profiles
    FOR UPDATE
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);


-- -----------------------------------------------------------------------------
-- 2. user_interactions
--    Audit log of every vouch and comment action a user performs on a track.
--    The unique constraint on (user_id, track_id, interaction_type) prevents
--    a user from vouching the same track twice while still allowing a comment
--    on a previously vouched track.
-- -----------------------------------------------------------------------------
CREATE TABLE user_interactions (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    track_id         UUID        NOT NULL REFERENCES tracks(id)     ON DELETE CASCADE,
    interaction_type TEXT        NOT NULL CHECK (interaction_type IN ('vouch', 'comment')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, track_id, interaction_type)
);

-- Index to make per-user queries fast (used by get_user_taste).
CREATE INDEX user_interactions_user_id_idx ON user_interactions (user_id);

-- RLS -------------------------------------------------------------------------
ALTER TABLE user_interactions ENABLE ROW LEVEL SECURITY;

-- Users may only see their own interaction log.
CREATE POLICY "user_interactions: select own"
    ON user_interactions
    FOR SELECT
    USING (auth.uid() = user_id);

-- Users may only log interactions on their own behalf.
CREATE POLICY "user_interactions: insert own"
    ON user_interactions
    FOR INSERT
    WITH CHECK (auth.uid() = user_id);
