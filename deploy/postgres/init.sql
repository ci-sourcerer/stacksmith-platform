\getenv api_password PLATFORM_API_DB_PASSWORD
\getenv worker_password PLATFORM_WORKER_DB_PASSWORD
CREATE ROLE platform_api LOGIN PASSWORD :'api_password';
CREATE ROLE platform_worker LOGIN PASSWORD :'worker_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
