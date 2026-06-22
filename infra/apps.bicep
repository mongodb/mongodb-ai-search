// =============================================================================
// SearchaaS — the three Container Apps (resource-group scope)
//
// Deployed as a MODULE that depends on the environment/infra module, so the
// Container Apps Environment is fully provisioned before any app write. This
// avoids the intermittent `ManagedEnvironmentNotProvisioned` race that occurs
// when the environment is re-evaluated (listKeys) in the same deployment pass
// as the apps.
// =============================================================================

param location string
param namePrefix string
param imageTag string
param atlasDb string
param configOverrides object = {}
param uiEmbedMcpKey bool = false

param environmentId string
param identityId string
param acrServer string

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

// Shared data secrets injected into the MCP and API apps.
var dataSecrets = [
  {
    name: 'atlas-uri'
    value: atlasUri
  }
  {
    name: 'voyage-api-key'
    value: voyageApiKey
  }
  {
    name: 'google-api-key'
    value: empty(googleApiKey) ? 'unset' : googleApiKey
  }
  {
    name: 'openai-api-key'
    value: empty(openaiApiKey) ? 'unset' : openaiApiKey
  }
  {
    name: 'azure-openai-api-key'
    value: azureOpenaiApiKey
  }
]

// Non-secret config overrides. Only non-empty entries are injected; the rest
// fall back to searchaas.yaml ${VAR:-default}. Changing them is a redeploy +
// restart — no image rebuild.
var configOverrideEnv = [for item in items(configOverrides): {
  name: item.key
  value: string(item.value)
}]

var dataEnvVars = concat([
  {
    name: 'ATLAS_URI'
    secretRef: 'atlas-uri'
  }
  {
    name: 'ATLAS_DB'
    value: atlasDb
  }
  {
    name: 'VOYAGE_API_KEY'
    secretRef: 'voyage-api-key'
  }
  {
    name: 'GOOGLE_API_KEY'
    secretRef: 'google-api-key'
  }
  {
    name: 'OPENAI_API_KEY'
    secretRef: 'openai-api-key'
  }
  {
    name: 'AZURE_OPENAI_API_KEY'
    secretRef: 'azure-openai-api-key'
  }
], configOverrideEnv)

// ---------------------------------------------------------------------------
// MCP server — external ingress on 8001, Bearer-gated (/mcp).
// min-replicas = 1 because streamable-http is session-stateful.
// ---------------------------------------------------------------------------
resource mcpApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-mcp'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8001
        transport: 'auto'
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['*']
          allowedHeaders: ['*']
        }
      }
      registries: [
        {
          server: acrServer
          identity: identityId
        }
      ]
      secrets: concat(dataSecrets, [
        {
          name: 'mcp-api-key'
          value: mcpApiKey
        }
      ])
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: '${acrServer}/searchaas-mcp:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(dataEnvVars, [
            {
              name: 'MCP_API_KEY'
              secretRef: 'mcp-api-key'
            }
            {
              name: 'ALLOWED_ORIGINS'
              value: '*'
            }
          ])
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// ---------------------------------------------------------------------------
// REST API — external ingress on 8000.
// ---------------------------------------------------------------------------
resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        corsPolicy: {
          allowedOrigins: ['*']
          allowedMethods: ['*']
          allowedHeaders: ['*']
        }
      }
      registries: [
        {
          server: acrServer
          identity: identityId
        }
      ]
      secrets: dataSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${acrServer}/searchaas-api:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: dataEnvVars
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}

// ---------------------------------------------------------------------------
// React UI — external ingress on 80 (nginx).
// ---------------------------------------------------------------------------
resource uiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-ui'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
      }
      registries: [
        {
          server: acrServer
          identity: identityId
        }
      ]
      secrets: uiEmbedMcpKey ? [
        {
          name: 'mcp-api-key'
          value: mcpApiKey
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'ui'
          image: '${acrServer}/searchaas-ui:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: concat([
            {
              name: 'FASTAPI_URL'
              value: 'https://${apiApp.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'MCP_URL'
              value: 'https://${mcpApp.properties.configuration.ingress.fqdn}/mcp'
            }
          ], uiEmbedMcpKey ? [
            {
              name: 'MCP_API_KEY'
              secretRef: 'mcp-api-key'
            }
          ] : [])
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 80
              }
              initialDelaySeconds: 5
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

output mcpUrl string = 'https://${mcpApp.properties.configuration.ingress.fqdn}/mcp'
output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
output uiUrl string = 'https://${uiApp.properties.configuration.ingress.fqdn}'
