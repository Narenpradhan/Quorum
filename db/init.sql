-- Quorum Schema Initialization
-- Idempotent PostgreSQL DDL & Seed Data

-- 1. Tables Creation
CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS poll_options (
    id SERIAL PRIMARY KEY,
    poll_id INT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    label VARCHAR(128) NOT NULL,
    vote_count INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS votes (
    id SERIAL PRIMARY KEY,
    poll_id INT NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    option_id INT NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
    voter_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Performance Indexes
CREATE INDEX IF NOT EXISTS idx_votes_poll_id ON votes(poll_id);
CREATE INDEX IF NOT EXISTS idx_votes_created_at ON votes(created_at);
CREATE INDEX IF NOT EXISTS idx_poll_options_poll_id ON poll_options(poll_id);

-- 3. Idempotent Seed Data
DO $$
DECLARE
    poll1_id INT;
    poll2_id INT;
BEGIN
    -- Seed Poll 1: Container Orchestration
    IF NOT EXISTS (SELECT 1 FROM polls WHERE title = 'What is your preferred container orchestration tool?') THEN
        INSERT INTO polls (title, description)
        VALUES (
            'What is your preferred container orchestration tool?',
            'Cast your vote for the primary orchestration framework driving your modern infrastructure stack.'
        ) RETURNING id INTO poll1_id;

        INSERT INTO poll_options (poll_id, label, vote_count) VALUES
            (poll1_id, 'Kubernetes', 0),
            (poll1_id, 'Docker Swarm', 0),
            (poll1_id, 'Nomad', 0),
            (poll1_id, 'Bare Metal', 0);
    END IF;

    -- Seed Poll 2: Tabs vs Spaces
    IF NOT EXISTS (SELECT 1 FROM polls WHERE title = 'Tabs vs Spaces?') THEN
        INSERT INTO polls (title, description)
        VALUES (
            'Tabs vs Spaces?',
            'The eternal indentation debate. Select your side in the editor config holy war.'
        ) RETURNING id INTO poll2_id;

        INSERT INTO poll_options (poll_id, label, vote_count) VALUES
            (poll2_id, '2 Spaces', 0),
            (poll2_id, '4 Spaces', 0),
            (poll2_id, 'Tabs', 0);
    END IF;
END $$;
