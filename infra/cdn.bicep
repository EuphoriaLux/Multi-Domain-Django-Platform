// Azure CDN for public blob media containers
// Places edge PoPs closer to Luxembourg users for faster image loading
//
// Only public containers go through CDN:
// - crush-lu-media, vinsdelux-media, shared-media, entreprinder-media, powerup-media
//
// Private containers (crush-lu-private, vinsdelux-private) MUST NOT use CDN
// because they rely on time-limited SAS tokens for access control.

param prefix string
param location string
param tags object

@description('Storage account blob endpoint (e.g., https://xxx.blob.core.windows.net)')
param storageBlobEndpoint string

@description('Storage account hostname (e.g., xxx.blob.core.windows.net)')
param storageHostname string

// CDN Profile - Standard Microsoft tier (cheapest, ~$3-5/month for small traffic)
resource cdnProfile 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: '${prefix}-cdn'
  location: 'global'
  tags: tags
  sku: {
    name: 'Standard_Microsoft'
  }
}

// CDN Endpoint pointing to storage blob origin
resource cdnEndpoint 'Microsoft.Cdn/profiles/endpoints@2024-02-01' = {
  parent: cdnProfile
  name: '${prefix}-media'
  location: 'global'
  tags: tags
  properties: {
    originHostHeader: storageHostname
    isHttpAllowed: false   // HTTPS only
    isCompressionEnabled: true
    contentTypesToCompress: [
      'image/svg+xml'
      'application/json'
      'text/css'
      'text/plain'
      'text/html'
    ]
    origins: [
      {
        name: 'blob-storage'
        properties: {
          hostName: storageHostname
          httpsPort: 443
          originHostHeader: storageHostname
        }
      }
    ]
    // Cache images for 7 days at the edge
    deliveryPolicy: {
      rules: [
        {
          name: 'CacheImages'
          order: 1
          conditions: [
            {
              name: 'UrlFileExtension'
              parameters: {
                typeName: 'DeliveryRuleUrlFileExtensionMatchConditionParameters'
                operator: 'Equal'
                matchValues: [
                  'jpg'
                  'jpeg'
                  'png'
                  'gif'
                  'webp'
                  'svg'
                  'ico'
                ]
                transforms: [
                  'Lowercase'
                ]
              }
            }
          ]
          actions: [
            {
              name: 'CacheExpiration'
              parameters: {
                typeName: 'DeliveryRuleCacheExpirationActionParameters'
                cacheBehavior: 'Override'
                cacheType: 'All'
                cacheDuration: '7.00:00:00'  // 7 days
              }
            }
          ]
        }
      ]
    }
  }
}

output cdnEndpointHostname string = cdnEndpoint.properties.hostName
output cdnProfileName string = cdnProfile.name
