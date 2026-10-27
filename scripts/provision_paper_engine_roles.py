"""Explicit administrator setup for an isolated synthetic-only PAPER database.

The administrator DSN belongs to provisioning, never to PaperEngine. This helper
returns separate runtime and synthetic-controller DSNs without printing them.
It grants no ownership, DDL, DELETE, TRUNCATE, superuser or role inheritance.
"""
from dataclasses import dataclass, field
from pathlib import Path
import re

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


@dataclass(frozen=True)
class PaperDatabaseRoles:
    runtime_dsn: str = field(repr=False)
    event_dsn: str = field(repr=False)
    runtime_role: str
    event_role: str


def require_isolated_paper_database(conn) -> str:
    database = conn.execute('SELECT current_database()').fetchone()[0]
    if not database.startswith('paper_engine_'):
        raise ValueError('dedicated paper_engine_ database required; existing application databases denied')
    if conn.execute("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='paper_sim')").fetchone()[0]:
        raise ValueError('existing paper_sim schema refused before migration')
    return database


def provision_paper_engine_roles(admin_dsn: str, *, isolated_paper: bool,
        runtime_role: str, event_role: str, runtime_password: str, event_password: str) -> PaperDatabaseRoles:
    if isolated_paper is not True or runtime_role == event_role:
        raise ValueError('explicit isolated PAPER database and distinct roles required')
    for role, password in ((runtime_role, runtime_password), (event_role, event_password)):
        if not re.fullmatch(r'paper_engine_[a-z0-9_]{1,45}', role) or len(password) < 24:
            raise ValueError('bounded PAPER role and independently generated strong password required')
    with psycopg.connect(admin_dsn) as conn:
        database = require_isolated_paper_database(conn)
        conn.execute((Path(__file__).resolve().parents[1] / 'migrations/0003_paper_engine.sql').read_text())
        for role, password in ((runtime_role, runtime_password), (event_role, event_password)):
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB '
                'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8').format(
                    sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        tables = sql.SQL(',').join(sql.Identifier(name) for name in
            ('paper_engine_orders', 'paper_engine_commands', 'paper_engine_events'))
        conn.execute(sql.SQL('REVOKE ALL ON {} FROM PUBLIC').format(tables))
        for role in (runtime_role, event_role):
            conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
                sql.Identifier(database), sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(tables, sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT INSERT (environment,scope_key,client_id,order_id,logical_id,'
            'owner_strategy_id,ownership_epoch,quantity_unit,price_currency,quantity) '
            'ON paper_engine_orders TO {}').format(sql.Identifier(runtime_role)))
        conn.execute(sql.SQL('GRANT INSERT ON paper_engine_commands TO {}').format(sql.Identifier(runtime_role)))
        conn.execute(sql.SQL('GRANT UPDATE (pending,pending_command_id,certainty,revision) '
            'ON paper_engine_orders TO {}').format(sql.Identifier(runtime_role)))
        conn.execute(sql.SQL('GRANT INSERT ON paper_engine_events TO {}').format(sql.Identifier(event_role)))
        conn.execute(sql.SQL('GRANT UPDATE (quantity,filled,lifecycle,pending,pending_command_id,certainty,revision) '
            'ON paper_engine_orders TO {}').format(sql.Identifier(event_role)))
    base = conninfo_to_dict(admin_dsn)
    return PaperDatabaseRoles(runtime_dsn=make_conninfo(**dict(base, user=runtime_role, password=runtime_password)),
        event_dsn=make_conninfo(**dict(base, user=event_role, password=event_password)),
        runtime_role=runtime_role, event_role=event_role)
