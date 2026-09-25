"""Create or refresh ``crush_analytics_ro``, the Crush Data MCP's read-only login.

Run by Tom on the production slot (Kudu webssh), as the app's own admin login:

    /antenv/bin/python manage.py setup_analytics_role            # create/refresh + audit
    /antenv/bin/python manage.py setup_analytics_role --dry-run  # print the SQL only
    /antenv/bin/python manage.py setup_analytics_role --audit    # print current privileges

Spec: ai-memory-hub/specs/2026-09-25-crush-data-mcp.md

The column grants come from ``analytics_readonly.GRANTS`` and nowhere else.
Every run first revokes whatever the role holds, so the result is always
exactly the allowlist. The password is read with ``getpass`` (never echoed,
never an argument) and sent to Postgres only as a SCRAM-SHA-256 verifier, so
no plaintext password can reach server logs.
"""

from __future__ import annotations

import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from crush_lu.services.analytics_readonly import GRANTS

ROLE = "crush_analytics_ro"
ROLE_SETTINGS = (
    ("default_transaction_read_only", "on"),
    ("statement_timeout", "10s"),
    ("lock_timeout", "1s"),
    ("idle_in_transaction_session_timeout", "15s"),
)


def _qn(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def grant_statements(role: str = ROLE, database: str = "pythonapp") -> list[str]:
    """CONNECT and schema USAGE explicitly, then one column GRANT per table.

    CONNECT and USAGE are granted to the role itself rather than relied on
    through PUBLIC, so revoking those from PUBLIC later cannot lock it out.
    """
    r = _qn(role)
    return [
        f"GRANT CONNECT ON DATABASE {_qn(database)} TO {r}",
        f"GRANT USAGE ON SCHEMA public TO {r}",
        *[
            f"GRANT SELECT ({', '.join(_qn(c) for c in columns)}) ON public.{_qn(table)} TO {r}"
            for table, columns in GRANTS.items()
        ],
    ]


def role_statements(role: str = ROLE) -> list[str]:
    """Role creation and hardening (no password, no grants)."""
    r = _qn(role)
    return [
        (
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN CREATE ROLE {r} LOGIN; END IF; END $$"
        ),
        (
            f"ALTER ROLE {r} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 3"
        ),
        *[f"ALTER ROLE {r} SET {name} = '{value}'" for name, value in ROLE_SETTINGS],
    ]


AUDIT_SQL = {
    "role": (
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
        "rolbypassrls, rolinherit, rolconnlimit, rolconfig FROM pg_roles WHERE rolname = %s"
    ),
    "memberships": (
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
        "JOIN pg_roles g ON g.oid = m.roleid WHERE r.rolname = %s ORDER BY 1"
    ),
    "table_privileges": (
        "SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.table_privileges WHERE grantee = %s GROUP BY 1 ORDER BY 1"
    ),
    "column_privileges": (
        "SELECT table_name, privilege_type, string_agg(column_name, ',' ORDER BY column_name) "
        "FROM information_schema.column_privileges WHERE grantee = %s GROUP BY 1, 2 ORDER BY 1, 2"
    ),
    "schema_create": "SELECT has_schema_privilege(%s, 'public', 'CREATE')",
}


class Command(BaseCommand):
    help = "Create/refresh the read-only crush_analytics_ro login from analytics_readonly.GRANTS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Print the SQL and exit."
        )
        parser.add_argument(
            "--audit",
            action="store_true",
            help="Print the role's current privileges and exit.",
        )
        parser.add_argument(
            "--keep-password",
            action="store_true",
            help="Refresh grants and settings without changing the password (role must exist).",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            for statement in role_statements():
                self.stdout.write(statement + ";")
            self.stdout.write(
                f"ALTER ROLE {_qn(ROLE)} PASSWORD '<scram-sha-256 verifier>';"
            )
            self.stdout.write(
                f"-- revoke every privilege {ROLE} currently holds (computed at run time)"
            )
            for statement in grant_statements():
                self.stdout.write(statement + ";")
            return

        connection = connections["default"]
        if connection.vendor != "postgresql":
            raise CommandError("setup_analytics_role only runs against PostgreSQL.")

        if options["audit"]:
            self._audit(connection)
            return

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user")
            database, user = cursor.fetchone()
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [ROLE])
            exists = cursor.fetchone() is not None
        self.stdout.write(
            f"Database: {database}   connected as: {user}   role exists: {exists}"
        )
        if database != "pythonapp":
            confirm = input(
                f"This is '{database}', not 'pythonapp'. Type its name to continue: "
            )
            if confirm != database:
                raise CommandError("Aborted.")

        verifier = None
        if options["keep_password"]:
            if not exists:
                raise CommandError(
                    f"{ROLE} does not exist yet; run without --keep-password."
                )
        else:
            password = getpass.getpass(f"New password for {ROLE}: ")
            if len(password) < 24:
                raise CommandError("Use at least 24 characters.")
            if getpass.getpass("Repeat it: ") != password:
                raise CommandError("Passwords do not match.")
            from psycopg2.extensions import encrypt_password

            connection.ensure_connection()
            verifier = encrypt_password(
                password, ROLE, connection.connection, "scram-sha-256"
            )
            del password

        with transaction.atomic(using="default"), connection.cursor() as cursor:
            for statement in role_statements():
                cursor.execute(statement)
            if verifier:
                cursor.execute(f"ALTER ROLE {_qn(ROLE)} PASSWORD %s", [verifier])
            self._revoke_everything(cursor)
            for statement in grant_statements(database=database):
                cursor.execute(statement)

        self.stdout.write(
            self.style.SUCCESS(f"{ROLE} is configured. Current privileges:")
        )
        self._audit(connection)

    def _revoke_everything(self, cursor):
        """Reset to zero: table-level and column-level privileges in public."""
        r = _qn(ROLE)
        cursor.execute(
            "SELECT DISTINCT table_name FROM information_schema.table_privileges "
            "WHERE grantee = %s AND table_schema = 'public'",
            [ROLE],
        )
        for (table,) in cursor.fetchall():
            cursor.execute(f"REVOKE ALL ON public.{_qn(table)} FROM {r}")
        cursor.execute(
            "SELECT table_name, string_agg(DISTINCT column_name, ',') "
            "FROM information_schema.column_privileges "
            "WHERE grantee = %s AND table_schema = 'public' GROUP BY 1",
            [ROLE],
        )
        for table, columns in cursor.fetchall():
            cols = ", ".join(_qn(c) for c in columns.split(","))
            cursor.execute(f"REVOKE ALL ({cols}) ON public.{_qn(table)} FROM {r}")

    def _audit(self, connection):
        with connection.cursor() as cursor:
            for label, sql in AUDIT_SQL.items():
                cursor.execute(sql, [ROLE])
                rows = cursor.fetchall()
                self.stdout.write(f"-- {label}")
                for row in rows:
                    self.stdout.write("   " + " | ".join(str(v) for v in row))
