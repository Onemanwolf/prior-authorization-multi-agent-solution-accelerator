// Foundry Agent Service standard setup for private mode (Spec 002 section 6.3).
// Follows Microsoft's sample 15-private-network-standard-agent-setup:
//   Cosmos DB (threads), Storage (files), AI Search (vector store), each private;
//   project connections (Entra auth) and the project capability host.
// The account capability host is created by the platform because the account
// has networkInjections (declaring a second one fails with 409).
// Capability host creation can take 30-35 minutes.

param location string
param tags object = {}
param resourceToken string

@description('Foundry account and project (created by ai-foundry.bicep)')
param accountName string
param projectName string
param projectPrincipalId string

param peSubnetId string

@description('DNS zone IDs: blob, cosmos, search')
param dnsZoneIds object

@description('AI Search SKU (standard matches the Microsoft sample)')
param searchSku string = 'standard'

var storageName = 'st${resourceToken}'
var cosmosName = 'cosmos-${resourceToken}'
var searchName = 'srch-${resourceToken}'

// Built-in roles
var cosmosDbOperatorRoleId = '230815da-be43-4aae-9cb4-875f7bd000aa'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

// -- Dependent resources (public access disabled) ----------------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    enableAutomaticFailover: false
  }
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchName
  location: location
  tags: tags
  sku: { name: searchSku }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'disabled'
    disableLocalAuth: false
    authOptions: {
      aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' }
    }
    networkRuleSet: { bypass: 'None' }
    semanticSearch: 'disabled'
  }
}

// -- Private endpoints ---------------------------------------------------------

module storagePe './private-endpoint.bicep' = {
  name: 'pe-storage'
  params: {
    name: 'pe-${storageName}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: storage.id
    groupId: 'blob'
    dnsZoneIds: [dnsZoneIds.blob]
  }
}

module cosmosPe './private-endpoint.bicep' = {
  name: 'pe-cosmos'
  params: {
    name: 'pe-${cosmosName}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: cosmos.id
    groupId: 'Sql'
    dnsZoneIds: [dnsZoneIds.cosmos]
  }
}

module searchPe './private-endpoint.bicep' = {
  name: 'pe-search'
  params: {
    name: 'pe-${searchName}'
    location: location
    tags: tags
    subnetId: peSubnetId
    targetResourceId: search.id
    groupId: 'searchService'
    dnsZoneIds: [dnsZoneIds.search]
  }
}

// -- Roles the project identity needs BEFORE the capability host --------------

resource cosmosOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cosmos.id, projectPrincipalId, cosmosDbOperatorRoleId)
  scope: cosmos
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cosmosDbOperatorRoleId)
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource storageContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, projectPrincipalId, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource searchIndexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, projectPrincipalId, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource searchServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, projectPrincipalId, searchServiceContributorRoleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// -- Project connections and capability host -----------------------------------

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

resource cosmosConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: cosmosName
  properties: {
    category: 'CosmosDB'
    target: cosmos.properties.documentEndpoint
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: cosmos.id
      location: location
    }
  }
}

resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: storageName
  properties: {
    category: 'AzureStorageAccount'
    target: storage.properties.primaryEndpoints.blob
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: storage.id
      location: location
    }
  }
}

resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: searchName
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${searchName}.search.windows.net'
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: search.id
      location: location
    }
  }
}

#disable-next-line BCP081
resource capabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: 'caphostproj'
  properties: {
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
    vectorStoreConnections: [searchConnection.name]
    storageConnections: [storageConnection.name]
    threadStorageConnections: [cosmosConnection.name]
  }
  dependsOn: [
    storagePe
    cosmosPe
    searchPe
    cosmosOperator
    storageContributor
    searchIndexContributor
    searchServiceContributor
  ]
}

// -- Roles the project identity needs AFTER the capability host ----------------

// The sample scopes this with an ABAC condition to the agent containers; the pilot
// grants it on the account (the storage account only holds agent data).
resource storageOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, projectPrincipalId, storageBlobDataOwnerRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [capabilityHost]
}

// Cosmos DB data plane: Built-in Data Contributor on the database the capability host creates.
resource cosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, projectPrincipalId, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: projectPrincipalId
    scope: '${cosmos.id}/dbs/enterprise_memory'
  }
  dependsOn: [capabilityHost, storageOwner]
}

output storageName string = storage.name
output cosmosName string = cosmos.name
output searchName string = search.name
