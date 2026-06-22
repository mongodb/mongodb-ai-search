// =============================================================================
// SearchaaS — supporting resources + Container Apps (resource-group scope)
//
// Provisions:
//   - Log Analytics workspace        (Container Apps logs)
//   - Azure Container Registry        (image storage)
//   - User-assigned managed identity  (+ AcrPull role on the registry)
//   - Container Apps Environment
//   - searchaas-mcp  Container App    (external, :8001, /mcp, Bearer-gated)
//   - searchaas-api  Container App    (external, :8000)
//   - searchaas-ui   Container App    (external, :80)
// =============================================================================

@description('Azure region for all resources.')
param location string

@description('Short name prefix used for resource naming.')
param namePrefix string

@description('Container image tag to deploy.')
param imageTag string

@description('Atlas DB name (non-secret).')
param atlasDb string

@description('Optional non-secret config overrides injected as env vars. Empty values fall back to searchaas.yaml defaults — no image rebuild required to change them.')
param configOverrides object = {}

@description('If true, the MCP Bearer key is embedded in the UI\'s client-side config.js so the playground UI can call the authenticated MCP endpoint. NOTE: this exposes the key to anyone who can load the UI. Leave false for production; users can paste the key in the UI settings instead.')
param uiEmbedMcpKey bool = false

@secure()
param atlasUri string
@secure()
param voyageApiKey string
@secure()
param googleApiKey string = ''
@secure()
param openaiApiKey string = ''
@secure()
param azureOpenaiApiKey string
@secure()
param mcpApiKey string

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
// Apps module — deployed AFTER the environment is fully provisioned. Splitting
// the apps into their own module forces ARM to complete the environment (and
// its listKeys-driven update) before any Container App write, eliminating the
// intermittent `ManagedEnvironmentNotProvisioned` race.
// ---------------------------------------------------------------------------
module apps 'apps.bicep' = {
  name: 'searchaas-apps'
  params: {
    location: location
    namePrefix: namePrefix
    imageTag: imageTag
    atlasDb: atlasDb
    configOverrides: configOverrides
    uiEmbedMcpKey: uiEmbedMcpKey
    environmentId: env.id
    identityId: identity.id
    acrServer: acr.properties.loginServer
    atlasUri: atlasUri
    voyageApiKey: voyageApiKey
    googleApiKey: googleApiKey
    openaiApiKey: openaiApiKey
    azureOpenaiApiKey: azureOpenaiApiKey
    mcpApiKey: mcpApiKey
  }
  dependsOn: [
    env
    acrPull
  ]
}

// ---- Outputs ---------------------------------------------------------------
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output mcpUrl string = apps.outputs.mcpUrl
output apiUrl string = apps.outputs.apiUrl
output uiUrl string = apps.outputs.uiUrl
