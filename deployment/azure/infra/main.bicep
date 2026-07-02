// =============================================================================
// SearchaaS — Azure deployment (subscription scope)
//
// Two-phase deployment to eliminate the ManagedEnvironmentNotProvisioned race:
//
//   Phase 1 — `resources` module (resources.bicep):
//     Creates RG, ACR, Log Analytics, managed identity, Container Apps
//     Environment. No Container Apps — just infra.
//
//   Phase 2 — `apps` module (apps.bicep) with dependsOn: [resources]:
//     ARM waits for Phase 1 to fully complete (environment in Succeeded state)
//     before starting this deployment. No race possible.
//
// Deploy:
//   az deployment sub create \
//     --name searchaas \
//     --location centralindia \
//     --template-file deployment/azure/infra/main.bicep \
//     --parameters deployment/azure/infra/main.parameters.json \
//     --parameters atlasUri='<...>' voyageApiKey='<...>' \
//                  openaiApiKey='<...>' mcpApiKey='<...>'
// =============================================================================

targetScope = 'subscription'

@description('Azure region for all resources.')
param location string = 'centralindia'

@description('Short name prefix used for resource naming. Lowercase alphanumeric.')
@minLength(3)
@maxLength(20)
param namePrefix string = 'searchaas'

@description('Resource group name to create.')
param resourceGroupName string = 'rg-${namePrefix}'

@description('Container image tag to deploy for all three images.')
param imageTag string = 'latest'

@description('Atlas DB name (non-secret).')
param atlasDb string = 'amazon'

@description('Optional non-secret config overrides injected as env vars (e.g. ATLAS_COLLECTION, ATLAS_VECTOR_INDEX, EMBEDDINGS_PROVIDER). Changing these needs only a redeploy + restart — no image rebuild.')
param configOverrides object = {}

@description('If true, the MCP Bearer key is embedded in the UI client-side config.js so the playground UI can call the authenticated MCP endpoint. Exposes the key to UI visitors.')
param uiEmbedMcpKey bool = false

// ---- Secrets (passed at deploy time, never committed) ----------------------
@secure()
@description('MongoDB Atlas connection string.')
param atlasUri string

@secure()
@description('Voyage AI API key (query embeddings).')
param voyageApiKey string

@secure()
@description('Google Gemini API key (optional alternate planner LLM).')
param googleApiKey string = ''

@secure()
@description('OpenAI API key (optional — only if planner provider = openai).')
param openaiApiKey string = ''

@secure()
@description('Azure OpenAI API key (planner LLM — default provider azure_openai).')
param azureOpenaiApiKey string = ''

@secure()
@description('Bearer token required by the public MCP endpoint.')
param mcpApiKey string

// ---------------------------------------------------------------------------
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

// Phase 1: infrastructure (RG, ACR, Log Analytics, identity, environment).
// No secrets required — none of these resources need Atlas/API keys.
module resources 'resources.bicep' = {
  name: 'searchaas-resources'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
  }
}

// Phase 2: the three Container Apps.
// dependsOn: [resources] makes this a separate ARM child deployment that ARM
// only starts after `searchaas-resources` fully completes. The environment
// will be in Succeeded state — the preflight race cannot happen.
module apps 'apps.bicep' = {
  name: 'searchaas-apps'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    imageTag: imageTag
    atlasDb: atlasDb
    configOverrides: configOverrides
    uiEmbedMcpKey: uiEmbedMcpKey
    // Referencing environmentReady forces ARM to wait for the polling gate in
    // resources.bicep to confirm the environment is genuinely Succeeded before
    // any container app is written. This eliminates ManagedEnvironmentNotProvisioned.
    environmentId: resources.outputs.environmentReady ? resources.outputs.environmentId : resources.outputs.environmentId
    identityId: resources.outputs.identityId
    acrServer: resources.outputs.acrLoginServer
    atlasUri: atlasUri
    voyageApiKey: voyageApiKey
    googleApiKey: googleApiKey
    openaiApiKey: openaiApiKey
    azureOpenaiApiKey: azureOpenaiApiKey
    mcpApiKey: mcpApiKey
  }
  // Explicit sequencing: ARM must fully complete `searchaas-resources` (environment
  // in Succeeded state) before starting this child deployment. The implicit dependency
  // through resources.outputs.* would also enforce this, but we keep it explicit so
  // the two-phase ordering is obvious to readers and tooling.
  #disable-next-line no-unnecessary-dependson
  dependsOn: [resources]
}

// ---- Outputs ---------------------------------------------------------------
output resourceGroup string = rg.name
output acrName string = resources.outputs.acrName
output acrLoginServer string = resources.outputs.acrLoginServer
output mcpUrl string = apps.outputs.mcpUrl
output apiUrl string = apps.outputs.apiUrl
output uiUrl string = apps.outputs.uiUrl
