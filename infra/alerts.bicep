// Azure Monitor Alerts for proactive monitoring
// Creates alerts for response time, failed requests, and availability

param prefix string
param location string
param tags object

@description('Application Insights resource ID')
param appInsightsId string

@description('Action group email for alert notifications (optional)')
param alertEmail string = ''

// Create action group for notifications (if email provided)
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (alertEmail != '') {
  name: '${prefix}-alerts-ag'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'AppAlerts'
    enabled: true
    emailReceivers: [
      {
        name: 'AdminEmail'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// Response Time Alert - triggers when average response time exceeds 5 seconds
resource responseTimeAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-response-time-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Alert when average response time exceeds 5 seconds'
    severity: 2 // Warning
    enabled: true
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighResponseTime'
          metricName: 'requests/duration'
          metricNamespace: 'microsoft.insights/components'
          operator: 'GreaterThan'
          threshold: 5000 // 5 seconds in milliseconds
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: alertEmail != '' ? [
      {
        actionGroupId: actionGroup.id
      }
    ] : []
  }
}

// Failed Requests Alert - triggers when more than 10 failed requests in 5 minutes
resource failedRequestsAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-failed-requests-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Alert when failed requests exceed threshold'
    severity: 1 // Error
    enabled: true
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighFailedRequests'
          metricName: 'requests/failed'
          metricNamespace: 'microsoft.insights/components'
          operator: 'GreaterThan'
          threshold: 10
          timeAggregation: 'Count'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: alertEmail != '' ? [
      {
        actionGroupId: actionGroup.id
      }
    ] : []
  }
}

// Server Exceptions Alert - triggers on any server exception
resource serverExceptionsAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-exceptions-alert'
  location: 'global'
  tags: tags
  properties: {
    description: 'Alert on server exceptions'
    severity: 1 // Error
    enabled: true
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'ServerExceptions'
          metricName: 'exceptions/server'
          metricNamespace: 'microsoft.insights/components'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Count'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: alertEmail != '' ? [
      {
        actionGroupId: actionGroup.id
      }
    ] : []
  }
}

// Availability Test for Crush.lu
resource crushAvailabilityTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: '${prefix}-crush-availability'
  location: location
  tags: union(tags, {
    'hidden-link:${appInsightsId}': 'Resource'
  })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: '${prefix}-crush-availability'
    Name: 'Crush.lu Availability'
    Description: 'Availability test for crush.lu'
    Enabled: true
    Frequency: 300 // 5 minutes
    Timeout: 120
    Kind: 'standard'
    RetryEnabled: true
    Locations: [
      { Id: 'emea-nl-ams-azr' }  // Amsterdam
      { Id: 'emea-gb-db3-azr' }  // Dublin
      { Id: 'emea-fr-pra-edge' } // Paris
    ]
    Request: {
      RequestUrl: 'https://crush.lu/healthz/'
      HttpVerb: 'GET'
      ParseDependentRequests: false
    }
    ValidationRules: {
      ExpectedHttpStatusCode: 200
      SSLCheck: true
      SSLCertRemainingLifetimeCheck: 7
    }
  }
}

// Availability Test for PowerUP.lu
resource powerupAvailabilityTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: '${prefix}-powerup-availability'
  location: location
  tags: union(tags, {
    'hidden-link:${appInsightsId}': 'Resource'
  })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: '${prefix}-powerup-availability'
    Name: 'PowerUP.lu Availability'
    Description: 'Availability test for powerup.lu'
    Enabled: true
    Frequency: 300
    Timeout: 120
    Kind: 'standard'
    RetryEnabled: true
    Locations: [
      { Id: 'emea-nl-ams-azr' }
      { Id: 'emea-gb-db3-azr' }
      { Id: 'emea-fr-pra-edge' }
    ]
    Request: {
      RequestUrl: 'https://powerup.lu/'
      HttpVerb: 'GET'
      ParseDependentRequests: false
    }
    ValidationRules: {
      ExpectedHttpStatusCode: 200
      SSLCheck: true
      SSLCertRemainingLifetimeCheck: 7
    }
  }
}

// Availability Test for VinsDelux.com
resource vinsdeluxAvailabilityTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: '${prefix}-vinsdelux-availability'
  location: location
  tags: union(tags, {
    'hidden-link:${appInsightsId}': 'Resource'
  })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: '${prefix}-vinsdelux-availability'
    Name: 'VinsDelux.com Availability'
    Description: 'Availability test for vinsdelux.com'
    Enabled: true
    Frequency: 300
    Timeout: 120
    Kind: 'standard'
    RetryEnabled: true
    Locations: [
      { Id: 'emea-nl-ams-azr' }
      { Id: 'emea-gb-db3-azr' }
      { Id: 'emea-fr-pra-edge' }
    ]
    Request: {
      RequestUrl: 'https://vinsdelux.com/'
      HttpVerb: 'GET'
      ParseDependentRequests: false
    }
    ValidationRules: {
      ExpectedHttpStatusCode: 200
      SSLCheck: true
      SSLCertRemainingLifetimeCheck: 7
    }
  }
}

output actionGroupId string = alertEmail != '' ? actionGroup.id : ''
