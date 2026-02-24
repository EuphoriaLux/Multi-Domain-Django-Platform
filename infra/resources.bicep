param name string
param location string
param resourceToken string
param tags object
@secure()
param databasePassword string
@secure()
param secretKey string

var prefix = '${name}-${resourceToken}'

var pgServerName = '${prefix}-postgres-server'
var databaseSubnetName = 'database-subnet'
var webappSubnetName = 'webapp-subnet'

// Azure Redis Cache
var cacheServerName = '${prefix}-redisCache'

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: '${prefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16'
      ]
    }
    subnets: [
      {
        name: databaseSubnetName
        properties: {
          addressPrefix: '10.0.0.0/24'
          delegations: [
            {
              name: '${prefix}-subnet-delegation'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
      {
        name: webappSubnetName
        properties: {
          addressPrefix: '10.0.1.0/24'
          delegations: [
            {
              name: '${prefix}-subnet-delegation-web'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
    ]
  }
  resource databaseSubnet 'subnets' existing = {
    name: databaseSubnetName
  }
  resource webappSubnet 'subnets' existing = {
    name: webappSubnetName
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: '${pgServerName}.private.postgres.database.azure.com'
  location: 'global'
  tags: tags
  dependsOn: [
    virtualNetwork
  ]
}

resource privateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: '${pgServerName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource web 'Microsoft.Web/sites@2022-03-01' = {
  name: '${prefix}-app-service'
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  kind: 'app,linux'
  properties: {
    serverFarmId: appServicePlan.id
    siteConfig: {
      alwaysOn: true
      linuxFxVersion: 'PYTHON|3.11'
      ftpsState: 'Disabled'
      appCommandLine: 'bash /home/site/wwwroot/startup.sh'
      minTlsVersion: '1.2'
      healthCheckPath: '/healthz/'
    }
    httpsOnly: true
  }
  identity: {
    type: 'SystemAssigned'
  }

  resource webAppSettings 'config' = {
    name: 'appsettings'
    properties: {
      SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
      ENABLE_ORYX_BUILD: 'true'
      // Disable Oryx output tarball caching - run directly from /home/site/wwwroot/
      // This fixes stale staticfiles manifest issues after deployment
      ORYX_DISABLE_OUTPUT_TAR_FILE: 'true'
      // Skip Oryx collectstatic - CI already builds the complete staticfiles manifest
      // This prevents Oryx from overwriting CI's manifest with an incomplete one
      DISABLE_COLLECTSTATIC: 'true'
      SECRET_KEY: secretKey
      // Production domains - marked as slot-sticky via slotConfigNames (won't swap)
      CUSTOM_DOMAINS: 'crush.lu,www.crush.lu,entreprinder.lu,www.entreprinder.lu,vinsdelux.com,www.vinsdelux.com,power-up.lu,www.power-up.lu,powerup.lu,www.powerup.lu,tableau.lu,www.tableau.lu,arborist.lu,www.arborist.lu,delegations.lu,www.delegations.lu'
      ALLOWED_HOSTS_ENV: 'crush.lu,www.crush.lu,entreprinder.lu,www.entreprinder.lu,vinsdelux.com,www.vinsdelux.com,power-up.lu,www.power-up.lu,powerup.lu,www.powerup.lu,tableau.lu,www.tableau.lu,arborist.lu,www.arborist.lu,delegations.lu,www.delegations.lu,${prefix}-app-service.azurewebsites.net'
      FLASK_DEBUG: 'False'
      // Azure Storage Account Configuration for Media Files (uses Managed Identity - no key needed)
      AZURE_ACCOUNT_NAME: storageAccount.name
      // AZURE_CONTAINER_NAME removed - all models now use platform-specific storage
      // Platform containers: crush-lu-media, crush-lu-private, vinsdelux-media, etc.
      // AZURE_ACCOUNT_KEY removed - using Managed Identity with Storage Blob Data Contributor role
      // Deployment flags - set to false for faster deployments
      // Set INITIAL_DEPLOYMENT=true manually in portal for first deployment only
      DEPLOY_MEDIA_AND_DATA: 'false'
      INITIAL_DEPLOYMENT: 'false'
      // Legacy options (kept for backward compatibility)
      SYNC_MEDIA_TO_AZURE: 'false'
      POPULATE_SAMPLE_DATA: 'false'
      // Azure Redis Cache connection string (TLS, port 6380)
      AZURE_REDIS_CONNECTIONSTRING: 'rediss://:${redisCache.listKeys().primaryKey}@${redisCache.name}.redis.cache.windows.net:6380/0'

      // =============================================================================
      // WALLET PASS CONFIGURATION (Crush.lu)
      // =============================================================================
      // NOTE: Sensitive values (private keys, passwords) should be added manually
      // in Azure Portal -> App Service -> Configuration -> Application settings
      //
      // Apple Wallet - Add these manually in Azure Portal:
      //   WALLET_APPLE_PASS_TYPE_IDENTIFIER=pass.lu.crush.member
      //   WALLET_APPLE_TEAM_IDENTIFIER=<your-apple-team-id>
      //   WALLET_APPLE_ORGANIZATION_NAME=Crush.lu
      //   WALLET_APPLE_CERT_PATH=/home/site/wwwroot/certs/pass-cert.pem
      //   WALLET_APPLE_KEY_PATH=/home/site/wwwroot/certs/pass-key.pem
      //   WALLET_APPLE_WWDR_CERT_PATH=/home/site/wwwroot/certs/AppleWWDRCA.pem
      //   WALLET_APPLE_KEY_PASSWORD=<your-key-password>
      //   WALLET_APPLE_WEB_SERVICE_URL=https://crush.lu/wallet/v1
      //   PASSKIT_APNS_KEY_ID=<your-apns-key-id>
      //   PASSKIT_APNS_TEAM_ID=<your-apple-team-id>
      //   PASSKIT_APNS_PRIVATE_KEY=<your-apns-private-key>
      //   PASSKIT_APNS_USE_SANDBOX=false
      //
      // Google Wallet - Add these manually in Azure Portal:
      //   WALLET_GOOGLE_ISSUER_ID=<your-google-issuer-id>
      //   WALLET_GOOGLE_CLASS_SUFFIX=crush-member
      //   WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL=<your-service-account>@<project>.iam.gserviceaccount.com
      //   WALLET_GOOGLE_PRIVATE_KEY=<your-private-key-with-escaped-newlines>
      //   WALLET_GOOGLE_KEY_ID=<optional-key-id>
      // =============================================================================

      // Referral points configuration (safe to include here)
      REFERRAL_POINTS_PER_SIGNUP: '100'
      REFERRAL_POINTS_PER_PROFILE_APPROVED: '50'
    }
  }

  // PostgreSQL Connection String (production database)
  resource webAppConnectionStrings 'config' = {
    name: 'connectionstrings'
    properties: {
      AZURE_POSTGRESQL_CONNECTIONSTRING: {
        value: 'dbname=${pythonAppDatabase.name} host=${postgresServer.name}.postgres.database.azure.com port=5432 sslmode=require user=${postgresServer.properties.administratorLogin} password=${databasePassword}'
        type: 'PostgreSQL'
      }
    }
  }

  resource logs 'config' = {
    name: 'logs'
    properties: {
      applicationLogs: {
        fileSystem: {
          level: 'Verbose'
        }
      }
      detailedErrorMessages: {
        enabled: true
      }
      failedRequestsTracing: {
        enabled: true
      }
      httpLogs: {
        fileSystem: {
          enabled: true
          retentionInDays: 7
          retentionInMb: 100
        }
      }
    }
  }

  resource webappVnetConfig 'networkConfig' = {
    name: 'virtualNetwork'
    properties: {
      subnetResourceId: virtualNetwork::webappSubnet.id
    }
  }

  dependsOn: [ virtualNetwork ]

}

// Staging deployment slot for zero-downtime deployments (included free with Premium tier)
resource stagingSlot 'Microsoft.Web/sites/slots@2023-12-01' = {
  parent: web
  name: 'staging'
  location: location
  tags: union(tags, { 'azd-service-name': 'web-staging' })
  kind: 'app,linux'
  properties: {
    serverFarmId: appServicePlan.id
    siteConfig: {
      alwaysOn: true
      linuxFxVersion: 'PYTHON|3.11'
      ftpsState: 'Disabled'
      appCommandLine: 'bash /home/site/wwwroot/startup.sh'
      minTlsVersion: '1.2'
      healthCheckPath: '/healthz/'
    }
    httpsOnly: true
  }
  identity: {
    type: 'SystemAssigned'
  }

  resource stagingAppSettings 'config' = {
    name: 'appsettings'
    properties: {
      SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
      ENABLE_ORYX_BUILD: 'true'
      // Disable Oryx output tarball caching - run directly from /home/site/wwwroot/
      // This fixes stale staticfiles manifest issues after deployment
      ORYX_DISABLE_OUTPUT_TAR_FILE: 'true'
      // Skip Oryx collectstatic - CI already builds the complete staticfiles manifest
      // This prevents Oryx from overwriting CI's manifest with an incomplete one
      DISABLE_COLLECTSTATIC: 'true'
      // ISOLATED DATABASE: Uses pythonapp_staging instead of pythonapp to prevent test data affecting production
      SECRET_KEY: secretKey
      // Staging domains (test.*) - marked as slot-sticky via slotConfigNames (won't swap)
      CUSTOM_DOMAINS: 'test.crush.lu,test.entreprinder.lu,test.vinsdelux.com,test.power-up.lu,test.powerup.lu,test.tableau.lu,test.arborist.lu,test.delegations.lu'
      ALLOWED_HOSTS_ENV: 'test.crush.lu,test.entreprinder.lu,test.vinsdelux.com,test.power-up.lu,test.powerup.lu,test.tableau.lu,test.arborist.lu,test.delegations.lu,${prefix}-app-service-staging.azurewebsites.net'
      FLASK_DEBUG: 'False'
      AZURE_ACCOUNT_NAME: storageAccount.name
      // AZURE_CONTAINER_NAME removed - all models now use platform-specific storage
      // Staging platform containers: crush-lu-media-staging, vinsdelux-media-staging, etc.
      DEPLOY_MEDIA_AND_DATA: 'false'
      INITIAL_DEPLOYMENT: 'false'
      SYNC_MEDIA_TO_AZURE: 'false'
      POPULATE_SAMPLE_DATA: 'false'
      REFERRAL_POINTS_PER_SIGNUP: '100'
      REFERRAL_POINTS_PER_PROFILE_APPROVED: '50'
      // STAGING MODE FLAG: Enables staging-specific behavior (email prefixes, analytics skip, etc.)
      STAGING_MODE: 'true'
      // NOTE: GA4_CRUSH_LU, GA4_POWERUP, GA4_ARBORIST are intentionally NOT set for staging
      // to prevent test traffic from polluting production analytics
    }
  }

  // PostgreSQL Connection String (staging database - isolated from production)
  resource stagingConnectionStrings 'config' = {
    name: 'connectionstrings'
    properties: {
      AZURE_POSTGRESQL_CONNECTIONSTRING: {
        value: 'dbname=${pythonAppStagingDatabase.name} host=${postgresServer.name}.postgres.database.azure.com port=5432 sslmode=require user=${postgresServer.properties.administratorLogin} password=${databasePassword}'
        type: 'PostgreSQL'
      }
    }
  }
}

// Slot-sticky settings configuration
// These settings won't swap when swapping deployment slots
// This ensures staging and production maintain their own isolated resources
resource slotConfigNames 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: web
  name: 'slotConfigNames'
  properties: {
    appSettingNames: [
      // Domain settings - each slot has its own domains
      'CUSTOM_DOMAINS'
      'ALLOWED_HOSTS_ENV'
      // AZURE_CONTAINER_NAME removed - platform-specific storage handles isolation
      // Staging mode flag - only set in staging slot
      'STAGING_MODE'
      // Analytics - only production should track GA4
      'GA4_CRUSH_LU'
      'GA4_POWERUP'
      'GA4_ARBORIST'
      // Application Insights settings - production only, never swap to staging
      'APPLICATIONINSIGHTS_CONNECTION_STRING'
      'APPINSIGHTS_PROFILERFEATURE_VERSION'
      'APPINSIGHTS_SNAPSHOTFEATURE_VERSION'
      'ApplicationInsightsAgent_EXTENSION_VERSION'
      'DiagnosticServices_EXTENSION_VERSION'
      'InstrumentationEngine_EXTENSION_VERSION'
      'SnapshotDebugger_EXTENSION_VERSION'
      'XDT_MicrosoftApplicationInsights_BaseExtensions'
      'XDT_MicrosoftApplicationInsights_Mode'
      'XDT_MicrosoftApplicationInsights_PreemptSdk'
    ]
    // CRITICAL: Database isolation - prevents staging database from swapping to production
    connectionStringNames: [
      'AZURE_POSTGRESQL_CONNECTIONSTRING'
    ]
  }
}

// Role assignment for staging slot to access Storage via Managed Identity
resource stagingStorageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, stagingSlot.id, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: stagingSlot.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource webdiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'AllLogs'
  scope: web
  properties: {
    workspaceId: logAnalyticsWorkspace.id
    logs: [
      {
        category: 'AppServiceConsoleLogs'
        enabled: true
      }
      {
        category: 'AppServiceAppLogs'
        enabled: true
      }
      {
        category: 'AppServiceAuditLogs'
        enabled: true
      }
      {
        category: 'AppServiceIPSecAuditLogs'
        enabled: true
      }
      {
        category: 'AppServicePlatformLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-service-plan'
  location: location
  tags: tags
  sku: {
    name: 'P0v3'
    tier: 'Premium0V3'
  }
  properties: {
    reserved: true
    zoneRedundant: false
  }
}

// Autoscale rules for handling event registration peaks
// Scales between 1-3 instances based on CPU usage
resource autoscaleSettings 'Microsoft.Insights/autoscalesettings@2022-10-01' = {
  name: '${prefix}-autoscale'
  location: location
  tags: tags
  properties: {
    enabled: true
    targetResourceUri: appServicePlan.id
    profiles: [
      {
        name: 'Default'
        capacity: {
          minimum: '1'
          maximum: '3'
          default: '1'
        }
        rules: [
          {
            // Scale OUT when CPU > 70% for 5 minutes
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: appServicePlan.id
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT5M'
              timeAggregation: 'Average'
              operator: 'GreaterThan'
              threshold: 70
            }
            scaleAction: {
              direction: 'Increase'
              type: 'ChangeCount'
              value: '1'
              cooldown: 'PT5M'
            }
          }
          {
            // Scale IN when CPU < 30% for 10 minutes
            metricTrigger: {
              metricName: 'CpuPercentage'
              metricResourceUri: appServicePlan.id
              timeGrain: 'PT1M'
              statistic: 'Average'
              timeWindow: 'PT10M'
              timeAggregation: 'Average'
              operator: 'LessThan'
              threshold: 30
            }
            scaleAction: {
              direction: 'Decrease'
              type: 'ChangeCount'
              value: '1'
              cooldown: 'PT10M'
            }
          }
        ]
      }
    ]
  }
}

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-workspace'
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    features: {
      searchVersion: 1
    }
    sku: {
      name: 'PerGB2018'
    }
  }
}

module applicationInsightsResources './appinsights.bicep' = {
  name: 'applicationinsights-resources'
  params: {
    prefix: prefix
    location: location
    tags: tags
    workspaceId: logAnalyticsWorkspace.id
  }
}

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  location: location
  tags: tags
  name: pgServerName
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '17'
    administratorLogin: 'postgresadmin'
    administratorLoginPassword: databasePassword
    storage: {
      storageSizeGB: 32  // Start small, can scale UP anytime (but cannot scale down)
    }
    backup: {
      backupRetentionDays: 14
      geoRedundantBackup: 'Enabled'
    }
    network: {
      delegatedSubnetResourceId: virtualNetwork::databaseSubnet.id
      privateDnsZoneArmResourceId: privateDnsZone.id
    }
    highAvailability: {
      mode: 'Disabled'
    }
    maintenanceWindow: {
      customWindow: 'Enabled'
      dayOfWeek: 2       // Tuesday
      startHour: 3       // 3 AM UTC (4 AM CET) - lowest traffic window
      startMinute: 0
    }
  }

  dependsOn: [
    privateDnsZoneLink
  ]
}

resource pythonAppDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2022-01-20-preview' = {
  parent: postgresServer
  name: 'pythonapp'
}

// Staging database - isolated from production to prevent test data affecting real users
resource pythonAppStagingDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2022-01-20-preview' = {
  parent: postgresServer
  name: 'pythonapp_staging'
}

// Azure Redis Cache - Basic C0 tier (~$16/month)
// Sub-millisecond cache for rate limiting, sessions, and cached queries
resource redisCache 'Microsoft.Cache/redis@2023-04-01' = {
  location: location
  name: cacheServerName
  tags: tags
  properties: {
    sku: {
      capacity: 0       // C0 = 250MB (sufficient for cache + sessions)
      family: 'C'
      name: 'Basic'     // Basic tier (no replication needed for cache)
    }
    enableNonSslPort: false
    redisVersion: '6'
    publicNetworkAccess: 'Enabled'  // Access via firewall rules (Basic tier doesn't support Private Endpoint)
    minimumTlsVersion: '1.2'
  }
}

// Azure Storage Account for Media Files
var storageAccountName = '${toLower('media')}${uniqueString(resourceGroup().id)}' // Use a fixed prefix and uniqueString
// Legacy container variables removed - now using platform-specific containers
// (crush-lu-media, vinsdelux-media, entreprinder-media, etc.)
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe' // Storage Blob Data Contributor

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    allowSharedKeyAccess: true // Required during transition to Managed Identity
  }

  resource blobServices 'blobServices' = {
    name: 'default'
    // Legacy 'media' and 'media-staging' containers removed
    // Platform-specific containers are created manually or via migration scripts
    // Examples: crush-lu-media, crush-lu-private, vinsdelux-media, etc.
  }
}

// Role assignment for App Service to access Storage via Managed Identity
resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, web.id, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: web.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output WEB_URI string = 'https://${web.properties.defaultHostName}'
output APPLICATIONINSIGHTS_CONNECTION_STRING string = applicationInsightsResources.outputs.APPLICATIONINSIGHTS_CONNECTION_STRING

// Azure Storage Account Outputs
output AZURE_STORAGE_ACCOUNT_NAME string = storageAccount.name
// AZURE_STORAGE_CONTAINER_NAME output removed - platform-specific containers in use
output AZURE_STORAGE_BLOB_ENDPOINT string = storageAccount.properties.primaryEndpoints.blob

var webAppSettingsKeys = map(items(web::webAppSettings.properties), setting => setting.key)
output WEB_APP_SETTINGS array = webAppSettingsKeys
output WEB_APP_LOG_STREAM string = format('https://portal.azure.com/#@/resource{0}/logStream', web.id)
output WEB_APP_SSH string = format('https://{0}.scm.azurewebsites.net/webssh/host', web.name)
output WEB_APP_CONFIG string = format('https://portal.azure.com/#@/resource{0}/configuration', web.id)

// Outputs for Key Vault and Alerts modules
output APP_SERVICE_PRINCIPAL_ID string = web.identity.principalId
output STAGING_SLOT_PRINCIPAL_ID string = stagingSlot.identity.principalId
output APPLICATION_INSIGHTS_ID string = applicationInsightsResources.outputs.APPLICATION_INSIGHTS_ID
