-- Docker Compose only: hand the database created by the postgres image to the migration role.
ALTER DATABASE ringsays OWNER TO ringsays_owner;
