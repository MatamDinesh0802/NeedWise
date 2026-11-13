#!/usr/bin/env bash
# Runs once, on the first start of an empty Postgres volume.
# Creates one database per service, plus two roles for NeedWise:
#   needwise_owner: owns the schema, used only by migrations
#   needwise_app:   runtime role, data access only, can never bypass Row-Level Security
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
	CREATE ROLE needwise_owner LOGIN PASSWORD '${NEEDWISE_OWNER_PASSWORD}';
	CREATE ROLE needwise_app LOGIN PASSWORD '${NEEDWISE_APP_PASSWORD}' NOBYPASSRLS;
	CREATE DATABASE needwise OWNER needwise_owner;

	CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
	CREATE DATABASE keycloak OWNER keycloak;

	CREATE ROLE openfga LOGIN PASSWORD '${OPENFGA_DB_PASSWORD}';
	CREATE DATABASE openfga OWNER openfga;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname needwise <<-EOSQL
	REVOKE ALL ON SCHEMA public FROM PUBLIC;
	GRANT USAGE ON SCHEMA public TO needwise_app;
	-- Every table and sequence the owner creates is automatically usable by the app role.
	ALTER DEFAULT PRIVILEGES FOR ROLE needwise_owner IN SCHEMA public
		GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO needwise_app;
	ALTER DEFAULT PRIVILEGES FOR ROLE needwise_owner IN SCHEMA public
		GRANT USAGE, SELECT ON SEQUENCES TO needwise_app;
EOSQL
