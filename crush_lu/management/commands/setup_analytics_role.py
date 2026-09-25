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

from crush_lu.services.analytics_readonly import GRANTS, audit_role

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
            "--allow-connect-to",
            default="",
            help=(
                "Comma list of OTHER databases the role may still reach through "
                "PUBLIC's default CONNECT (only ones holding no member data)."
            ),
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
            # What the role can EFFECTIVELY do (PUBLIC grants included) must be
            # exactly the allowlist; otherwise roll everything back.
            violations = audit_role(cursor, ROLE)
            violations += self._other_database_access(
                cursor, options.get("allow_connect_to", "")
            )
            if violations:
                details = "\n  ".join(violations)
                raise CommandError(
                    f"{ROLE} would exceed its allowlist; nothing was changed:\n  {details}"
                )

        self.stdout.write(
            self.style.SUCCESS(f"{ROLE} is configured. Current privileges:")
        )
        self._audit(connection)

    def _other_database_access(self, cursor, allowed_csv):
        """Other databases the role can CONNECT to (normally via PUBLIC).

        audit_role() only sees the current database, and PUBLIC's default
        CONNECT cannot be revoked for one role alone. So every other reachable
        database must be hardened (REVOKE CONNECT ... FROM PUBLIC, after
        checking which logins use it) or explicitly acknowledged as holding no
        member data with --allow-connect-to.
        """
        allowed = {name.strip() for name in allowed_csv.split(",") if name.strip()}
        cursor.execute(
            "SELECT datname FROM pg_database WHERE NOT datistemplate "
            "AND datname <> current_database() "
            "AND has_database_privilege(%s, datname, 'CONNECT') ORDER BY 1",
            [ROLE],
        )
        return [
            f"can connect to database {name} (harden it, or acknowledge it with "
            f"--allow-connect-to if it holds no member data)"
            for (name,) in cursor.fetchall()
            if name not in allowed
        ]

    def _revoke_everything(self, cursor):
        """Reset the role to zero privileges before applying the allowlist.

        Reads the catalogs directly (every schema, not just public) so a
        pre-existing role cannot keep access beyond GRANTS: role memberships
        (a NOINHERIT member could still SET ROLE), relation and column ACLs,
        schema, database and function privileges. Ownership and default
        privileges cannot be revoked away, so they abort the run instead.
        """
        r = _qn(ROLE)
        cursor.execute(
            "SELECT (SELECT count(*) FROM pg_class WHERE relowner = %(o)s::regrole)"
            " + (SELECT count(*) FROM pg_namespace WHERE nspowner = %(o)s::regrole)"
            " + (SELECT count(*) FROM pg_proc WHERE proowner = %(o)s::regrole)"
            " + (SELECT count(*) FROM pg_database WHERE datdba = %(o)s::regrole)"
            " + (SELECT count(*) FROM pg_largeobject_metadata"
            "    WHERE lomowner = %(o)s::regrole)"
            " + (SELECT count(*) FROM pg_default_acl d, aclexplode(d.defaclacl) a"
            "    WHERE a.grantee = %(o)s::regrole OR d.defaclrole = %(o)s::regrole)",
            {"o": ROLE},
        )
        if cursor.fetchone()[0]:
            raise CommandError(
                f"{ROLE} owns objects, a database or a large object, or has default "
                "privileges; resolve that by hand "
                "(REASSIGN OWNED / ALTER DEFAULT PRIVILEGES) before re-running."
            )
        cursor.execute(
            "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
            "WHERE m.member = %s::regrole",
            [ROLE],
        )
        for (group,) in cursor.fetchall():
            cursor.execute(f"REVOKE {_qn(group)} FROM {r}")
        cursor.execute(
            "SELECT DISTINCT n.nspname, c.relname, c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE a.grantee = %s::regrole",
            [ROLE],
        )
        for schema, relation, kind in cursor.fetchall():
            keyword = "SEQUENCE" if kind == "S" else "TABLE"
            cursor.execute(
                f"REVOKE ALL ON {keyword} {_qn(schema)}.{_qn(relation)} FROM {r}"
            )
        cursor.execute(
            "SELECT n.nspname, c.relname, string_agg(DISTINCT at.attname, ',') "
            "FROM pg_attribute at JOIN pg_class c ON c.oid = at.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(at.attacl) a WHERE a.grantee = %s::regrole "
            "GROUP BY 1, 2",
            [ROLE],
        )
        for schema, relation, columns in cursor.fetchall():
            cols = ", ".join(_qn(c) for c in columns.split(","))
            cursor.execute(
                f"REVOKE ALL ({cols}) ON {_qn(schema)}.{_qn(relation)} FROM {r}"
            )
        cursor.execute(
            "SELECT DISTINCT n.nspname FROM pg_namespace n "
            "CROSS JOIN LATERAL aclexplode(n.nspacl) a WHERE a.grantee = %s::regrole",
            [ROLE],
        )
        for (schema,) in cursor.fetchall():
            cursor.execute(f"REVOKE ALL ON SCHEMA {_qn(schema)} FROM {r}")
        cursor.execute(
            "SELECT DISTINCT d.datname FROM pg_database d "
            "CROSS JOIN LATERAL aclexplode(d.datacl) a WHERE a.grantee = %s::regrole",
            [ROLE],
        )
        for (database,) in cursor.fetchall():
            cursor.execute(f"REVOKE ALL ON DATABASE {_qn(database)} FROM {r}")
        cursor.execute(
            "SELECT DISTINCT p.oid::regprocedure::text FROM pg_proc p "
            "CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE a.grantee = %s::regrole",
            [ROLE],
        )
        for (signature,) in cursor.fetchall():
            cursor.execute(f"REVOKE ALL ON FUNCTION {signature} FROM {r}")
        cursor.execute(
            "SELECT DISTINCT l.oid FROM pg_largeobject_metadata l "
            "CROSS JOIN LATERAL aclexplode(l.lomacl) a WHERE a.grantee = %s::regrole",
            [ROLE],
        )
        for (large_object,) in cursor.fetchall():
            cursor.execute(f"REVOKE ALL ON LARGE OBJECT {int(large_object)} FROM {r}")

    def _audit(self, connection):
        with connection.cursor() as cursor:
            for label, sql in AUDIT_SQL.items():
                cursor.execute(sql, [ROLE])
                rows = cursor.fetchall()
                self.stdout.write(f"-- {label}")
                for row in rows:
                    self.stdout.write("   " + " | ".join(str(v) for v in row))
            violations = audit_role(cursor, ROLE)
            self.stdout.write("-- effective privileges beyond the allowlist")
            self.stdout.write(
                "   none"
                if not violations
                else "\n".join(f"   {v}" for v in violations)
            )
