"""Explicit creation of fresh isolated PAPER databases and restricted logins.

No existing database is migrated or reset. Administrator credentials are used
only here, never saved in the generated runtime configuration or printed.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy.engine import URL

from .paper_runtime import UnifiedPaperRuntime


@dataclass(frozen=True)
class ProvisionedPaper:
    ledger_dsn: str = field(repr=False)
    venue_dsn: str = field(repr=False)
    event_dsn: str = field(repr=False)
    console_database_url: str = field(repr=False)
    journal_directory: str

    def runtime_kwargs(self):
        return dict(ledger_dsn=self.ledger_dsn, venue_dsn=self.venue_dsn,
            event_dsn=self.event_dsn, journal_directory=self.journal_directory)


def provision(admin_dsn, *, initial_cash, journal_directory, isolated_paper=False, output_path=None):
    if isolated_paper is not True:
        raise ValueError('explicit isolated PAPER provisioning required')
    output = Path(output_path).resolve() if output_path else None
    if output and output.exists():
        raise ValueError('configuration destination already exists; never overwrite credentials')
    # Provisioning is a repository administrator operation; the application
    # never imports or invokes it during an order or ordinary startup.
    root = Path(__file__).resolve().parents[3]
    from scripts.provision_paper_engine_roles import provision_paper_engine_roles
    base = conninfo_to_dict(admin_dsn)
    suffix = uuid4().hex[:12]
    business, venue, console = 'paper_runtime_' + suffix, 'paper_engine_' + suffix, 'paper_control_' + suffix
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for name in (business, venue, console):
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    business_admin = make_conninfo(**dict(base, dbname=business))
    with psycopg.connect(business_admin) as conn:
        for path in ('migrations/0001_business_ledger.sql', 'deploy/db/roles.sql',
                     'migrations/0002_risk_resolution_authority.sql', 'migrations/0004_command_abort_guard.sql',
                     'migrations/0005_owner_cas_authority.sql', 'migrations/0006_proposal_intake_role.sql',
                     'migrations/0007_owner_controller_ddl_guard.sql'):
            conn.execute((root / path).read_text(encoding='utf-8'))
        role, password = 'paper_console_' + suffix, secrets.token_urlsafe(32)
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE '
            'NOINHERIT NOREPLICATION NOBYPASSRLS').format(sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(business)))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(business), sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT ledger_owner TO {}').format(sql.Identifier(role)))
    UnifiedPaperRuntime.provision(business_admin, initial_cash=initial_cash)
    venue_roles = provision_paper_engine_roles(make_conninfo(**dict(base, dbname=venue)), isolated_paper=True,
        runtime_role='paper_engine_run_' + suffix, event_role='paper_engine_evt_' + suffix,
        runtime_password=secrets.token_urlsafe(32), event_password=secrets.token_urlsafe(32))
    console_role, console_password = 'paper_control_' + suffix, secrets.token_urlsafe(32)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE '
            'NOINHERIT NOREPLICATION NOBYPASSRLS').format(sql.Identifier(console_role), sql.Literal(console_password)))
        conn.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(sql.Identifier(console), sql.Identifier(console_role)))
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(console)))
    console_url = URL.create('postgresql+psycopg', username=console_role, password=console_password,
        host=base.get('host'), port=int(base.get('port', '5432')), database=console).render_as_string(hide_password=False)
    result = ProvisionedPaper(ledger_dsn=make_conninfo(**dict(base, dbname=business, user=role, password=password)),
        venue_dsn=venue_roles.runtime_dsn, event_dsn=venue_roles.event_dsn,
        console_database_url=console_url, journal_directory=str(Path(journal_directory).resolve()))
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids overwriting another provisioning result.
        with output.open('x', encoding='utf-8') as stream:
            json.dump(dict(database_url=result.console_database_url, **result.runtime_kwargs(), initial_cash=initial_cash), stream, indent=2)
        try:
            output.chmod(0o600)
        except OSError:
            pass  # Windows ACLs are inherited from the explicitly chosen folder.
    return result
