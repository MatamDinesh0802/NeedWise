#!/usr/bin/env bash
# Runs once, on the first start of an empty Postgres volume.
# Creates one database per service, plus two roles for WorkMind:
#   workmind_owner: owns the schema, used only by migrations
#   workmind_app:   runtime role, data access only, can never bypass Row-Level Security
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
	CREATE ROLE workmind_owner LOGIN PASSWORD '${WORKMIND_OWNER_PASSWORD}';
	CREATE ROLE workmind_app LOGIN PASSWORD '${WORKMIND_APP_PASSWORD}' NOBYPASSRLS;
	CREATE DATABASE workmind OWNER workmind_owner;

	CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
	CREATE DATABASE keycloak OWNER keycloak;

	CREATE ROLE openfga LOGIN PASSWORD '${OPENFGA_DB_PASSWORD}';
	CREATE DATABASE openfga OWNER openfga;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname workmind <<-EOSQL
	REVOKE ALL ON SCHEMA public FROM PUBLIC;
	GRANT USAGE ON SCHEMA public TO workmind_app;
	-- Every table and sequence the owner creates is automatically usable by the app role.
	ALTER DEFAULT PRIVILEGES FOR ROLE workmind_owner IN SCHEMA public
		GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO workmind_app;
	ALTER DEFAULT PRIVILEGES FOR ROLE workmind_owner IN SCHEMA public
		GRANT USAGE, SELECT ON SEQUENCES TO workmind_app;
EOSQL
