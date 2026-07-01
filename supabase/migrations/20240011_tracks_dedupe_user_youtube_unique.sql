-- Enforce at most one track row per (user_id, youtube_id).
--
-- Prior to this migration, save_track_to_db did a plain INSERT and the
-- discovery walk had no exclusion list, so re-recommended tracks piled up
-- as duplicate library rows. The partial unique index lets NULL user_id
-- rows (legacy anonymous inserts) coexist without violating the constraint.
--
-- Preflight (also run in a one-shot admin script before applying):
--   DELETE FROM public.tracks t USING public.tracks t2
--   WHERE t.user_id = t2.user_id
--     AND t.youtube_id = t2.youtube_id
--     AND t.ctid > t2.ctid;

CREATE UNIQUE INDEX IF NOT EXISTS tracks_user_youtube_key
  ON public.tracks (user_id, youtube_id)
  WHERE user_id IS NOT NULL AND youtube_id IS NOT NULL;
