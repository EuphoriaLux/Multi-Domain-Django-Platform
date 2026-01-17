// Temporary Azure Container Instance for PostgreSQL migration
// This container will have pg_dump and pg_restore tools

param location string = 'westeurope'
param vnetName string = 'django-app-ajfffwjb5ie3s-vnet'

@secure()
param dbPassword string

// Create a new subnet for the container instance
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' existing = {
  name: vnetName
}

resource containerSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' = {
  parent: vnet
  name: 'container-subnet'
  properties: {
    addressPrefix: '10.0.3.0/24'
    delegations: [
      {
        name: 'Microsoft.ContainerInstance.containerGroups'
        properties: {
          serviceName: 'Microsoft.ContainerInstance/containerGroups'
        }
      }
    ]
  }
}

// Container Instance with PostgreSQL 17 client tools
resource migrationContainer 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: 'postgres-migration'
  location: location
  properties: {
    containers: [
      {
        name: 'postgres-tools'
        properties: {
          image: 'postgres:17-alpine'
          resources: {
            requests: {
              cpu: 1
              memoryInGB: 2
            }
          }
          command: [
            '/bin/sh'
            '-c'
            'echo "Migration container ready. Running migration..."; export PGPASSWORD="$DB_PASSWORD"; echo "=== Dumping from PostgreSQL 13 ==="; pg_dump --host=django-app-ajfffwjb5ie3s-postgres-server.postgres.database.azure.com --port=5432 --username=postgresadmin --dbname=pythonapp --format=custom --file=/tmp/backup.dump --verbose; echo "=== Restoring to PostgreSQL 17 ==="; pg_restore --host=django-app-postgres-v17.postgres.database.azure.com --port=5432 --username=postgresadmin --dbname=pythonapp --verbose --no-owner /tmp/backup.dump; echo "=== Verifying migration ==="; psql --host=django-app-postgres-v17.postgres.database.azure.com --port=5432 --username=postgresadmin --dbname=pythonapp -c "SELECT count(*) as migration_count FROM django_migrations;"; echo "=== Migration complete! ==="; sleep 3600'
          ]
          environmentVariables: [
            {
              name: 'DB_PASSWORD'
              secureValue: dbPassword
            }
          ]
        }
      }
    ]
    osType: 'Linux'
    restartPolicy: 'Never'
    subnetIds: [
      {
        id: containerSubnet.id
      }
    ]
  }
}

output containerName string = migrationContainer.name
output subnetId string = containerSubnet.id
