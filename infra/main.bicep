targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name which is used to generate a short unique hash for each resource')
param name string

@minLength(1)
@description('Primary location for all resources')
param location string

@secure()
@description('PostGreSQL Server administrator password')
param databasePassword string

@secure()
@description('Django SECRET_KEY for securing signed data')
param secretKey string

@description('Email for alert notifications (optional)')
param alertEmail string = ''

var resourceToken = toLower(uniqueString(subscription().id, name, location))
var tags = { 'azd-env-name': name }

resource resourceGroup 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: '${name}-rg'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: resourceGroup
  params: {
    name: name
    location: location
    resourceToken: resourceToken
    tags: tags
    databasePassword: databasePassword
    secretKey: secretKey
  }
}

// Key Vault for secure secrets management
module keyVault 'keyvault.bicep' = {
  name: 'keyvault'
  scope: resourceGroup
  params: {
    prefix: '${name}-${resourceToken}'
    location: location
    tags: tags
    databasePassword: databasePassword
    secretKey: secretKey
    appServicePrincipalId: resources.outputs.APP_SERVICE_PRINCIPAL_ID
    stagingSlotPrincipalId: resources.outputs.STAGING_SLOT_PRINCIPAL_ID
  }
}

// Azure Monitor Alerts for proactive monitoring
module alerts 'alerts.bicep' = {
  name: 'alerts'
  scope: resourceGroup
  params: {
    prefix: '${name}-${resourceToken}'
    location: location
    tags: tags
    appInsightsId: resources.outputs.APPLICATION_INSIGHTS_ID
    alertEmail: alertEmail
  }
}

output AZURE_LOCATION string = location
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.APPLICATIONINSIGHTS_CONNECTION_STRING
output WEB_URI string = resources.outputs.WEB_URI
output WEB_APP_SETTINGS array = resources.outputs.WEB_APP_SETTINGS
output WEB_APP_LOG_STREAM string = resources.outputs.WEB_APP_LOG_STREAM
output WEB_APP_SSH string = resources.outputs.WEB_APP_SSH
output WEB_APP_CONFIG string = resources.outputs.WEB_APP_CONFIG
output KEY_VAULT_NAME string = keyVault.outputs.keyVaultName
output KEY_VAULT_URI string = keyVault.outputs.keyVaultUri