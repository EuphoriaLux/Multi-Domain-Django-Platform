"""Read-only, pseudonymized production analytics behind the Crush Data MCP.

Spec: ai-memory-hub/specs/2026-09-25-crush-data-mcp.md

AI agents reach these functions through ``GET /api/analytics/<tool>/``
(``crush_lu/api_analytics.py``) via a local stdio MCP server. Every query runs
on the ``settings.ANALYTICS_DB_ALIAS`` connection, which in production logs in
as ``crush_analytics_ro``: a read-only role that holds only the column grants
listed in ``GRANTS``. Two rules follow, and both are enforced by tests:

* Never load model instances (``.get()``, iterating a queryset,
  ``select_related``). They SELECT every column, most of which the role cannot
  read, so the query fails in production while passing on SQLite (which has no
  grants). Use ``.values()`` / ``.values_list()`` / aggregates, always with an
  explicit ``.order_by()`` so a model's ``Meta.ordering`` cannot drag an
  ungranted column into the SQL. ``test_api_analytics.SqlColumnAuditTests``
  parses every statement and fails on any column outside ``GRANTS``.
* ``GRANTS`` is the single source of truth for the database boundary:
  ``manage.py setup_analytics_role`` turns it into the GRANT statements.

Nothing returned here identifies a person: members appear as keyed pseudonyms,
ages as bands, dates at day precision, and small demographic cross-tab cells
are suppressed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from statistics import median

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.db.models import Count, Q, Sum
from django.utils import timezone

from crush_lu.models import (
    CreditRedemption,
    CrushConnectMembership,
    CrushCredit,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PaymentTransaction,
    PremiumMembership,
    ProfileSubmission,
    UserDataConsent,
    WeeklyMetricsSnapshot,
)
from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest, ConnectWeekSession
from crush_lu.models.events import SEAT_HOLDING_STATUSES

User = get_user_model()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The database boundary. Column-level SELECT grants for crush_analytics_ro in
# the production ``pythonapp`` database, and nothing else. Adding a column here
# is a privacy decision: never add names, email, username, phone, photos,
# tokens, IPs, free text, SumUp ids or payloads, or GDPR Art. 9-adjacent fields
# (preferred_genders, Connect lifestyle answers).
# ---------------------------------------------------------------------------
# Exactly the columns the generated SQL reads (SqlColumnAuditTests checks
# both directions), so leaked credentials expose nothing no tool needs.
GRANTS: dict[str, tuple[str, ...]] = {
    "auth_user": ("id", "is_staff", "is_superuser"),
    "crush_lu_crushprofile": (
        "id",
        "user_id",
        "gender",
        "date_of_birth",  # only ever turned into an age band, never returned
        "location",  # canton-* / border-* codes
        "verification_status",
        "verification_method",
        # Legacy step marker, still set to "submitted" by the self-serve path,
        # which creates no ProfileSubmission row.
        "completion_status",
        "phone_verified",
        "created_at",
    ),
    "crush_lu_userdataconsent": (
        "id",
        "user_id",
        "crushlu_banned",
    ),
    "crush_lu_profilesubmission": (
        "id",
        "profile_id",
        "coach_id",
        "status",
        "submitted_at",
        "reviewed_at",
    ),
    "crush_lu_meetupevent": (
        "id",
        # modeltranslation rewrites .values("title") to the active language's
        # column (with fallbacks), so every translation column needs the grant.
        "title",
        "title_en",
        "title_de",
        "title_fr",
        "event_type",
        "canton",
        "date_time",
        "registration_fee",
        "max_participants",
        "max_participants_m",
        "max_participants_f",
        "max_participants_nb",
        "reserved_premium_seats",
        "registration_mode",
        "is_published",
        "is_cancelled",
    ),
    "crush_lu_eventregistration": (
        "id",
        "event_id",
        "user_id",
        "status",
        "registered_at",
        "payment_confirmed",
        "checked_in_at",
    ),
    "crush_lu_paymenttransaction": (
        "id",
        "user_id",
        "event_id",
        "provider",
        "status",
        "purpose",
        "amount",
        "created_at",
        "paid_at",
    ),
    "crush_lu_crushcredit": (
        "id",
        "user_id",
        "reason",
        "status",
        "amount_cents",
        "issued_at",
    ),
    "crush_lu_creditredemption": (
        "id",
        "credit_id",
        "event_registration_id",
        "amount_cents",
        "redeemed_at",
    ),
    "crush_lu_premiummembership": (
        "id",
        "user_id",
        "status",
        "created_at",
    ),
    "crush_lu_crushconnectmembership": (
        "id",
        "user_id",
        "created_at",
        "onboarding_started_at",
        "onboarded_at",
        "onboarding_step",
        "paused_at",
        "excluded_by_coach",
    ),
    "crush_lu_connectweeksession": (
        "id",
        "user_id",
        "status",
        "started_at",
    ),
    "crush_lu_connectweeklyrequest": (
        "id",
        "requester_id",
        "recipient_id",
        "status",
        "sent_at",
        "responded_at",
    ),
    "crush_lu_weeklymetricssnapshot": (
        "id",
        "week_start",
        "week_end",
        "metrics",
        "computed_at",
    ),
    # LuxID flag only: never ``uid`` / ``extra_data``, never token values.
    "socialaccount_socialaccount": ("id", "user_id", "provider"),
    "socialaccount_socialtoken": ("id", "account_id", "app_id"),
    "socialaccount_socialapp": ("id", "provider", "provider_id"),
}

AGE_BANDS = (
    (18, 24, "18-24"),
    (25, 29, "25-29"),
    (30, 34, "30-34"),
    (35, 39, "35-39"),
    (40, 44, "40-44"),
    (45, 49, "45-49"),
    (50, 200, "50+"),
)
SUPPRESSION_FLOOR = 5
SUPPRESSED = "suppressed"
UNKNOWN = "unknown"
DEMOGRAPHIC_DIMENSIONS = ("gender", "age_band", "canton")
GROUPABLE_DIMENSIONS = DEMOGRAPHIC_DIMENSIONS + ("verification_status", "luxid")
MAX_RANGE_DAYS = 400
DEFAULT_RANGE_DAYS = 90
MAX_EVENTS = 500
MAX_MEMBER_ROWS = 2000
DEFAULT_MEMBER_ROWS = 500
MAX_KPI_WEEKS = 52

# The profile form's LOCATION_CHOICES. The model field is free text (and
# editable in the admin), so anything else is reported as "other", never
# verbatim: a stray value could be a finer location than a canton.
LOCATION_CODES = frozenset(
    {
        "canton-capellen", "canton-clervaux", "canton-diekirch", "canton-echternach",
        "canton-esch", "canton-grevenmacher", "canton-luxembourg", "canton-mersch",
        "canton-redange", "canton-remich", "canton-vianden", "canton-wiltz",
        "border-belgium", "border-germany", "border-france",
    }
)  # fmt: skip
MIN_DATE = date(2000, 1, 1)
MAX_DATE = date(2100, 12, 31)
_ATTENDED = Q(status="attended") | Q(checked_in_at__isnull=False)

# Seeded / QA accounts (see crush_lu/management/commands/create_connect_test_users).
_TEST_USERNAME = (
    Q(username__startswith="connect_premium_")
    | Q(username__startswith="connect_candidate_")
    | Q(username__startswith="beta_nolux")
    | Q(username__regex=r"^testuser[0-9]+$")
)

REAL_MEMBER_RULE = (
    "A real member has a CrushProfile and is not staff, not superuser, not banned "
    "(UserDataConsent.crushlu_banned), and not a test account (email domain in "
    "TEST_EMAIL_DOMAINS or a seeded QA username). Guests who registered without a "
    "profile are not members. Every tool except kpi_weekly counts real members "
    "only; excluded accounts (including profile-less guests) are reported "
    "separately where they touch money or seats."
)


class NotConfigured(Exception):
    """The analytics alias, API key or pseudonym key is missing."""


class NotFound(Exception):
    """The requested object does not exist."""


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    return bool(
        getattr(settings, "ANALYTICS_API_KEY", "")
        and getattr(settings, "ANALYTICS_PSEUDONYM_KEY", "")
        and getattr(settings, "ANALYTICS_DB_ALIAS", "") in settings.DATABASES
    )


def _alias() -> str:
    if not is_configured():
        raise NotConfigured("analytics is not configured")
    alias = settings.ANALYTICS_DB_ALIAS
    # Re-verified at most every 10 minutes per worker; a failure is never cached.
    _assert_least_privilege(alias, int(timezone.now().timestamp() // 600))
    return alias


# ---------------------------------------------------------------------------
# Effective-privilege audit. GRANTS is only a boundary if the login really
# holds nothing else: not table-level SELECT, not writes, not CREATE, not an
# elevated attribute, and not privileges reaching it through PUBLIC or a
# membership. has_*_privilege() resolves all of those, so the audit asks
# PostgreSQL what the role can effectively do rather than reading its ACLs.
# Used at request time (below) and by `manage.py setup_analytics_role`.
# ---------------------------------------------------------------------------

PRIVILEGE_AUDIT_SQL = {
    "elevated": (
        "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls "
        "OR rolreplication FROM pg_roles WHERE rolname = %(role)s"
    ),
    "relations": (
        "SELECT n.nspname, c.relname, "
        "has_table_privilege(%(role)s, c.oid, 'SELECT'), "
        "has_any_column_privilege(%(role)s, c.oid, 'SELECT'), "
        "has_table_privilege(%(role)s, c.oid, "
        "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
        "OR has_any_column_privilege(%(role)s, c.oid, 'INSERT,UPDATE,REFERENCES') "
        "OR (current_setting('server_version_num')::int >= 170000 "
        "AND has_table_privilege(%(role)s, c.oid, 'MAINTAIN')), "
        # What PUBLIC itself may read: system catalogs are compared against it.
        # 'public' names the PUBLIC pseudo-role in every has_*_privilege
        # function (PostgreSQL docs, "Access Privilege Inquiry Functions").
        "has_table_privilege('public', c.oid, 'SELECT') "
        "OR has_any_column_privilege('public', c.oid, 'SELECT') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 't')"
    ),
    "columns": (
        "SELECT c.relname, a.attname FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%(tables)s) "
        "AND a.attnum > 0 AND NOT a.attisdropped "
        "AND has_column_privilege(%(role)s, c.oid, a.attnum, 'SELECT')"
    ),
    # A grant option would let the login re-grant what it holds (an allowed
    # member-data column to PUBLIC, say) and so bypass the API key. PUBLIC can
    # never hold a grant option and the login is NOINHERIT with no memberships,
    # so its own ACL entries are the complete set.
    "grant_options": (
        "SELECT 'relation', c.oid::regclass::text FROM pg_class c "
        "CROSS JOIN LATERAL aclexplode(c.relacl) a "
        "WHERE a.grantee = %(role)s::regrole AND a.is_grantable "
        "UNION ALL "
        "SELECT 'column', c.oid::regclass::text || '.' || quote_ident(at.attname) "
        "FROM pg_attribute at JOIN pg_class c ON c.oid = at.attrelid "
        "CROSS JOIN LATERAL aclexplode(at.attacl) a "
        "WHERE a.grantee = %(role)s::regrole AND a.is_grantable "
        "UNION ALL "
        "SELECT 'schema', n.nspname::text FROM pg_namespace n "
        "CROSS JOIN LATERAL aclexplode(n.nspacl) a "
        "WHERE a.grantee = %(role)s::regrole AND a.is_grantable "
        "UNION ALL "
        "SELECT 'database', d.datname::text FROM pg_database d "
        "CROSS JOIN LATERAL aclexplode(d.datacl) a "
        "WHERE a.grantee = %(role)s::regrole AND a.is_grantable "
        "UNION ALL "
        "SELECT 'routine', p.oid::regprocedure::text FROM pg_proc p "
        "CROSS JOIN LATERAL aclexplode(p.proacl) a "
        "WHERE a.grantee = %(role)s::regrole AND a.is_grantable "
        "UNION ALL "
        "SELECT 'large object', l.oid::text FROM pg_largeobject_metadata l "
        "CROSS JOIN LATERAL aclexplode(l.lomacl) a "
        "WHERE a.grantee = %(role)s::regrole AND a.is_grantable "
    ),
    "sequences": (
        "SELECT n.nspname, c.relname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind = 'S' "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND has_sequence_privilege(%(role)s, c.oid, 'SELECT,USAGE,UPDATE')"
    ),
    # CREATE on this database (which ownership implies) would let the login
    # create a schema of its own; existing-schema CREATE is checked below.
    # TEMPORARY would let it create and fill temp tables despite read-only
    # defaults; setup revokes PUBLIC's default TEMPORARY on this database.
    "database_create": (
        "SELECT has_database_privilege(%(role)s, current_database(), 'CREATE'), "
        "has_database_privilege(%(role)s, current_database(), 'TEMPORARY')"
    ),
    # Ownership of anything (types, domains, operators, ... not only relations)
    # in ANY database of the cluster or of shared objects, from the shared
    # dependency register: the login can reach other databases too.
    "owned_objects": (
        "SELECT count(*) FROM pg_shdepend "
        "WHERE refclassid = 'pg_authid'::regclass "
        "AND refobjid = %(role)s::regrole AND deptype = 'o'"
    ),
    # Default privileges would expose objects that do not exist yet: the next
    # table another owner creates would be readable before any audit sees it.
    # Any entry granting this login anything, or granting PUBLIC anything on
    # tables, sequences, schemas or large objects, is excess. (PUBLIC's
    # built-in EXECUTE on functions and USAGE on types are not stored here.)
    "default_privileges": (
        "SELECT pg_get_userbyid(d.defaclrole), COALESCE(n.nspname, '*'), "
        "d.defaclobjtype::text, a.privilege_type, a.grantee = 0 "
        "FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace "
        "CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
        "WHERE a.grantee = %(role)s::regrole "
        "OR (a.grantee = 0 AND d.defaclobjtype IN ('r', 'S', 'n', 'L'))"
    ),
    # The login must keep USAGE on public, or every column grant is unusable.
    "public_usage": ("SELECT has_schema_privilege(%(role)s, 'public', 'USAGE')"),
    # Catalog columns the login can read that PUBLIC could not read by default.
    # Defaults come from pg_init_privs (written by initdb, never by a later
    # GRANT), at relation or column level (pg_subscription is column-granted).
    # information_schema is created after initdb records them, so it is
    # compared with PUBLIC's live grants in "relations" instead.
    "system_columns": (
        "SELECT n.nspname, c.relname, string_agg(a.attname, ',' ORDER BY a.attnum) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid "
        "AND a.attnum > 0 AND NOT a.attisdropped "
        "WHERE (n.nspname = 'pg_catalog' OR n.nspname LIKE 'pg_toast%%') "
        "AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 't') "
        "AND has_column_privilege(%(role)s, c.oid, a.attnum, 'SELECT') "
        "AND NOT EXISTS (SELECT 1 FROM pg_init_privs i "
        "CROSS JOIN LATERAL aclexplode(i.initprivs) d "
        "WHERE i.classoid = 'pg_class'::regclass AND i.objoid = c.oid "
        "AND i.objsubid IN (0, a.attnum) "
        "AND d.grantee = 0 AND d.privilege_type = 'SELECT') "
        "GROUP BY 1, 2"
    ),
    # Large objects sit outside pg_class: owned, granted (directly or to
    # PUBLIC, grantee 0) or opened to everyone by lo_compat_privileges.
    "large_objects": (
        "SELECT count(*) FROM pg_largeobject_metadata l "
        "WHERE l.lomowner = %(role)s::regrole "
        "OR current_setting('lo_compat_privileges') = 'on' "
        "OR EXISTS (SELECT 1 FROM aclexplode(l.lomacl) a "
        "WHERE a.grantee = 0 OR a.grantee = %(role)s::regrole)"
    ),
    # CONNECT elsewhere must be allowlisted, and CREATE there is excess even
    # when it is. Template databases count too (template1 is connectable by
    # default, and a copy of app data could be marked as a template): only
    # datallowconn, which nobody can bypass, rules a database out. TEMPORARY
    # there is not audited: the allowlisted databases are Azure-managed or
    # empty templates (azure_sys and azure_maintenance are owned by azuresu,
    # so no login of ours can change their ACLs), temp tables there cannot read
    # member data, and these credentials sit beside the admin password in the
    # same App Service settings.
    "other_databases": (
        "SELECT datname, has_database_privilege(%(role)s, datname, 'CREATE') "
        "FROM pg_database WHERE datallowconn "
        "AND datname <> current_database() "
        "AND has_database_privilege(%(role)s, datname, 'CONNECT')"
    ),
    "memberships": (
        "SELECT g.rolname FROM pg_auth_members m "
        "JOIN pg_roles g ON g.oid = m.roleid "
        "JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = %(role)s"
    ),
    # EXECUTE reaches every login through PUBLIC by default. Invoker-rights
    # functions run with the caller's own (read-only, allowlisted) rights, so
    # only SECURITY DEFINER functions, which run as their owner, can reach
    # beyond GRANTS. System schemas are excluded.
    "definer_functions": (
        "SELECT n.nspname, p.proname FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE p.prosecdef "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_%%' "
        "AND has_schema_privilege(%(role)s, n.oid, 'USAGE') "
        "AND has_function_privilege(%(role)s, p.oid, 'EXECUTE')"
    ),
    # Routines authorize through their EXECUTE ACL, not table rights. In every
    # schema, including pg_catalog, it is excess when the login can run a
    # routine that PUBLIC cannot (a direct grant), or one that is restricted
    # by default, even through a later grant to PUBLIC. "Restricted by default"
    # comes from pg_init_privs, which initdb and extension scripts write and a
    # later GRANT never changes: an entry there whose ACL gives PUBLIC no
    # EXECUTE (lo_import, pg_read_file, ...). The last column says whether
    # the routine is reached through PUBLIC.
    "privileged_routines": (
        "SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), "
        "has_function_privilege('public', p.oid, 'EXECUTE') "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "LEFT JOIN pg_init_privs i ON i.objoid = p.oid "
        "AND i.classoid = 'pg_proc'::regclass AND i.objsubid = 0 "
        "WHERE has_function_privilege(%(role)s, p.oid, 'EXECUTE') "
        "AND (NOT has_function_privilege('public', p.oid, 'EXECUTE') "
        "OR (i.initprivs IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM aclexplode(i.initprivs) d "
        "WHERE d.grantee = 0 AND d.privilege_type = 'EXECUTE')))"
    ),
    "schemas_with_create": (
        "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%%' "
        "AND nspname <> 'information_schema' "
        "AND has_schema_privilege(%(role)s, oid, 'CREATE')"
    ),
}


# Roles that can use the login's privileges: members of it that inherit its
# rights or may SET ROLE to it. From PostgreSQL 16 a membership carries its own
# options; one with ADMIN OPTION only (what CREATE ROLE gives a non-superuser
# creator, such as the app's admin login) manages the role but cannot use it.
ROLE_MEMBERS_SQL = (
    "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
    "WHERE m.roleid = %(role)s::regrole AND (m.inherit_option OR m.set_option)"
)
ROLE_MEMBERS_SQL_BEFORE_16 = (
    "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
    "WHERE m.roleid = %(role)s::regrole"
)

# Parameters whose SET widens what the login can read. SET on one of these is
# a violation even when it comes through PUBLIC: the audit session only sees
# its own value, so the login could pass the audit and SET it elsewhere.
SENSITIVE_PARAMETERS = ("lo_compat_privileges",)

# PostgreSQL 15+ (pg_parameter_acl, has_parameter_privilege); audit_role skips
# it on older servers, where only superusers could SET these at all. Any
# ALTER SYSTEM right, and SET on a parameter beyond what PUBLIC holds, is also
# excess.
PARAMETER_AUDIT_SQL = (
    "SELECT parname FROM (SELECT parname FROM pg_parameter_acl "
    "UNION SELECT unnest(%(sensitive)s::text[])) p "
    "WHERE has_parameter_privilege(%(role)s, parname, 'ALTER SYSTEM') "
    "OR (has_parameter_privilege(%(role)s, parname, 'SET') "
    "AND (parname = ANY(%(sensitive)s::text[]) "
    "OR NOT has_parameter_privilege('public', parname, 'SET'))) "
    "ORDER BY parname"
)


# Catalogs that hold password verifiers, data samples, credentials or large
# object contents: readable by the analytics login is always a violation.
SENSITIVE_CATALOGS = frozenset(
    {"pg_authid", "pg_shadow", "pg_statistic", "pg_user_mapping", "pg_largeobject"}
)


def _is_system_schema(schema: str) -> bool:
    return schema in ("pg_catalog", "information_schema") or schema.startswith(
        "pg_toast"
    )


def privilege_violations(
    elevated,
    relations,
    columns,
    schemas_with_create,
    memberships=(),
    definer_functions=(),
    sequences=(),
    other_databases=(),
    allowed_databases=(),
    database_create=False,
    large_objects=0,
    database_temp=False,
    owned_objects=0,
    routines=(),
    parameters=(),
    grant_options=(),
    role_members=(),
    public_usage=True,
    system_columns=(),
    default_privileges=(),
) -> list:
    """Everything the role can effectively do beyond GRANTS, and any GRANTS
    column it can no longer read (pure; testable).

    Any role membership is a violation: the login is NOINHERIT, so
    has_*_privilege does not see a member role's rights, yet the login could
    SET ROLE to use them.
    """
    violations = []
    if elevated:
        violations.append("role has an elevated attribute")
    for group in memberships:
        violations.append(f"member of role {group}")
    for schema, function in definer_functions:
        violations.append(f"can execute SECURITY DEFINER {schema}.{function}")
    for row in routines:
        schema, routine, arguments = row[:3]
        via_public = row[3] if len(row) > 3 else False
        how = (
            "through a PUBLIC grant it does not have by default"
            if via_public
            else "beyond PUBLIC"
        )
        violations.append(f"can EXECUTE {schema}.{routine}({arguments}) {how}")
    for parameter in parameters:
        violations.append(f"can SET or ALTER SYSTEM parameter {parameter}")
    for kind, name in grant_options:
        violations.append(f"holds a grant option on {kind} {name}")
    for member in role_members:
        violations.append(f"role {member} can use this login's privileges")
    if not public_usage:
        violations.append("lacks USAGE on schema public (re-run setup_analytics_role)")
    kinds = {"r": "tables", "S": "sequences", "f": "functions", "T": "types"}
    kinds.update({"n": "schemas", "L": "large objects"})
    for owner, schema, kind, privilege, to_public in default_privileges:
        violations.append(
            f"default privileges of {owner} grant {privilege} on future "
            f"{kinds.get(kind, kind)} in schema {schema} to "
            + ("PUBLIC" if to_public else "this login")
        )
    for schema, relation, attributes in system_columns:
        violations.append(
            f"can read {schema}.{relation} ({attributes}), "
            "which PUBLIC cannot by default"
        )
    for schema, sequence in sequences:
        violations.append(f"can use sequence {schema}.{sequence}")
    if database_create:
        violations.append("can CREATE in the current database")
    if database_temp:
        violations.append("can create TEMPORARY tables in the current database")
    if owned_objects:
        violations.append(f"owns {owned_objects} object(s)")
    if large_objects:
        violations.append(f"can access {large_objects} large object(s)")
    for entry in other_databases:
        database, can_create = (
            entry if isinstance(entry, (tuple, list)) else (entry, False)
        )
        if can_create:
            violations.append(f"can CREATE in database {database}")
        if database not in allowed_databases:
            violations.append(
                f"can connect to database {database} (revoke PUBLIC CONNECT there, "
                "or add it to ANALYTICS_ALLOWED_OTHER_DATABASES if it holds no "
                "member data)"
            )
    for row in relations:
        schema, relation, table_select, any_column_select, can_write = row[:5]
        public_can_read = row[5] if len(row) > 5 else False
        name = f"{schema}.{relation}"
        if can_write:
            violations.append(f"can write {name}")
        if _is_system_schema(schema):
            # PUBLIC's default catalog access is harmless; anything beyond it,
            # and any access at all to a catalog holding secrets or data
            # samples, is not.
            if any_column_select and (
                not public_can_read or relation in SENSITIVE_CATALOGS
            ):
                violations.append(f"can read system relation {name}")
            continue
        if table_select:
            violations.append(f"table-level SELECT on {name}")
        if any_column_select and (schema != "public" or relation not in GRANTS):
            violations.append(f"can read {name}")
    for relation, column in columns:
        if column not in GRANTS.get(relation, ()):
            violations.append(f"can read public.{relation}.{column}")
    # A required grant that went missing would otherwise surface later as a
    # permission error inside whichever tool reads it.
    readable = set(map(tuple, columns))
    for relation, required in GRANTS.items():
        for column in required:
            if (relation, column) not in readable:
                violations.append(
                    f"lacks SELECT on public.{relation}.{column} "
                    "(re-run setup_analytics_role)"
                )
    for schema in schemas_with_create:
        violations.append(f"can CREATE in schema {schema}")
    return violations


def audit_role(cursor, role: str) -> list:
    params = {"role": role, "tables": list(GRANTS)}
    results = {}
    for key, sql in PRIVILEGE_AUDIT_SQL.items():
        cursor.execute(sql, params)
        results[key] = cursor.fetchall()
    parameters = []
    cursor.execute("SHOW server_version_num")
    version = int(cursor.fetchone()[0])
    if version >= 150000:
        cursor.execute(
            PARAMETER_AUDIT_SQL, {**params, "sensitive": list(SENSITIVE_PARAMETERS)}
        )
        parameters = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        ROLE_MEMBERS_SQL if version >= 160000 else ROLE_MEMBERS_SQL_BEFORE_16, params
    )
    role_members = [row[0] for row in cursor.fetchall()]
    elevated = bool(results["elevated"] and results["elevated"][0][0])
    return privilege_violations(
        elevated,
        results["relations"],
        results["columns"],
        [row[0] for row in results["schemas_with_create"]],
        [row[0] for row in results["memberships"]],
        results["definer_functions"],
        results["sequences"],
        results["other_databases"],
        getattr(settings, "ANALYTICS_ALLOWED_OTHER_DATABASES", ()),
        bool(results["database_create"] and results["database_create"][0][0]),
        results["large_objects"][0][0] if results["large_objects"] else 0,
        bool(results["database_create"] and results["database_create"][0][1]),
        results["owned_objects"][0][0] if results["owned_objects"] else 0,
        results["privileged_routines"],
        parameters,
        results["grant_options"],
        role_members,
        bool(results["public_usage"] and results["public_usage"][0][0]),
        results["system_columns"],
        results["default_privileges"],
    )


@lru_cache(maxsize=8)
def _assert_least_privilege(alias: str, _ttl_bucket: int) -> bool:
    connection = connections[alias]
    # Tests point the alias at the SQLite test DB ("default"); production pins
    # "analytics", which settings never let be anything else.
    if connection.vendor != "postgresql" or alias == "default":
        return True
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        role = cursor.fetchone()[0]
        violations = audit_role(cursor, role)
    if violations:
        logger.error(
            "Analytics login %s exceeds its allowlist: %s", role, "; ".join(violations)
        )
        raise NotConfigured("analytics database login exceeds its allowlist")
    return True


def pseudonym(user_id: int) -> str:
    """Stable, non-reversible member key (HMAC-SHA256, 16 hex chars)."""
    key = settings.ANALYTICS_PSEUDONYM_KEY.encode()
    return hmac.new(key, f"user:{user_id}".encode(), hashlib.sha256).hexdigest()[:16]


def age_band(dob: date | None, on: date) -> str | None:
    if not dob:
        return None
    age = on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))
    if age < 18:
        return "under-18"
    for low, high, label in AGE_BANDS:
        if low <= age <= high:
            return label
    return None


def canton_code(location: str | None) -> str | None:
    if not location:
        return None
    return location if location in LOCATION_CODES else "other"


def _local_date(value: datetime | None) -> date | None:
    return timezone.localtime(value).date() if value else None


def _day(value: datetime | None) -> str | None:
    local = _local_date(value)
    return local.isoformat() if local else None


def _bucket(day: date, grain: str) -> str:
    if grain == "month":
        return day.strftime("%Y-%m")
    return (day - timedelta(days=day.weekday())).isoformat()


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(start, time.min), tz),
        timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz),
    )


def _euros(value) -> float:
    return round(float(value or Decimal("0")), 2)


def _cents_to_euros(cents) -> float:
    return round((cents or 0) / 100, 2)


def _test_account_user_ids() -> set[int]:
    """Test/QA accounts, identified by email domain or seeded username.

    Deliberately runs on ``default``: the analytics role holds no grant on
    ``auth_user.email`` / ``username``, and these identifiers must never enter
    the analytics connection. Only the resulting ids are used, as an exclusion
    list, and they never leave the process.
    """
    from crush_lu.signals import TEST_EMAIL_DOMAINS

    by_domain = Q()
    for domain in TEST_EMAIL_DOMAINS:
        by_domain |= Q(email__iendswith=f"@{domain}")
    return set(
        User.objects.using("default")
        .filter(by_domain | _TEST_USERNAME)
        .order_by()
        .values_list("id", flat=True)
    )


def _with_profile(alias: str):
    """Users with a CrushProfile, as a subquery: the first half of the
    real-member rule. Guests may register for unrestricted events without a
    profile (views_events.event_register); they are not members."""
    return CrushProfile.objects.using(alias).order_by().values("user_id")


def excluded_user_ids(alias: str) -> set[int]:
    staff = set(
        User.objects.using(alias)
        .filter(Q(is_staff=True) | Q(is_superuser=True))
        .order_by()
        .values_list("id", flat=True)
    )
    banned = set(
        UserDataConsent.objects.using(alias)
        .filter(crushlu_banned=True)
        .order_by()
        .values_list("user_id", flat=True)
    )
    return staff | banned | _test_account_user_ids()


def _luxid_user_ids(alias: str, user_ids) -> set[int]:
    """Mirror of ``CrushProfile.luxid_account_querysets`` on the analytics alias."""
    from allauth.socialaccount.models import SocialAccount, SocialToken

    user_ids = list(user_ids)
    if not user_ids:
        return set()
    native = set(
        SocialAccount.objects.using(alias)
        .filter(user_id__in=user_ids, provider="luxid")
        .order_by()
        .values_list("user_id", flat=True)
    )
    oidc = set(
        SocialToken.objects.using(alias)
        .filter(
            account__user_id__in=user_ids,
            account__provider="openid_connect",
            app__provider="openid_connect",
            app__provider_id="luxid",
        )
        .order_by()
        .values_list("account__user_id", flat=True)
    )
    return native | oidc


def _real_member_profiles(alias: str, excluded: set[int], **filters) -> list[dict]:
    return list(
        CrushProfile.objects.using(alias)
        .filter(**filters)
        .exclude(user_id__in=excluded)
        .order_by("created_at", "id")
        .values(
            "id",
            "user_id",
            "gender",
            "date_of_birth",
            "location",
            "verification_status",
            "verification_method",
            "completion_status",
            "phone_verified",
            "created_at",
        )
    )


def _generalized_quasi_identifiers(profiles: list[dict]) -> dict[int, dict]:
    """k-anonymity generalization of gender / age band / canton for member rows.

    Pseudonymized rows would otherwise defeat the demographics suppression: a
    filter such as gender=F & age_band=18-24 & canton=canton-vianden would
    count, and list, a cell that demographics() hides. So every member-level
    output (members, event_detail) carries these three values generalized over
    the WHOLE real-member population, never a filtered subset, so a member
    always generalizes the same way. Member filters match the generalized
    values.

    Generalization is top-down (gender, then age band within a gender, then
    canton within a gender+age group). At each level, groups smaller than
    SUPPRESSION_FLOOR are pooled into that level's "suppressed" bucket; if the
    bucket itself would end up non-empty but under the floor, the smallest
    retained sibling groups are folded in until it reaches the floor. So every
    distinct output (gender, age_band, canton) tuple covers at least
    SUPPRESSION_FLOOR members: a lone member can never be the only one whose
    canton reads "suppressed" next to a detailed sibling cell.
    """
    today = timezone.localdate()
    floor = SUPPRESSION_FLOOR
    # Missing values are a real, filterable group: "unknown", never null.
    raw = {
        p["user_id"]: (
            p["gender"] or UNKNOWN,
            age_band(p["date_of_birth"], today) or UNKNOWN,
            canton_code(p["location"]) or UNKNOWN,
        )
        for p in profiles
    }

    def split(uids, level):
        groups = defaultdict(list)
        for uid in uids:
            groups[raw[uid][level]].append(uid)
        retained = {k: v for k, v in groups.items() if len(v) >= floor}
        bucket = [uid for k, v in groups.items() if len(v) < floor for uid in v]
        # Fold the smallest retained siblings into a non-empty, sub-floor bucket.
        # Keys are compared as strings so None (unknown) sorts deterministically.
        while bucket and len(bucket) < floor and retained:
            smallest = min(retained, key=lambda k: (len(retained[k]), str(k)))
            bucket.extend(retained.pop(smallest))
        return retained, bucket

    generalized = {}
    genders, top_bucket = split(list(raw), 0)
    for uid in top_bucket:
        generalized[uid] = dict.fromkeys(DEMOGRAPHIC_DIMENSIONS, SUPPRESSED)
    for gender, gender_uids in genders.items():
        bands, gender_bucket = split(gender_uids, 1)
        for uid in gender_bucket:
            generalized[uid] = {
                "gender": gender,
                "age_band": SUPPRESSED,
                "canton": SUPPRESSED,
            }
        for band, band_uids in bands.items():
            cantons, band_bucket = split(band_uids, 2)
            for uid in band_bucket:
                generalized[uid] = {
                    "gender": gender,
                    "age_band": band,
                    "canton": SUPPRESSED,
                }
            for canton, canton_uids in cantons.items():
                for uid in canton_uids:
                    generalized[uid] = {
                        "gender": gender,
                        "age_band": band,
                        "canton": canton,
                    }
    return generalized


def _is_panel_audit_row(row: dict) -> bool:
    """A coach-panel verification's audit-trail row, not a member submission.

    views_coach._record_panel_verification captures `now` before its checks
    and writes it as reviewed_at on an approved, coach-set row created later,
    so submitted_at (auto_now_add at insert) is never earlier than reviewed_at.
    A real submission is reviewed after it was submitted (a revision re-submit
    resets submitted_at but also reopens the row as pending). The direction of
    the two timestamps, not their distance, marks the audit row, without
    reading its free-text notes.
    """
    return (
        row["status"] == "approved"
        and row["coach_id"] is not None
        and row["reviewed_at"] is not None
        and row["submitted_at"] is not None
        and row["reviewed_at"] <= row["submitted_at"]
    )


def _attended_user_ids(alias: str, user_ids) -> set[int]:
    return set(
        EventRegistration.objects.using(alias)
        .filter(_ATTENDED, user_id__in=list(user_ids))
        .order_by()
        .values_list("user_id", flat=True)
    )


def _paid_event_user_ids(alias: str, user_ids) -> set[int]:
    return set(
        PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(user_id__in=list(user_ids))
        .order_by()
        .values_list("user_id", flat=True)
    )


def _flag_user_ids(model, alias: str, user_ids, **filters) -> set[int]:
    return set(
        model.objects.using(alias)
        .filter(user_id__in=list(user_ids), **filters)
        .order_by()
        .values_list("user_id", flat=True)
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def definitions() -> dict:
    return {
        "purpose": (
            "Read-only, pseudonymized Crush.lu production analytics (database "
            "pythonapp, production only). Members appear as 16-hex pseudonyms "
            "that are stable across calls; they cannot be mapped back to a person."
        ),
        "real_member_rule": REAL_MEMBER_RULE,
        "timezone": "Dates and buckets use Europe/Luxembourg; weeks start on Monday.",
        # Every value demographics() can report for age_band: the adult bands,
        # minors, and members without a (usable) birth date.
        "age_bands": [label for _, _, label in AGE_BANDS] + ["under-18", UNKNOWN],
        "privacy_model": (
            "Pseudonymized, not anonymous (decision D2 in the spec): member-level rows "
            "are personal data. The controls target linkage through the classical "
            "quasi-identifiers an outsider could know, gender, age band and canton; "
            "the other row fields (verification status, LuxID, activity, dates) are the "
            "analytic payload and are visible per pseudonym by design."
        ),
        "suppression": (
            f"In demographics, a cell crossing two or more of {list(DEMOGRAPHIC_DIMENSIONS)} "
            f"with fewer than {SUPPRESSION_FLOOR} members is left out entirely (labels "
            "included) and counted in suppressed_cells; a filtered population below the "
            "floor is withheld whole (suppressed: true). In member-level rows (members, "
            "event_detail) gender, age_band and canton are generalized over the whole "
            f"real-member population: a value reads '{SUPPRESSED}' when fewer than "
            f"{SUPPRESSION_FLOOR} members share it (canton first, then age band, then "
            "gender), and member filters match these generalized values. age_band is the "
            f"current age band. As a guardrail, a members call whose filters leave 1-"
            f"{SUPPRESSION_FLOOR - 1} matches returns no rows; it is not a guarantee, "
            "since the non-demographic fields are visible in unfiltered rows."
        ),
        "limits": {
            "max_range_days": MAX_RANGE_DAYS,
            "default_range_days": DEFAULT_RANGE_DAYS,
            "max_events": MAX_EVENTS,
            "max_member_rows": MAX_MEMBER_ROWS,
            "max_kpi_weeks": MAX_KPI_WEEKS,
            "cache_seconds": 300,
        },
        "definitions": {
            "seat_holder": f"registration status in {SEAT_HOLDING_STATUSES}",
            "checked_in": "registration status 'attended' or checked_in_at set",
            "paid_event_revenue": (
                "PaymentTransaction.paid_event_registrations(): providers sumup/manual, "
                "purpose event_registration, status paid. Crush Credit redemptions are a "
                "payment method, not new revenue, and are reported separately."
            ),
            "verified": "CrushProfile.verification_status == 'verified'",
            "seat_holders_by_gender": (
                "Seat holders per gender as member rows show it: generalized over "
                "the whole real-member population, small groups pooled as "
                "'suppressed'."
            ),
            "luxid_linked": "a LuxID social account (native or LuxID OIDC app) is connected",
            "connect_onboarded": "CrushConnectMembership.onboarded_at is set",
            "premium_active": "a PremiumMembership with status 'active'",
            "kpi_weekly": (
                "Persisted WeeklyMetricsSnapshot payloads from the Monday job; these are "
                "NOT filtered to real members, so they can differ from the other tools."
            ),
        },
        "enums": {
            "event_type": [value for value, _ in MeetupEvent.EVENT_TYPE_CHOICES],
            "event_canton": [value for value, _ in MeetupEvent.CANTON_CHOICES],
            "member_canton": sorted(LOCATION_CODES) + ["other", UNKNOWN, SUPPRESSED],
            "member_gender": ["M", "F", "NB", "O", "P", UNKNOWN, SUPPRESSED],
            "member_age_band": [label for _, _, label in AGE_BANDS]
            + ["under-18", UNKNOWN, SUPPRESSED],
            "registration_status": [
                "applied",
                "pending",
                "confirmed",
                "waitlist",
                "cancelled",
                "attended",
                "no_show",
            ],
            "verification_status": ["incomplete", "pending", "verified", "rejected"],
            # demographics() reports a member without a stored gender as
            # "unknown".
            "gender": ["M", "F", "NB", "O", "P", UNKNOWN],
        },
        "tools": {
            "definitions": "this document",
            "kpi_weekly": "weeks (1-52, default 12)",
            "funnel": "from, to (YYYY-MM-DD), grain=week|month: signup cohorts and how far they got",
            "events": "from, to, event_type?, canton? (enums.event_canton): per-event fill, gender mix, check-ins, revenue",
            "event_detail": "event_id: the event plus one pseudonymized row per registration",
            "payments": "from, to, grain: money by purpose/provider/status, credits, Premium",
            "connect": "from, to: Crush Connect onboarding, weekly sessions and requests",
            "retention": "from, to: repeat attendance of members who attended events in the window",
            "demographics": "group_by (comma list of gender,age_band,canton,verification_status,luxid), verification_status?",
            "members": "signup_from?, signup_to?, verification_status?, gender?, age_band?, canton? (enums.member_canton), luxid?, attended?, limit? (<=2000)",
        },
    }


def kpi_weekly(weeks: int = 12) -> dict:
    from crush_lu.services.weekly_kpis import compute_deltas

    alias = _alias()
    rows = list(
        WeeklyMetricsSnapshot.objects.using(alias)
        .order_by("-week_start")
        .values("week_start", "week_end", "metrics", "computed_at")[: weeks + 1]
    )
    rows.reverse()
    out = []
    for index, row in enumerate(rows):
        previous = rows[index - 1]["metrics"] if index else None
        if index == 0 and len(rows) > weeks:
            continue  # fetched only to compute the first returned week's deltas
        out.append(
            {
                "week_start": row["week_start"].isoformat(),
                "week_end": row["week_end"].isoformat(),
                "computed_at": (
                    row["computed_at"].isoformat() if row["computed_at"] else None
                ),
                "metrics": row["metrics"],
                "deltas": compute_deltas(row["metrics"], previous),
            }
        )
    return {"real_member_filtered": False, "weeks": out}


def funnel(start: date, end: date, grain: str = "week") -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    profiles = _real_member_profiles(
        alias, excluded, created_at__gte=low, created_at__lt=high
    )
    user_ids = [p["user_id"] for p in profiles]
    # Submitted = the member submitted their profile: the self-serve path sets
    # completion_status="submitted" / verification_status="pending" and writes
    # no ProfileSubmission; the paid coach path writes a row. Coach-panel audit
    # rows are not submissions, so a member verified from the panel without
    # ever submitting counts as verified but not submitted (the stages are
    # flags, not a strictly nested funnel).
    submitted = {
        p["id"]
        for p in profiles
        if p["completion_status"] == "submitted"
        or p["verification_status"] in ("pending", "rejected")
    } | {
        row["profile_id"]
        for row in ProfileSubmission.objects.using(alias)
        .filter(profile_id__in=[p["id"] for p in profiles])
        .order_by()
        .values("profile_id", "coach_id", "status", "submitted_at", "reviewed_at")
        if not _is_panel_audit_row(row)
    }
    luxid = _luxid_user_ids(alias, user_ids)
    attended = _attended_user_ids(alias, user_ids)
    paid_event = _paid_event_user_ids(alias, user_ids)
    connect = _flag_user_ids(
        CrushConnectMembership, alias, user_ids, onboarded_at__isnull=False
    )
    premium = _flag_user_ids(PremiumMembership, alias, user_ids, status="active")

    stages = (
        "signups",
        "phone_verified",
        "submitted",
        "verified",
        "luxid_linked",
        "attended_event",
        "paid_event",
        "connect_onboarded",
        "premium_active",
    )
    cohorts: dict[str, Counter] = defaultdict(Counter)
    methods: dict[str, Counter] = defaultdict(Counter)
    for p in profiles:
        key = _bucket(_local_date(p["created_at"]), grain)
        uid = p["user_id"]
        row = cohorts[key]
        row["signups"] += 1
        row["phone_verified"] += bool(p["phone_verified"])
        row["submitted"] += p["id"] in submitted
        if p["verification_status"] == "verified":
            row["verified"] += 1
            methods[key][p["verification_method"] or "unknown"] += 1
        row["luxid_linked"] += uid in luxid
        row["attended_event"] += uid in attended
        row["paid_event"] += uid in paid_event
        row["connect_onboarded"] += uid in connect
        row["premium_active"] += uid in premium

    totals = Counter()
    for row in cohorts.values():
        totals.update(row)
    return {
        "grain": grain,
        "cohort_basis": (
            "CrushProfile.created_at; stages count what the cohort has reached as of "
            "now. Stages are independent flags: a member a coach verified from the "
            "panel without submitting counts as verified but not submitted."
        ),
        "stages": list(stages),
        "cohorts": [
            {
                "cohort": key,
                **{s: cohorts[key][s] for s in stages},
                "verified_by_method": dict(methods[key]),
            }
            for key in sorted(cohorts)
        ],
        "totals": {s: totals[s] for s in stages},
    }


def events(
    start: date, end: date, event_type: str | None = None, canton: str | None = None
) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    qs = MeetupEvent.objects.using(alias).filter(date_time__gte=low, date_time__lt=high)
    if event_type:
        qs = qs.filter(event_type=event_type)
    if canton:
        qs = qs.filter(canton=canton)
    event_rows = list(
        qs.order_by("date_time", "id").values(*EVENT_FIELDS)[: MAX_EVENTS + 1]
    )
    truncated = len(event_rows) > MAX_EVENTS
    out = _summarize_events(alias, excluded, event_rows[:MAX_EVENTS])
    return {"count": len(out), "truncated": truncated, "events": out}


EVENT_FIELDS = (
    "id",
    "title",
    "event_type",
    "canton",
    "date_time",
    "registration_fee",
    "max_participants",
    "max_participants_m",
    "max_participants_f",
    "max_participants_nb",
    "reserved_premium_seats",
    "registration_mode",
    "is_published",
    "is_cancelled",
)


def _summarize_events(
    alias: str,
    excluded: set[int],
    event_rows: list[dict],
    quasi: dict[int, dict] | None = None,
) -> list:
    """Per-event stats for exactly these rows (shared by events/event_detail).

    Seat holders are counted by their GENERALIZED gender, as member rows show
    it: a raw count beside event_detail's rows (say NB: 1 next to a single
    "suppressed" row) would map the raw value back to a pseudonym.
    """
    event_ids = [e["id"] for e in event_rows]
    if quasi is None:
        quasi = _generalized_quasi_identifiers(_real_member_profiles(alias, excluded))

    registrations = list(
        EventRegistration.objects.using(alias)
        .filter(event_id__in=event_ids)
        .order_by()
        .values("event_id", "user_id", "status", "checked_in_at")
    )
    revenue = {
        row["event_id"]: row
        for row in PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(event_id__in=event_ids)
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by()
        .values("event_id")
        .annotate(n=Count("id"), total=Sum("amount"))
    }
    credits = {
        row["event_registration__event_id"]: row
        for row in CreditRedemption.objects.using(alias)
        .filter(event_registration__event_id__in=event_ids)
        .filter(event_registration__user_id__in=_with_profile(alias))
        .exclude(event_registration__user_id__in=excluded)
        .order_by()
        .values("event_registration__event_id")
        .annotate(n=Count("id"), cents=Sum("amount_cents"))
    }

    per_event: dict[int, dict] = defaultdict(
        lambda: {
            "by_status": Counter(),
            "by_gender": Counter(),
            "checked_in": 0,
            "excluded": 0,
        }
    )
    for reg in registrations:
        stats = per_event[reg["event_id"]]
        # Not in quasi: no profile, or not a real member.
        if reg["user_id"] in excluded or reg["user_id"] not in quasi:
            stats["excluded"] += 1
            continue
        stats["by_status"][reg["status"]] += 1
        if reg["status"] in SEAT_HOLDING_STATUSES:
            stats["by_gender"][quasi[reg["user_id"]]["gender"]] += 1
        if reg["status"] == "attended" or reg["checked_in_at"]:
            stats["checked_in"] += 1

    out = []
    for e in event_rows:
        stats = per_event[e["id"]]
        seat_holders = sum(stats["by_status"][s] for s in SEAT_HOLDING_STATUSES)
        capacity = e["max_participants"] or 0
        paid = revenue.get(e["id"], {})
        credit = credits.get(e["id"], {})
        out.append(
            {
                "event_id": e["id"],
                "title": e["title"],
                "event_type": e["event_type"],
                "date": _day(e["date_time"]),
                "starts_at": timezone.localtime(e["date_time"]).isoformat(),
                "canton": e["canton"] or None,
                "fee_eur": _euros(e["registration_fee"]),
                "capacity": capacity,
                "gender_caps": {
                    "M": e["max_participants_m"],
                    "F": e["max_participants_f"],
                    "NB": e["max_participants_nb"],
                },
                "reserved_premium_seats": e["reserved_premium_seats"],
                "registration_mode": e["registration_mode"],
                "is_published": e["is_published"],
                "is_cancelled": e["is_cancelled"],
                "registrations_by_status": dict(stats["by_status"]),
                "seat_holders": seat_holders,
                "seat_holders_by_gender": dict(stats["by_gender"]),
                "checked_in": stats["checked_in"],
                "no_show": stats["by_status"]["no_show"],
                "fill_pct": (
                    round(100 * seat_holders / capacity, 1) if capacity else None
                ),
                "paid_registrations": paid.get("n", 0),
                "paid_revenue_eur": _euros(paid.get("total")),
                "credit_redemptions": credit.get("n", 0),
                "credit_redeemed_eur": _cents_to_euros(credit.get("cents")),
                "excluded_account_registrations": stats["excluded"],
            }
        )
    return out


def event_detail(event_id: int) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    event = (
        MeetupEvent.objects.using(alias)
        .filter(id=event_id)
        .order_by()
        .values(*EVENT_FIELDS)
        .first()
    )
    if not event:
        raise NotFound(f"event {event_id} does not exist")
    quasi = _generalized_quasi_identifiers(_real_member_profiles(alias, excluded))
    # Built for this event alone, never searched for in a capped day list.
    summary = _summarize_events(alias, excluded, [event], quasi)[0]

    registrations = list(
        EventRegistration.objects.using(alias)
        .filter(event_id=event_id)
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by("registered_at", "id")
        .values(
            "id",
            "user_id",
            "status",
            "registered_at",
            "checked_in_at",
            "payment_confirmed",
        )
    )
    user_ids = [r["user_id"] for r in registrations]
    unknown_member = dict.fromkeys(DEMOGRAPHIC_DIMENSIONS, SUPPRESSED)
    prior = dict(
        EventRegistration.objects.using(alias)
        .filter(
            _ATTENDED, user_id__in=user_ids, event__date_time__lt=event["date_time"]
        )
        .order_by()
        .values("user_id")
        .annotate(n=Count("id"))
        .values_list("user_id", "n")
    )
    # Payment flags describe the registration's CURRENT cycle. A cancelled paid
    # seat keeps its immutable transaction and redemption while its row is
    # reused on re-signup, so the ledger alone would report a new pending cycle
    # as paid: payment_confirmed is the cycle marker (set on payment, cleared
    # on cancellation), and a redemption counts only if it follows the
    # registered_at that re-registration resets.
    redemptions: dict[int, list] = defaultdict(list)
    for row in (
        CreditRedemption.objects.using(alias)
        .filter(event_registration__event_id=event_id)
        .order_by()
        .values("event_registration_id", "redeemed_at")
    ):
        redemptions[row["event_registration_id"]].append(row["redeemed_at"])
    rows = []
    for reg in registrations:
        uid = reg["user_id"]
        member_qi = quasi.get(uid, unknown_member)
        prior_count = prior.get(uid, 0)
        rows.append(
            {
                "member": pseudonym(uid),
                "status": reg["status"],
                "registered_day": _day(reg["registered_at"]),
                "checked_in": bool(reg["status"] == "attended" or reg["checked_in_at"]),
                "paid": bool(reg["payment_confirmed"]),
                "paid_with_credit": bool(reg["payment_confirmed"])
                and any(
                    redeemed_at >= reg["registered_at"]
                    for redeemed_at in redemptions.get(reg["id"], [])
                ),
                "gender": member_qi["gender"],
                "age_band": member_qi["age_band"],
                "canton": member_qi["canton"],
                "prior_events_attended": prior_count,
                "first_event": prior_count == 0,
            }
        )
    return {"event": summary, "registrations": rows}


def payments(start: date, end: date, grain: str = "month") -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    txs = list(
        PaymentTransaction.objects.using(alias)
        .filter(created_at__gte=low, created_at__lt=high)
        .order_by()
        .values("user_id", "created_at", "provider", "status", "purpose", "amount")
    )
    groups: dict[tuple, dict] = defaultdict(
        lambda: {"count": 0, "amount": Decimal("0")}
    )
    excluded_totals = {"count": 0, "amount": Decimal("0")}
    with_profile = set(
        CrushProfile.objects.using(alias)
        .filter(user_id__in={tx["user_id"] for tx in txs if tx["user_id"]})
        .order_by()
        .values_list("user_id", flat=True)
    )
    for tx in txs:
        if tx["user_id"] in excluded or tx["user_id"] not in with_profile:
            excluded_totals["count"] += 1
            excluded_totals["amount"] += tx["amount"] or 0
            continue
        key = (
            _bucket(_local_date(tx["created_at"]), grain),
            tx["purpose"],
            tx["provider"],
            tx["status"],
        )
        groups[key]["count"] += 1
        groups[key]["amount"] += tx["amount"] or 0

    paid_revenue: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "amount": Decimal("0")}
    )
    for tx in (
        PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(
            Q(paid_at__gte=low, paid_at__lt=high)
            | Q(paid_at__isnull=True, created_at__gte=low, created_at__lt=high)
        )
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by()
        .values("paid_at", "created_at", "amount")
    ):
        key = _bucket(_local_date(tx["paid_at"] or tx["created_at"]), grain)
        paid_revenue[key]["count"] += 1
        paid_revenue[key]["amount"] += tx["amount"] or 0

    issued: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "cents": 0})
    for credit in (
        CrushCredit.objects.using(alias)
        .filter(issued_at__gte=low, issued_at__lt=high)
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by()
        .values("reason", "status", "amount_cents")
    ):
        issued[(credit["reason"], credit["status"])]["count"] += 1
        issued[(credit["reason"], credit["status"])]["cents"] += (
            credit["amount_cents"] or 0
        )
    redeemed = (
        CreditRedemption.objects.using(alias)
        .filter(redeemed_at__gte=low, redeemed_at__lt=high)
        .filter(credit__user_id__in=_with_profile(alias))
        .exclude(credit__user_id__in=excluded)
        .order_by()
        .aggregate(n=Count("id"), cents=Sum("amount_cents"))
    )
    premium_created = Counter(
        dict(
            PremiumMembership.objects.using(alias)
            .filter(created_at__gte=low, created_at__lt=high)
            .filter(user_id__in=_with_profile(alias))
            .exclude(user_id__in=excluded)
            .order_by()
            .values("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )
    )
    premium_active_now = (
        PremiumMembership.objects.using(alias)
        .filter(status="active")
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by()
        .count()
    )
    return {
        "grain": grain,
        "transactions_by_created": [
            {
                "bucket": k[0],
                "purpose": k[1],
                "provider": k[2],
                "status": k[3],
                "count": v["count"],
                "amount_eur": _euros(v["amount"]),
            }
            for k, v in sorted(groups.items())
        ],
        "paid_event_revenue_by_paid_at": [
            {"bucket": k, "count": v["count"], "amount_eur": _euros(v["amount"])}
            for k, v in sorted(paid_revenue.items())
        ],
        "credits_issued": [
            {
                "reason": k[0],
                "current_status": k[1],
                "count": v["count"],
                "amount_eur": _cents_to_euros(v["cents"]),
            }
            for k, v in sorted(issued.items())
        ],
        "credits_redeemed": {
            "count": redeemed["n"] or 0,
            "amount_eur": _cents_to_euros(redeemed["cents"]),
        },
        "premium": {
            "created_by_status": dict(premium_created),
            "active_now": premium_active_now,
        },
        "excluded_accounts": {
            "transactions": excluded_totals["count"],
            "amount_eur": _euros(excluded_totals["amount"]),
        },
    }


def connect(start: date, end: date) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    memberships = list(
        CrushConnectMembership.objects.using(alias)
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by()
        .values(
            "created_at",
            "onboarding_started_at",
            "onboarded_at",
            "onboarding_step",
            "paused_at",
            "excluded_by_coach",
        )
    )

    def in_window(value):
        return value is not None and low <= value < high

    stuck_steps = Counter(
        m["onboarding_step"]
        for m in memberships
        if m["onboarding_started_at"] and not m["onboarded_at"]
    )
    sessions = dict(
        ConnectWeekSession.objects.using(alias)
        .filter(started_at__gte=low, started_at__lt=high)
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by()
        .values("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )
    requests = list(
        ConnectWeeklyRequest.objects.using(alias)
        .filter(sent_at__gte=low, sent_at__lt=high)
        .filter(requester_id__in=_with_profile(alias))
        .exclude(requester_id__in=excluded)
        .filter(recipient_id__in=_with_profile(alias))
        .exclude(recipient_id__in=excluded)
        .order_by()
        .values("status", "sent_at", "responded_at")
    )
    by_status = Counter(r["status"] for r in requests)
    response_hours = [
        (r["responded_at"] - r["sent_at"]).total_seconds() / 3600
        for r in requests
        if r["responded_at"] and r["sent_at"]
    ]
    sent = len(requests)
    return {
        "memberships": {
            "created_in_window": sum(in_window(m["created_at"]) for m in memberships),
            "onboarding_started_in_window": sum(
                in_window(m["onboarding_started_at"]) for m in memberships
            ),
            "onboarded_in_window": sum(
                in_window(m["onboarded_at"]) for m in memberships
            ),
            "onboarded_total": sum(bool(m["onboarded_at"]) for m in memberships),
            "paused_now": sum(bool(m["paused_at"]) for m in memberships),
            "excluded_by_coach_now": sum(
                bool(m["excluded_by_coach"]) for m in memberships
            ),
            "started_not_finished_by_step": {
                str(k): v
                for k, v in sorted(
                    stuck_steps.items(), key=lambda kv: (kv[0] is None, kv[0])
                )
            },
        },
        "sessions_started_by_status": sessions,
        "requests": {
            "sent": sent,
            "by_status": dict(by_status),
            "responded": len(response_hours),
            "response_rate_pct": (
                round(100 * len(response_hours) / sent, 1) if sent else None
            ),
            "accepted_rate_pct": (
                round(100 * by_status["accepted"] / sent, 1) if sent else None
            ),
            "median_response_hours": (
                round(median(response_hours), 1) if response_hours else None
            ),
        },
    }


def retention(start: date, end: date) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    low, high = _window(start, end)
    # Bound the work by the window first: who attended in it, then only their
    # history (needed for first-timer and return-within-90-days checks).
    attendee_ids = set(
        EventRegistration.objects.using(alias)
        .filter(_ATTENDED, event__date_time__gte=low, event__date_time__lt=high)
        .filter(user_id__in=_with_profile(alias))
        .exclude(user_id__in=excluded)
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()
    )
    attended = list(
        EventRegistration.objects.using(alias)
        .filter(_ATTENDED, user_id__in=attendee_ids)
        .order_by("event__date_time")
        .values("user_id", "event__date_time")
    )
    history: dict[int, list[datetime]] = defaultdict(list)
    for row in attended:
        history[row["user_id"]].append(row["event__date_time"])

    in_window = {
        uid: [d for d in dates if low <= d < high] for uid, dates in history.items()
    }
    in_window = {uid: dates for uid, dates in in_window.items() if dates}
    distribution = Counter(
        "3+" if len(dates) >= 3 else str(len(dates)) for dates in in_window.values()
    )
    gaps = []
    for dates in in_window.values():
        # Calendar days in Europe/Luxembourg, as documented: flooring a UTC
        # timedelta loses a day across the spring daylight-saving change.
        gaps.extend(
            (_local_date(b) - _local_date(a)).days for a, b in zip(dates, dates[1:])
        )
    first_timers = sum(1 for uid in in_window if history[uid][0] >= low)
    mature_cutoff = timezone.now() - timedelta(days=90)
    mature = [
        uid
        for uid in in_window
        if low <= history[uid][0] < high and history[uid][0] <= mature_cutoff
    ]
    returned = sum(
        1
        for uid in mature
        if len(history[uid]) > 1
        and history[uid][1] - history[uid][0] <= timedelta(days=90)
    )
    return {
        "attendees": len(in_window),
        "events_attended_in_window": dict(distribution),
        "median_days_between_events": median(gaps) if gaps else None,
        "first_time_attendees": first_timers,
        "returning_attendees": len(in_window) - first_timers,
        "first_timers_returned_within_90d": {
            "eligible": len(mature),
            "returned": returned,
            "pct": round(100 * returned / len(mature), 1) if mature else None,
            "note": "only first-timers whose first event is at least 90 days old are eligible",
        },
    }


def demographics(group_by: list[str], verification_status: str | None = None) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    filters = (
        {"verification_status": verification_status} if verification_status else {}
    )
    profiles = _real_member_profiles(alias, excluded, **filters)
    luxid = (
        _luxid_user_ids(alias, [p["user_id"] for p in profiles])
        if "luxid" in group_by
        else set()
    )
    today = timezone.localdate()

    def value(p, dim):
        if dim == "gender":
            return p["gender"] or UNKNOWN
        if dim == "age_band":
            return age_band(p["date_of_birth"], today) or UNKNOWN
        if dim == "canton":
            return canton_code(p["location"]) or UNKNOWN
        if dim == "verification_status":
            return p["verification_status"]
        return p["user_id"] in luxid

    total = len(profiles)
    if 0 < total < SUPPRESSION_FLOOR:
        # A filtered population below the floor is withheld whole: its total
        # and any cell label would describe fewer than SUPPRESSION_FLOOR people.
        return {
            "group_by": group_by,
            "verification_status": verification_status,
            "total_members": None,
            "suppressed": True,
            "suppressed_cells": None,
            "cells": [],
        }
    counts = Counter(tuple(value(p, d) for d in group_by) for p in profiles)
    suppress = sum(d in DEMOGRAPHIC_DIMENSIONS for d in group_by) >= 2
    cells, suppressed = [], 0
    for key, n in sorted(counts.items(), key=lambda kv: [str(x) for x in kv[0]]):
        if suppress and n < SUPPRESSION_FLOOR:
            # Dropped entirely: even the labels of a small cell say that
            # someone with that combination exists.
            suppressed += 1
            continue
        cells.append({**dict(zip(group_by, key)), "members": n})
    return {
        "group_by": group_by,
        "verification_status": verification_status,
        "total_members": total,
        "suppressed": False,
        "suppressed_cells": suppressed,
        "cells": cells,
    }


def members(
    signup_from: date | None = None,
    signup_to: date | None = None,
    verification_status: str | None = None,
    gender: str | None = None,
    age_band_filter: str | None = None,
    canton: str | None = None,
    luxid: bool | None = None,
    attended: bool | None = None,
    limit: int = DEFAULT_MEMBER_ROWS,
) -> dict:
    alias = _alias()
    excluded = excluded_user_ids(alias)
    # Generalize over the whole population first, then filter on the
    # generalized values (see _generalized_quasi_identifiers).
    population = _real_member_profiles(alias, excluded)
    quasi = _generalized_quasi_identifiers(population)
    low = high = None
    if signup_from or signup_to:
        low, high = _window(signup_from or MIN_DATE, signup_to or timezone.localdate())

    def keep(p):
        qi = quasi[p["user_id"]]
        return (
            (low is None or low <= p["created_at"] < high)
            and (
                not verification_status
                or p["verification_status"] == verification_status
            )
            and (not gender or qi["gender"] == gender)
            and (not age_band_filter or qi["age_band"] == age_band_filter)
            and (not canton or qi["canton"] == canton)
        )

    profiles = [p for p in population if keep(p)]

    # The two remaining filters need only id sets over the matches; the
    # per-member activity below is fetched for the returned page alone, so a
    # small `limit` never loads the whole attendance/payment history.
    if luxid is not None:
        luxid_all = _luxid_user_ids(alias, [p["user_id"] for p in profiles])
        profiles = [p for p in profiles if (p["user_id"] in luxid_all) == luxid]
    if attended is not None:
        attended_all = _attended_user_ids(alias, [p["user_id"] for p in profiles])
        profiles = [p for p in profiles if (p["user_id"] in attended_all) == attended]

    total = len(profiles)
    if 0 < total < SUPPRESSION_FLOOR:
        # The floor applies to the final filtered population too: stacking
        # non-demographic filters (status, signup window, LuxID, attendance)
        # on a retained cell must not single out fewer than the floor.
        return {
            "total_matching": None,
            "returned": 0,
            "truncated": False,
            "suppressed": True,
            "members": [],
        }
    page = profiles[:limit]
    user_ids = [p["user_id"] for p in page]
    luxid_ids = _luxid_user_ids(alias, user_ids)
    attended_dates: dict[int, list[datetime]] = defaultdict(list)
    for row in (
        EventRegistration.objects.using(alias)
        .filter(_ATTENDED, user_id__in=user_ids)
        .order_by("event__date_time")
        .values("user_id", "event__date_time")
    ):
        attended_dates[row["user_id"]].append(row["event__date_time"])
    paid_counts = dict(
        PaymentTransaction.objects.using(alias)
        .paid_event_registrations()
        .filter(user_id__in=user_ids)
        .order_by()
        .values("user_id")
        .annotate(n=Count("id"))
        .values_list("user_id", "n")
    )
    premium = _flag_user_ids(PremiumMembership, alias, user_ids, status="active")
    connect_ids = _flag_user_ids(
        CrushConnectMembership, alias, user_ids, onboarded_at__isnull=False
    )

    rows = []
    for p in page:
        uid = p["user_id"]
        dates = attended_dates.get(uid, [])
        rows.append(
            {
                "member": pseudonym(uid),
                "signup_week": _bucket(_local_date(p["created_at"]), "week"),
                **quasi[uid],
                "verification_status": p["verification_status"],
                "verification_method": p["verification_method"] or None,
                "phone_verified": bool(p["phone_verified"]),
                "luxid_linked": uid in luxid_ids,
                "events_attended": len(dates),
                "first_event_day": _day(dates[0]) if dates else None,
                "last_event_day": _day(dates[-1]) if dates else None,
                "paid_event_registrations": paid_counts.get(uid, 0),
                "premium_active": uid in premium,
                "connect_onboarded": uid in connect_ids,
            }
        )
    return {
        "total_matching": total,
        "returned": len(rows),
        "truncated": total > len(rows),
        "suppressed": False,
        "members": rows,
    }
