CREATE TABLE schema_version (version integer PRIMARY KEY CHECK (version > 0));
INSERT INTO schema_version VALUES (1);

CREATE TABLE operations (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    admission_paused boolean NOT NULL DEFAULT false
);
INSERT INTO operations (singleton) VALUES (true);

CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    owner varchar(100) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    payload text NOT NULL CHECK (octet_length(payload) <= 16384),
    payload_hash char(64) NOT NULL,
    state varchar(16) NOT NULL CHECK (state IN ('queued','running','succeeded','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    lease_token uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    duration_seconds double precision NOT NULL DEFAULT 0 CHECK (duration_seconds BETWEEN 0 AND 15),
    word_count integer CHECK (word_count >= 0),
    checksum char(64),
    error_category varchar(64),
    UNIQUE (owner, idempotency_key),
    CHECK ((state = 'running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK ((state IN ('succeeded','failed')) = (completed_at IS NOT NULL)),
    CHECK ((state = 'succeeded') = (word_count IS NOT NULL AND checksum IS NOT NULL))
);
CREATE INDEX jobs_pending ON jobs (created_at, id) WHERE state IN ('queued','running');
CREATE INDEX jobs_retention ON jobs (completed_at) WHERE completed_at IS NOT NULL;

