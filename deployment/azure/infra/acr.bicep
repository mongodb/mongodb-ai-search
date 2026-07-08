// =============================================================================
// SearchaaS — bootstrap ACR only (subscription scope)
//
// Creates the resource group and Container Registry so images can be built
// and pushed BEFORE the Container Apps (which reference those images) are
// deployed via main.bicep. Names match main.bicep exactly so the same ACR is
// reused on the full deploy.
//
// Deploy:
//   az deployment sub create \
//     --name searchaas-acr \
//     --location eastus \
//     --template-file infra/acr.bicep \
//     --parameters infra/main.parameters.json
// =============================================================================

targetScope = 'subscription'

param location string = 'centralindia'

@minLength(3)
@maxLength(20)
param namePrefix string = 'searchaas'

param resourceGroupName string = 'rg-${namePrefix}'

// Unused here but accepted so the shared main.parameters.json file applies
// cleanly to this template too.
#disable-next-line no-unused-params
param imageTag string = 'latest'
#disable-next-line no-unused-params
param atlasDb string = 'amazon'
#disable-next-line no-unused-params
param uiEmbedMcpKey bool = false
#disable-next-line no-unused-params
param configOverrides object = {}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module acrModule 'acr-resource.bicep' = {
  name: 'searchaas-acr'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
  }
}

output acrName string = acrModule.outputs.acrName
output acrLoginServer string = acrModule.outputs.acrLoginServer
output resourceGroup string = rg.name
