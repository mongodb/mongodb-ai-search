// =============================================================================
// SearchaaS — supporting infrastructure (resource-group scope)
//
// Provisions:
//   - Log Analytics workspace        (Container Apps logs)
//   - Azure Container Registry        (image storage)
//   - User-assigned managed identity  (+ AcrPull role on the registry)
//   - Container Apps Environment
//
// Container Apps are NOT deployed here. They live in apps.bicep, called from
// main.bicep with `dependsOn: [resources]`. That makes apps a separate ARM
// child deployment that only starts after this one fully completes (environment
// in Succeeded state), eliminating the ManagedEnvironmentNotProvisioned race.
// =============================================================================

@description('Azure region for all resources.')
param location string

@description('Short name prefix used for resource naming.')
param namePrefix string

// A short deterministic suffix keeps globally-unique names (ACR) collision-free.
var suffix = uniqueString(resourceGroup().id)
var acrName = toLower('${namePrefix}acr${suffix}')
var logName = '${namePrefix}-logs'
var envName = '${namePrefix}-env'
var identityName = '${namePrefix}-id'

// ---------------------------------------------------------------------------
// Log Analytics
// ---------------------------------------------------------------------------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------
// Container Registry
// ---------------------------------------------------------------------------
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    // Admin user not required — apps pull via managed identity below.
    adminUserEnabled: false
  }
}

// ---------------------------------------------------------------------------
// User-assigned managed identity + AcrPull role assignment
// ---------------------------------------------------------------------------
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// Built-in AcrPull role definition id.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Container Apps Environment
// ---------------------------------------------------------------------------
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Environment readiness gate
//
// A managed environment's ARM resource reports success the moment its PUT
// returns, but the Container Apps backend keeps provisioning asynchronously
// afterwards (and re-PUTs bounce it into `Updating`). `dependsOn` alone does
// NOT wait for that, which is why apps intermittently hit
// `ManagedEnvironmentNotProvisioned`.
//
// This deployment script polls the environment until provisioningState is
// genuinely `Succeeded`. The apps module depends on its output, so ARM cannot
// start the apps until the environment is truly ready.
// ---------------------------------------------------------------------------
resource envReady 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: '${namePrefix}-env-ready'
  location: location
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    azCliVersion: '2.61.0'
    retentionInterval: 'PT1H'
    timeout: 'PT30M'
    cleanupPreference: 'OnSuccess'
    environmentVariables: [
      { name: 'RG', value: resourceGroup().name }
      { name: 'ENV_NAME', value: env.name }
    ]
    scriptContent: '''
      set -e
      for i in $(seq 1 120); do
        state=$(az resource show -g "$RG" -n "$ENV_NAME" \
          --resource-type Microsoft.App/managedEnvironments \
          --query "properties.provisioningState" -o tsv)
        echo "attempt $i: $state"
        if [ "$state" = "Succeeded" ]; then
          echo '{"ready":true}' > "$AZ_SCRIPTS_OUTPUT_PATH"
          exit 0
        fi
        if [ "$state" = "Failed" ] || [ "$state" = "Canceled" ]; then
          echo "environment provisioning $state" >&2
          exit 1
        fi
        sleep 15
      done
      echo "timed out waiting for environment" >&2
      exit 1
    '''
  }
}

// The deployment script's managed identity needs read access to poll the env.
var readerRoleId = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
resource envReadyReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(env.id, identity.id, readerRoleId)
  scope: env
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', readerRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---- Outputs (consumed by the apps module in main.bicep) -------------------
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output environmentId string = env.id
output identityId string = identity.id
// Truthy only after the environment is genuinely Succeeded. The apps module
// references this so ARM gates the apps behind real environment readiness.
output environmentReady bool = envReady.properties.outputs.ready
