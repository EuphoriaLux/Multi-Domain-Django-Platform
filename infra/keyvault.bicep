// Azure Key Vault for secure secrets management
// This module creates a Key Vault with RBAC authorization and stores application secrets

param prefix string
param location string
param tags object

@secure()
@description('Database password to store in Key Vault')
param databasePassword string

@secure()
@description('Django SECRET_KEY to store in Key Vault')
param secretKey string

@description('Principal ID of the App Service to grant access')
param appServicePrincipalId string

@description('Principal ID of the Staging Slot to grant access')
param stagingSlotPrincipalId string

// Key Vault Secrets User role ID
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled' // Can be restricted with VNet integration later
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Store database password
resource databasePasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'database-password'
  properties: {
    value: databasePassword
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

// Store Django SECRET_KEY
resource secretKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'django-secret-key'
  properties: {
    value: secretKey
    contentType: 'text/plain'
    attributes: {
      enabled: true
    }
  }
}

// Grant App Service access to read secrets
resource appServiceKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, appServicePrincipalId, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: appServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Grant Staging Slot access to read secrets
resource stagingSlotKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, stagingSlotPrincipalId, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: stagingSlotPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Outputs for use in app settings
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output databasePasswordSecretUri string = databasePasswordSecret.properties.secretUri
output secretKeySecretUri string = secretKeySecret.properties.secretUri
