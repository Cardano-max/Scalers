-- 22-memories-artist-subject.sql — widen memories.subject_type to admit 'artist'.
--
-- The studio image-upload path (engine/studio/image_ingest.py) records "new design
-- uploaded" events as ARTIST-scoped memories (subject_type='artist',
-- subject_id=<artist slug>), and the artist API appends operator notes the same way.
-- The original CHECK (18-memories.sql / memory/store.py) covers only
-- customer/campaign/conversation/fact. Idempotent: safe on a fresh cluster (initdb
-- order: 18 creates, 22 widens) and on re-run against an existing one. Runtime twin:
-- studio/artist_memory.py ensure_artist_memory_schema().

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'memories'::regclass
          AND conname  = 'memories_subject_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%artist%'
    ) THEN
        ALTER TABLE memories DROP CONSTRAINT memories_subject_type_check;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'memories'::regclass
          AND conname  = 'memories_subject_type_check'
    ) THEN
        ALTER TABLE memories ADD CONSTRAINT memories_subject_type_check
            CHECK (subject_type IN
                   ('customer','campaign','conversation','fact','artist'));
    END IF;
END $$;
