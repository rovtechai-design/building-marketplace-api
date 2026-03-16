UPDATE users
SET role = 'admin'
WHERE LOWER(email) = LOWER('kevinlukeuwu@gmail.com')
  AND role <> 'admin';
