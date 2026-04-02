# Generated to ensure finops tables exist in production
# The tables should have been created by entreprinder/0004 but may be missing

from django.db import migrations


def check_and_create_tables(apps, schema_editor):
    """
    Check if finops tables exist and create them if they don't.
    Uses raw SQL to avoid Django's migration state issues.
    """
    connection = schema_editor.connection
    vendor = connection.vendor

    if vendor == 'sqlite':
        # SQLite - check using sqlite_master
        cursor = connection.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            AND name IN ('finops_hub_costexport', 'finops_hub_costrecord', 'finops_hub_costaggregation')
        """)
        existing_tables = {row[0] for row in cursor.fetchall()}

        # Create CostExport table if missing
        if 'finops_hub_costexport' not in existing_tables:
            cursor.execute("""
                CREATE TABLE "finops_hub_costexport" (
                    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                    "blob_path" varchar(500) NOT NULL UNIQUE,
                    "subscription_name" varchar(200) NOT NULL,
                    "subscription_id" varchar(100) NULL,
                    "billing_period_start" date NOT NULL,
                    "billing_period_end" date NOT NULL,
                    "file_size_bytes" bigint NULL,
                    "records_imported" integer NOT NULL DEFAULT 0,
                    "import_started_at" datetime NOT NULL,
                    "import_completed_at" datetime NULL,
                    "import_status" varchar(20) NOT NULL DEFAULT 'pending',
                    "error_message" text NULL,
                    "needs_subscription_id" bool NOT NULL DEFAULT 0,
                    "blob_last_modified" datetime NULL,
                    "blob_etag" varchar(100) NULL
                )
            """)

        # Create CostRecord table if missing
        if 'finops_hub_costrecord' not in existing_tables:
            cursor.execute("""
                CREATE TABLE "finops_hub_costrecord" (
                    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                    "billed_cost" decimal NOT NULL,
                    "billing_currency" varchar(10) NOT NULL,
                    "effective_cost" decimal NOT NULL DEFAULT 0,
                    "list_cost" decimal NOT NULL DEFAULT 0,
                    "billing_period_start" date NOT NULL,
                    "billing_period_end" date NOT NULL,
                    "charge_period_start" datetime NOT NULL,
                    "charge_period_end" datetime NOT NULL,
                    "billing_account_id" varchar(200) NOT NULL,
                    "billing_account_name" varchar(200) NULL,
                    "sub_account_id" varchar(200) NOT NULL,
                    "sub_account_name" varchar(200) NOT NULL,
                    "resource_id" text NOT NULL,
                    "resource_name" varchar(300) NULL,
                    "resource_type" varchar(200) NULL,
                    "resource_group_name" varchar(200) NULL,
                    "service_name" varchar(200) NOT NULL,
                    "service_category" varchar(100) NULL,
                    "provider_name" varchar(100) NOT NULL DEFAULT 'Microsoft',
                    "region_id" varchar(100) NULL,
                    "region_name" varchar(100) NULL,
                    "sku_id" varchar(100) NULL,
                    "sku_description" text NULL,
                    "sku_meter_category" varchar(200) NULL,
                    "sku_meter_name" varchar(200) NULL,
                    "charge_category" varchar(50) NOT NULL,
                    "charge_description" text NULL,
                    "charge_frequency" varchar(50) NULL,
                    "consumed_quantity" decimal NOT NULL DEFAULT 0,
                    "consumed_unit" varchar(50) NULL,
                    "pricing_quantity" decimal NOT NULL DEFAULT 0,
                    "pricing_unit" varchar(50) NULL,
                    "tags" text NOT NULL DEFAULT '{}',
                    "extended_data" text NOT NULL DEFAULT '{}',
                    "record_hash" varchar(64) NULL UNIQUE,
                    "created_at" datetime NOT NULL,
                    "cost_export_id" bigint NOT NULL REFERENCES "finops_hub_costexport" ("id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
                )
            """)

        # Create CostAggregation table if missing
        if 'finops_hub_costaggregation' not in existing_tables:
            cursor.execute("""
                CREATE TABLE "finops_hub_costaggregation" (
                    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
                    "aggregation_type" varchar(20) NOT NULL,
                    "dimension_type" varchar(50) NOT NULL,
                    "dimension_value" varchar(300) NOT NULL,
                    "period_start" date NOT NULL,
                    "period_end" date NOT NULL,
                    "total_cost" decimal NOT NULL,
                    "currency" varchar(10) NOT NULL DEFAULT 'EUR',
                    "record_count" integer NOT NULL DEFAULT 0,
                    "usage_cost" decimal NOT NULL DEFAULT 0,
                    "purchase_cost" decimal NOT NULL DEFAULT 0,
                    "tax_cost" decimal NOT NULL DEFAULT 0,
                    "top_services" text NOT NULL DEFAULT '[]',
                    "top_resources" text NOT NULL DEFAULT '[]',
                    "created_at" datetime NOT NULL,
                    "updated_at" datetime NOT NULL,
                    UNIQUE ("aggregation_type", "dimension_type", "dimension_value", "period_start", "currency")
                )
            """)

    elif vendor == 'postgresql':
        # PostgreSQL - check using information_schema
        cursor = connection.cursor()
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('finops_hub_costexport', 'finops_hub_costrecord', 'finops_hub_costaggregation')
        """)
        existing_tables = {row[0] for row in cursor.fetchall()}

        # Create CostExport table if missing
        if 'finops_hub_costexport' not in existing_tables:
            cursor.execute("""
                CREATE TABLE "finops_hub_costexport" (
                    "id" bigserial NOT NULL PRIMARY KEY,
                    "blob_path" varchar(500) NOT NULL UNIQUE,
                    "subscription_name" varchar(200) NOT NULL,
                    "subscription_id" varchar(100) NULL,
                    "billing_period_start" date NOT NULL,
                    "billing_period_end" date NOT NULL,
                    "file_size_bytes" bigint NULL,
                    "records_imported" integer NOT NULL DEFAULT 0,
                    "import_started_at" timestamp with time zone NOT NULL,
                    "import_completed_at" timestamp with time zone NULL,
                    "import_status" varchar(20) NOT NULL DEFAULT 'pending',
                    "error_message" text NULL,
                    "needs_subscription_id" boolean NOT NULL DEFAULT false,
                    "blob_last_modified" timestamp with time zone NULL,
                    "blob_etag" varchar(100) NULL
                )
            """)
            # Create indexes for CostExport
            cursor.execute('CREATE INDEX "finops_hub__subscri_0dbb11_idx" ON "finops_hub_costexport" ("subscription_name", "billing_period_start")')
            cursor.execute('CREATE INDEX "finops_hub__import__9d31f3_idx" ON "finops_hub_costexport" ("import_status", "import_completed_at")')
            cursor.execute('CREATE INDEX "finops_export_blob_path_idx" ON "finops_hub_costexport" ("blob_path")')
            cursor.execute('CREATE INDEX "finops_export_sub_name_idx" ON "finops_hub_costexport" ("subscription_name")')
            cursor.execute('CREATE INDEX "finops_export_sub_id_idx" ON "finops_hub_costexport" ("subscription_id")')
            cursor.execute('CREATE INDEX "finops_export_period_start_idx" ON "finops_hub_costexport" ("billing_period_start")')
            cursor.execute('CREATE INDEX "finops_export_status_idx" ON "finops_hub_costexport" ("import_status")')
            cursor.execute('CREATE INDEX "finops_export_needs_sub_idx" ON "finops_hub_costexport" ("needs_subscription_id")')
            cursor.execute('CREATE INDEX "finops_export_blob_mod_idx" ON "finops_hub_costexport" ("blob_last_modified")')

        # Create CostRecord table if missing (depends on CostExport)
        if 'finops_hub_costrecord' not in existing_tables:
            cursor.execute("""
                CREATE TABLE "finops_hub_costrecord" (
                    "id" bigserial NOT NULL PRIMARY KEY,
                    "billed_cost" numeric(12, 4) NOT NULL,
                    "billing_currency" varchar(10) NOT NULL,
                    "effective_cost" numeric(12, 4) NOT NULL DEFAULT 0,
                    "list_cost" numeric(12, 4) NOT NULL DEFAULT 0,
                    "billing_period_start" date NOT NULL,
                    "billing_period_end" date NOT NULL,
                    "charge_period_start" timestamp with time zone NOT NULL,
                    "charge_period_end" timestamp with time zone NOT NULL,
                    "billing_account_id" varchar(200) NOT NULL,
                    "billing_account_name" varchar(200) NULL,
                    "sub_account_id" varchar(200) NOT NULL,
                    "sub_account_name" varchar(200) NOT NULL,
                    "resource_id" text NOT NULL,
                    "resource_name" varchar(300) NULL,
                    "resource_type" varchar(200) NULL,
                    "resource_group_name" varchar(200) NULL,
                    "service_name" varchar(200) NOT NULL,
                    "service_category" varchar(100) NULL,
                    "provider_name" varchar(100) NOT NULL DEFAULT 'Microsoft',
                    "region_id" varchar(100) NULL,
                    "region_name" varchar(100) NULL,
                    "sku_id" varchar(100) NULL,
                    "sku_description" text NULL,
                    "sku_meter_category" varchar(200) NULL,
                    "sku_meter_name" varchar(200) NULL,
                    "charge_category" varchar(50) NOT NULL,
                    "charge_description" text NULL,
                    "charge_frequency" varchar(50) NULL,
                    "consumed_quantity" numeric(20, 6) NOT NULL DEFAULT 0,
                    "consumed_unit" varchar(50) NULL,
                    "pricing_quantity" numeric(20, 6) NOT NULL DEFAULT 0,
                    "pricing_unit" varchar(50) NULL,
                    "tags" jsonb NOT NULL DEFAULT '{}',
                    "extended_data" jsonb NOT NULL DEFAULT '{}',
                    "record_hash" varchar(64) NULL UNIQUE,
                    "created_at" timestamp with time zone NOT NULL,
                    "cost_export_id" bigint NOT NULL REFERENCES "finops_hub_costexport" ("id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
                )
            """)
            # Create indexes for CostRecord
            cursor.execute('CREATE INDEX "finops_hub__billing_8853b3_idx" ON "finops_hub_costrecord" ("billing_period_start", "sub_account_name")')
            cursor.execute('CREATE INDEX "finops_hub__service_809822_idx" ON "finops_hub_costrecord" ("service_name", "billing_period_start")')
            cursor.execute('CREATE INDEX "finops_hub__resourc_1be05a_idx" ON "finops_hub_costrecord" ("resource_group_name", "billing_period_start")')
            cursor.execute('CREATE INDEX "finops_hub__charge__23fb00_idx" ON "finops_hub_costrecord" ("charge_period_start", "billed_cost")')
            cursor.execute('CREATE INDEX "finops_hub__resourc_3481d2_idx" ON "finops_hub_costrecord" ("resource_name", "service_name")')
            cursor.execute('CREATE INDEX "finops_record_billed_cost_idx" ON "finops_hub_costrecord" ("billed_cost")')
            cursor.execute('CREATE INDEX "finops_record_currency_idx" ON "finops_hub_costrecord" ("billing_currency")')
            cursor.execute('CREATE INDEX "finops_record_period_start_idx" ON "finops_hub_costrecord" ("billing_period_start")')
            cursor.execute('CREATE INDEX "finops_record_charge_start_idx" ON "finops_hub_costrecord" ("charge_period_start")')
            cursor.execute('CREATE INDEX "finops_record_account_id_idx" ON "finops_hub_costrecord" ("billing_account_id")')
            cursor.execute('CREATE INDEX "finops_record_sub_id_idx" ON "finops_hub_costrecord" ("sub_account_id")')
            cursor.execute('CREATE INDEX "finops_record_sub_name_idx" ON "finops_hub_costrecord" ("sub_account_name")')
            cursor.execute('CREATE INDEX "finops_record_resource_id_idx" ON "finops_hub_costrecord" USING hash ("resource_id")')
            cursor.execute('CREATE INDEX "finops_record_resource_name_idx" ON "finops_hub_costrecord" ("resource_name")')
            cursor.execute('CREATE INDEX "finops_record_resource_type_idx" ON "finops_hub_costrecord" ("resource_type")')
            cursor.execute('CREATE INDEX "finops_record_rg_name_idx" ON "finops_hub_costrecord" ("resource_group_name")')
            cursor.execute('CREATE INDEX "finops_record_service_idx" ON "finops_hub_costrecord" ("service_name")')
            cursor.execute('CREATE INDEX "finops_record_category_idx" ON "finops_hub_costrecord" ("service_category")')
            cursor.execute('CREATE INDEX "finops_record_region_idx" ON "finops_hub_costrecord" ("region_id")')
            cursor.execute('CREATE INDEX "finops_record_charge_cat_idx" ON "finops_hub_costrecord" ("charge_category")')
            cursor.execute('CREATE INDEX "finops_record_hash_idx" ON "finops_hub_costrecord" ("record_hash")')
            cursor.execute('CREATE INDEX "finops_record_export_idx" ON "finops_hub_costrecord" ("cost_export_id")')

        # Create CostAggregation table if missing
        if 'finops_hub_costaggregation' not in existing_tables:
            cursor.execute("""
                CREATE TABLE "finops_hub_costaggregation" (
                    "id" bigserial NOT NULL PRIMARY KEY,
                    "aggregation_type" varchar(20) NOT NULL,
                    "dimension_type" varchar(50) NOT NULL,
                    "dimension_value" varchar(300) NOT NULL,
                    "period_start" date NOT NULL,
                    "period_end" date NOT NULL,
                    "total_cost" numeric(12, 2) NOT NULL,
                    "currency" varchar(10) NOT NULL DEFAULT 'EUR',
                    "record_count" integer NOT NULL DEFAULT 0,
                    "usage_cost" numeric(12, 2) NOT NULL DEFAULT 0,
                    "purchase_cost" numeric(12, 2) NOT NULL DEFAULT 0,
                    "tax_cost" numeric(12, 2) NOT NULL DEFAULT 0,
                    "top_services" jsonb NOT NULL DEFAULT '[]',
                    "top_resources" jsonb NOT NULL DEFAULT '[]',
                    "created_at" timestamp with time zone NOT NULL,
                    "updated_at" timestamp with time zone NOT NULL,
                    UNIQUE ("aggregation_type", "dimension_type", "dimension_value", "period_start", "currency")
                )
            """)
            # Create indexes for CostAggregation
            cursor.execute('CREATE INDEX "finops_hub__aggrega_5d9e9e_idx" ON "finops_hub_costaggregation" ("aggregation_type", "period_start")')
            cursor.execute('CREATE INDEX "finops_hub__dimensi_8db775_idx" ON "finops_hub_costaggregation" ("dimension_type", "dimension_value", "period_start")')
            cursor.execute('CREATE INDEX "finops_agg_type_idx" ON "finops_hub_costaggregation" ("aggregation_type")')
            cursor.execute('CREATE INDEX "finops_agg_dim_type_idx" ON "finops_hub_costaggregation" ("dimension_type")')
            cursor.execute('CREATE INDEX "finops_agg_dim_value_idx" ON "finops_hub_costaggregation" ("dimension_value")')
            cursor.execute('CREATE INDEX "finops_agg_period_idx" ON "finops_hub_costaggregation" ("period_start")')


def no_op(apps, schema_editor):
    """Reverse operation - do nothing (tables should persist)"""
    pass


class Migration(migrations.Migration):
    """
    Ensure finops tables exist in production.

    This migration was created because the tables may have been missed during
    the migration from entreprinder to power_up apps. It safely checks if
    each table exists before creating it.
    """

    dependencies = [
        ('power_up', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(check_and_create_tables, no_op),
    ]
