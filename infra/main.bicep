// =============================================================================
// SearchaaS — Azure infrastructure (subscription scope)
//
// Creates the resource group, then provisions all supporting infrastructure
// and the three Container Apps (MCP, REST API, React UI) via the resources
// module.
//
// Deploy:
//   az deployment sub create \
//     --name searchaas \
//     --location eastus \
//     --template-file infra/main.bicep \
//     --parameters infra/main.parameters.json \
//     --parameters atlasUri='<...>' voyageApiKey='<...>' \
//                  googleApiKey='<...>' mcpApiKey='<...>'
// =============================================================================

targetScope = 'subscription'

@description('Azure region for all resources.')
param location string = 'eastus'

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
param azureOpenaiApiKey string

@secure()
@description('Bearer token required by the public MCP endpoint.')
param mcpApiKey string

// ---------------------------------------------------------------------------
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module resources 'resources.bicep' = {
  name: 'searchaas-resources'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    imageTag: imageTag
    atlasDb: atlasDb
    configOverrides: configOverrides
    uiEmbedMcpKey: uiEmbedMcpKey
    atlasUri: atlasUri
    voyageApiKey: voyageApiKey
    googleApiKey: googleApiKey
    openaiApiKey: openaiApiKey
    azureOpenaiApiKey: azureOpenaiApiKey
    mcpApiKey: mcpApiKey
  }
}

// ---- Outputs ---------------------------------------------------------------
output resourceGroup string = rg.name
output acrName string = resources.outputs.acrName
output acrLoginServer string = resources.outputs.acrLoginServer
output mcpUrl string = resources.outputs.mcpUrl
output apiUrl string = resources.outputs.apiUrl
output uiUrl string = resources.outputs.uiUrl
