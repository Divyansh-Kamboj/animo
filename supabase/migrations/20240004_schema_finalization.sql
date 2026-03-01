-- =============================================================================
-- Migration: Schema Finalization
-- 1. Add vibe_description and niche_score columns to tracks
-- 2. Create increment_vouch(row_id uuid) atomic RPC function
-- 3. Service-role bypass policy so the AI agent can write vibe descriptions
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Additional columns on tracks
--    vibe_description — AI-generated living description (updated by the agent)
--    niche_score      — 0-100 integer computed at discovery time
-- -----------------------------------------------------------------------------
ALTER TABLE tracks
    ADD COLUMN IF NOT EXISTS vibe_description TEXT,
    ADD COLUMN IF NOT EXISTS niche_score      INTEGER;


-- -----------------------------------------------------------------------------
-- 2. increment_vouch(row_id uuid) — atomic counter increment via RPC
--
--    Called from database.py:
--        _db.rpc("increment_vouch", {"row_id": track_id}).execute()
--
--    Using a SQL function avoids a read-modify-write race condition when
--    multiple users vouch simultaneously.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION increment_vouch(row_id uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
AS $$
    UPDATE tracks
    SET vouch_count = vouch_count + 1
    WHERE id = row_id;
$$;


-- -----------------------------------------------------------------------------
-- 3. Service-role RLS bypass for the AI agent
--    The agent (running server-side with the service_role key) needs to write
--    vibe_description back to tracks.  A SECURITY DEFINER function already
--    bypasses RLS, but this explicit policy covers direct table updates from
--    the service role as well.
-- -----------------------------------------------------------------------------
ALTER TABLE tracks ENABLE ROW LEVEL SECURITY;

-- Allow any authenticated user to read tracks (needed for public discovery)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tracks' AND policyname = 'tracks: public read'
    ) THEN
        CREATE POLICY "tracks: public read"
            ON tracks
            FOR SELECT
            USING (true);
    END IF;
END $$;

-- Allow the service role (backend) to insert tracks
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tracks' AND policyname = 'tracks: service insert'
    ) THEN
        CREATE POLICY "tracks: service insert"
            ON tracks
            FOR INSERT
            WITH CHECK (true);
    END IF;
END $$;

-- Allow the service role (backend / AI agent) to update tracks
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'tracks' AND policyname = 'tracks: service update'
    ) THEN
        CREATE POLICY "tracks: service update"
            ON tracks
            FOR UPDATE
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;
