# Retail price tracker architecture

This package captures public cloud retail prices independently of customer
subscriptions. The MVP has one connector: Azure Virtual Machines in selected
European regions, requested in EUR.

`RetailPriceConnector` separates provider API pagination and provider-specific
normalization from the append-only persistence service. A later provider plugs
in by implementing `iter_pages()` and `normalize_item()` and registering its
provider identifier; the raw-page archive, daily snapshot model, dashboard
history, and change detection remain shared.

The canonical price model retains generic provider, region/location, offer,
currency, and hardware-equivalence fields. Azure Retail Prices does not expose
vCPU or memory specifications, so those fields are intentionally empty until a
separate provider catalogue source can enrich them. No AWS, GCP, Luxembourg
hosting, or other European-provider integration is implemented in this MVP.

The default region set (West Europe, North Europe, and Germany West Central) is
an EU-focused comparison aid, not a claim that a particular workload or service
configuration satisfies legal, sovereignty, or contractual requirements.
