ALTER TABLE users
ADD COLUMN IF NOT EXISTS display_name VARCHAR(255),
ADD COLUMN IF NOT EXISTS full_name VARCHAR(255),
ADD COLUMN IF NOT EXISTS building_id INTEGER REFERENCES buildings(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS room_number_private VARCHAR(120),
ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'user',
ADD COLUMN IF NOT EXISTS profile_completed BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE listings
ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active';

CREATE TABLE IF NOT EXISTS listing_reports (
    id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    reporter_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reported_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
    reason VARCHAR(64) NOT NULL,
    details TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action_taken VARCHAR(64),
    CONSTRAINT uq_listing_reports_listing_reporter UNIQUE (listing_id, reporter_user_id)
);

CREATE INDEX IF NOT EXISTS idx_listing_reports_status ON listing_reports(status);
CREATE INDEX IF NOT EXISTS idx_listing_reports_listing_id ON listing_reports(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_reports_building_id ON listing_reports(building_id);

ALTER TABLE listing_images
DROP CONSTRAINT IF EXISTS listing_images_listing_id_fkey;

ALTER TABLE listing_images
ADD CONSTRAINT listing_images_listing_id_fkey
FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE;
