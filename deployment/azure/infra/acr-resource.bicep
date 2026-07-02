// =============================================================================
// SearchaaS — ACR resource (resource-group scope)
// Naming MUST match resources.bicep so main.bicep reuses the same registry.
// =============================================================================

param location string
param namePrefix string

var suffix = uniqueString(resourceGroup().id)
var acrName = toLower('${namePrefix}acr${suffix}')

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
