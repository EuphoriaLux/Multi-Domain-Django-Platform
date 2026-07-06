# infra directory

Bicep templates describing the Azure infrastructure (App Service, PostgreSQL,
Redis, storage, Front Door, …). `resources.bicep` also defines the App Service
start command (`startup.sh`). Deployment is driven by GitHub Actions
(`.github/workflows/`), not by `azd`.
