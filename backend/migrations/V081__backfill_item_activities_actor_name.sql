-- V081: Backfill item_activities actor_name with user's display name
-- Previously actor_name stored the username; now it should use name/fullname

UPDATE item_activities ia
SET actor_name = COALESCE(u.name, u.fullname, u.username)
FROM "User" u
WHERE ia.actor_id = u.id
  AND COALESCE(u.name, u.fullname) IS NOT NULL
  AND ia.actor_name != COALESCE(u.name, u.fullname, u.username);
