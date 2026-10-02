-- Cluster level roles. Run once per database server by a DBA, before migrations.
-- Passwords here are local development defaults; production passwords come from secrets manager.
--
-- ringsays_owner  owns schemas and tables; used only by migrations
-- ringsays_app    API runtime; subject to forced row level security, cannot bypass it
-- ringsays_worker background jobs (expiry sweep, outbox relay); across tenants through an explicit
--                 system_roles policy on each table (migration 0006), never used by API
-- ringsays_backoffice internal back office deployment only (RingSays reviewers); same policy, but granted
--                     only organisation, catalogue, verification and audit tables, never intents or identity
-- No role needs SUPERUSER or BYPASSRLS, so the same setup works on managed PostgreSQL
-- (backend/app/scripts/bootstrap_db.py does this there, with secrets instead of these passwords).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ringsays_owner') THEN
    CREATE ROLE ringsays_owner LOGIN PASSWORD 'ringsays_owner';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ringsays_app') THEN
    CREATE ROLE ringsays_app LOGIN PASSWORD 'ringsays_app' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ringsays_worker') THEN
    CREATE ROLE ringsays_worker LOGIN PASSWORD 'ringsays_worker' NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ringsays_backoffice') THEN
    CREATE ROLE ringsays_backoffice LOGIN PASSWORD 'ringsays_backoffice' NOBYPASSRLS;
  END IF;
END $$;
-- Databases created before migration 0006: drop the old attribute ONLY AFTER `alembic upgrade head`
-- has applied 0006 to every database on this server (roles are server wide; without the system_roles
-- policies the worker and back office would see no rows).
ALTER ROLE ringsays_worker NOBYPASSRLS;
ALTER ROLE ringsays_backoffice NOBYPASSRLS;
