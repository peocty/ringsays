-- Cluster level roles. Run once per database server by a DBA, before migrations.
-- Passwords here are local development defaults; production passwords come from secrets manager.
--
-- ringsays_owner  owns schemas and tables; used only by migrations
-- ringsays_app    API runtime; subject to forced row level security, cannot bypass it
-- ringsays_worker background jobs (expiry sweep, outbox relay); BYPASSRLS, never used by API
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ringsays_owner') THEN
    CREATE ROLE ringsays_owner LOGIN PASSWORD 'ringsays_owner';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ringsays_app') THEN
    CREATE ROLE ringsays_app LOGIN PASSWORD 'ringsays_app' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ringsays_worker') THEN
    CREATE ROLE ringsays_worker LOGIN PASSWORD 'ringsays_worker' BYPASSRLS;
  END IF;
END $$;
