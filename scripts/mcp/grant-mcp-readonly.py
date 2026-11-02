"""Create the least-privilege MCP read-only role for the fixed DEV databases.

Run by the host administrator. The Agent must not run this automatically.
It grants SELECT only; no INSERT/UPDATE/DELETE/DDL, and no access to aether_dev
or paper_control_dev.
"""
from pathlib import Path
import secrets

import psycopg
from psycopg import sql

ROLE = "aether_mcp_readonly"
DBS = ("paper_runtime_dev", "paper_engine_dev")
ADMIN_PASSWORD_FILE = Path(r"D:\Projects\Python\AetherTrade\.secrets\dev_admin_password")
MCP_PASSWORD_FILE = Path(r"D:\Projects\Python\AetherTrade\.secrets\mcp_toolbox_db_password")

admin_password = ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
if not MCP_PASSWORD_FILE.exists():
    MCP_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    MCP_PASSWORD_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
mcp_password = MCP_PASSWORD_FILE.read_text(encoding="utf-8").strip()
if len(mcp_password) < 24:
    raise SystemExit("MCP password file must contain at least 24 characters")

def connect(db):
    return psycopg.connect(host="127.0.0.1", port=55432, dbname=db,
                           user="aether_dev_admin", password=admin_password)

with connect("postgres") as conn:
    row = conn.execute("SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
                       "rolinherit, rolreplication, rolbypassrls "
                       "FROM pg_roles WHERE rolname=%s", (ROLE,)).fetchone()
    if row is None:
        conn.execute(sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4"
        ).format(sql.Identifier(ROLE), sql.Literal(mcp_password)))
    else:
        if any(row[1:]):
            raise SystemExit("existing role has unsafe attributes; refusing")
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(ROLE), sql.Literal(mcp_password)))
    for db in DBS:
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(db), sql.Identifier(ROLE)))
    for db in DBS + ("paper_control_dev", "aether_dev"):
        conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
            sql.Identifier(db)))

for db in DBS:
    with connect(db) as conn:
        schemas = ("ledger", "public") if db == "paper_runtime_dev" else ("public",)
        for schema in schemas:
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(ROLE)))
            conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
                sql.Identifier(schema), sql.Identifier(ROLE)))
            conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(ROLE)))
            conn.execute(sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(ROLE)))
            conn.execute(sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE aether_dev_admin IN SCHEMA {} "
                "GRANT SELECT ON TABLES TO {}"
            ).format(sql.Identifier(schema), sql.Identifier(ROLE)))
print("MCP read-only role configured: " + ROLE)
print("Databases: " + ", ".join(DBS))
print("Password file: " + str(MCP_PASSWORD_FILE))
