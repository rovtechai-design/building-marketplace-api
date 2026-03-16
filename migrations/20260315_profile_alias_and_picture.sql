ALTER TABLE users
ADD COLUMN IF NOT EXISTS public_alias VARCHAR(255),
ADD COLUMN IF NOT EXISTS profile_picture_url VARCHAR(500);

UPDATE users
SET public_alias = COALESCE(public_alias, display_name)
WHERE display_name IS NOT NULL;
