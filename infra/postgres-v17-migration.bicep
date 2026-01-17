// PostgreSQL 17 Migration - Temporary deployment file
// This creates a new PostgreSQL 17 server with 32 GB storage for migration
//
// Security Note: This template uses password authentication for compatibility with Django.
// For enhanced security, consider migrating to Azure AD authentication in the future.
// See: https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/how-to-configure-sign-in-azure-ad-authentication

@description('Resource Group name')
param resourceGroupName string = 'django-app-rg'

@description('Location for the PostgreSQL server')
param location string = 'westeurope'

@description('Admin username for PostgreSQL')
param adminUsername string = 'postgresadmin'

@secure()
@description('Admin password for PostgreSQL')
param adminPassword string

@description('Existing VNet name')
param vnetName string = 'django-app-ajfffwjb5ie3s-vnet'

@description('Subnet name for PostgreSQL v17')
param subnetName string = 'database-subnet-v17'

@description('Private DNS Zone name')
param privateDnsZoneName string = 'django-app-postgres-v17.private.postgres.database.azure.com'

// Reference existing VNet
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' existing = {
  name: vnetName
}

// Reference existing subnet (already created)
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' existing = {
  parent: vnet
  name: subnetName
}

// Reference existing Private DNS Zone (already created)
resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: privateDnsZoneName
}

// Create new PostgreSQL 17 Flexible Server
resource postgresServerV17 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: 'django-app-postgres-v17'
  location: location
  tags: {
    'azd-env-name': 'django-app'
    'migration': 'postgres-13-to-17'
  }
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '17'
    administratorLogin: adminUsername
    administratorLoginPassword: adminPassword
    storage: {
      storageSizeGB: 32
      autoGrow: 'Disabled'
    }
    backup: {
      backupRetentionDays: 14
      geoRedundantBackup: 'Enabled'
    }
    network: {
      delegatedSubnetResourceId: subnet.id
      privateDnsZoneArmResourceId: privateDnsZone.id
      publicNetworkAccess: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    maintenanceWindow: {
      customWindow: 'Disabled'
      dayOfWeek: 0
      startHour: 0
      startMinute: 0
    }
  }
}

// Create the pythonapp database
resource pythonappDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: postgresServerV17
  name: 'pythonapp'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

output serverName string = postgresServerV17.name
output serverFqdn string = postgresServerV17.properties.fullyQualifiedDomainName
output databaseName string = pythonappDatabase.name
