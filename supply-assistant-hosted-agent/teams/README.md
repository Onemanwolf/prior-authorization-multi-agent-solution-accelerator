# Teams app: Supply Assistant

Teams publishing for `supply-assistant-hosted`, kept as code. All commands run from the `agent/` folder.

## What is where

| Path | Purpose |
|---|---|
| `../azure.yaml` → `agentEndpoint` | Public endpoint protocols: `responses` (Entra) and `activity` (BotServiceRbac, needed by Teams). The platform bridges Activity to the Responses protocol that `main.py` serves. |
| `../azure.yaml` → `activity.publish` | Teams app metadata: display name, descriptions, developer, privacy and terms URLs, app version, publish scope. **Single source of truth** for these values. |
| `icons/color.png`, `icons/outline.png` | The active icon (192×192 full-bleed color, artwork inside the 120×120 safe area; 32×32 white on transparent). |
| `icons/options/<n>-<name>/` | Icon candidates: `color.svg` and `outline.svg` sources plus rendered PNGs. |
| `icons/options/preview.png` | Side-by-side sheet of all candidates (full size, Teams light and dark, outline). Send this to the client. |
| `icons/render_icons.py` | Renders the SVGs, checks the outline rules, writes the preview, and selects the active icon. |
| `build_package.py` | Takes a service-generated package, swaps in our icons and accent color, and writes `dist/appPackage.zip` (manifest + both icons at the zip root) after checking lengths, https URLs and version format. |
| `dist/` | Build output. Git-ignored. |

## Icon candidates

| # | Name | Idea |
|---|---|---|
| 1 | Hard hat | Jobsite helper: white hard hat, the yellow White Cap bar, an AI spark. Dark tile. |
| 2 | Supply chat | A supply crate inside a yellow chat bubble: "ask about supply". Dark tile. |
| 3 | Verified stack | Stacked material sheets with a check badge: "verified preferred supply". Yellow tile, stands out best in dark mode. |

Colors come from whitecap.com: yellow `#FEC526` (logo bar), near-black `#171717`, white. The icons are original artwork. They do not reuse the White Cap logo or wordmark. Check with the client's brand owner before shipping.

Option 3 is active until the client chooses. To switch:

```bash
uv run teams/icons/render_icons.py --select 3   # re-render all, make option 3 active
```

To change a design, edit its SVG and re-run the command. The outline icon must contain only white pixels on transparent, and the script fails otherwise. Microsoft's guidelines for the color icon: perfect square with full bleed (Teams rounds the corners), no border, no rounded corners, flat background, key artwork inside the 120×120 center, contrast of at least 4.5:1. See [Teams app icon guidelines](https://learn.microsoft.com/microsoftteams/platform/concepts/design/design-teams-app-icon-store-appbar).

## Publish paths

### A. azd only (default icons)

`azd ai agent publish` builds the package on the Microsoft 365 service from `activity.publish`. It always uses **default icons**, because `azure.yaml` has no icon field.

```bash
azd deploy                                   # records the Teams bot in the azd env (AGENT_<SERVICE>_BOT_NAME)
azd ai agent publish                         # scope from activity.publish.publishScope (now shared)
azd ai agent publish --scope shared          # or: shareable link, no admin approval
```

### B. Custom icons via the Foundry portal (recommended, no Teams admin needed)

The Foundry publish form has no icon field, but it can download the package it generates. That package has the real app id and bot binding, so uploading it updates the existing app.

1. Foundry portal → the agent → **Publish → Teams and Microsoft Copilot**. Fill in the fields from `activity.publish` in `azure.yaml`, and set **Publish version** higher than the live one.
2. **Next: Publish options → Download & customize → Download ZIP**.
3. Swap in our icons. The script keeps every generated field and replaces only the icons and the accent color:
   ```bash
   uv run teams/build_package.py --base ~/Downloads/<downloaded>.zip
   ```
4. In Teams: **Apps → Manage your apps → Upload an app**, then choose one of:
   - **Upload a custom app**: just you, available immediately.
   - **Submit an app to your org**: sends it to the Teams admin for approval. You don't need admin rights to submit.

Display-property changes (name, descriptions, URLs) without an icon change can also be made in Foundry under **Publish → Update agent Teams and Microsoft Copilot display properties**. Don't use direct publish after uploading a custom-icon package. It brings back the default icons.

### C. Custom icons from `azd ai agent pack`

Use this once `azd deploy` has recorded the Teams bot in the azd environment.

1. Build the package. The script runs `azd ai agent pack` itself and swaps in the icons:
   ```bash
   uv run teams/build_package.py
   ```
2. Optionally validate it at https://dev.teams.microsoft.com/tools/store-validation.
3. Upload `teams/dist/appPackage.zip`:
   - **Organization (tenant):** a Teams admin uses Teams Admin Center → *Teams apps* → *Manage apps*. For a new app, choose *Upload new app*. For the existing one, open it and choose *Upload file* to update.
   - **Just yourself (testing):** in Teams, *Apps* → *Manage your apps* → *Upload an app* → *Upload a custom app*.

There is no hand-written manifest path. Microsoft says to keep the generated app id, bot id and generated sections (`webApplicationInfo`, `copilotAgents`) unchanged, and Teams rejects packages where they changed. Only the icons and user-facing text may be customized.

Do not run `azd ai agent publish` after uploading a custom-icon package. It republishes with default icons.

### D. Custom icons through the Foundry REST API (not tried yet)

The Microsoft 365 publish API accepts `colorIconBase64` (192×192 PNG) and `outlineIconBase64` (32×32 PNG). Microsoft's page describes them next to the autopilot options, so check that they work for this agent before relying on it. If they do, this path publishes with our icons and no manual package editing. See [Publish by using the REST API](https://learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot-virtual-network). `POST .../microsoft365/zip` returns the same package without publishing it.

### Updating the app later

- Change agent behavior (prompt, tools, model): run `azd deploy` only. Teams users get the new version through the endpoint, and the app package does not change.
- Display text only (no icon change): Foundry → **Publish → Update agent Teams and Microsoft Copilot display properties** bumps the version for you. Microsoft does not say whether it keeps custom icons, so verify the icons afterwards.
- Change the name, descriptions or icon: edit `activity.publish` or the icon, **bump `appVersion`**, rebuild, and upload again. Foundry's publish API rejects an existing version (`version already exists`), and Teams updates need a higher version too. `appVersion` must be digits and periods and must not start with 0.

## Bot and app ids: read before the first build

The agent was first published to Teams from the Foundry portal. That publish created the Azure Bot and the Teams app. They are not visible in subscription `WC01-PAI` and are not recorded in the azd environment. Because of this:

- `azd ai agent pack` and `publish` currently fail with *no recorded Teams bot name*. Use path B (download the package from Foundry, then `--base`), which carries the real ids.
- The **first `azd deploy` with the `activity` settings in `azure.yaml` creates a new bot**, and `azd ai agent publish` then creates a **second Teams app**. Avoid this while the portal-created app is the live one.
- Never change the Teams app id. A new id means a new app in the Teams store. Keep the generated ids unchanged in the manifest.
- **Seen on 2026-11-12 (agent version 4):** `azd deploy` updated the agent, then failed at *Ensuring Azure Bot resource* with `Azure Bot MsaAppID "b3d10754-77fb-4db1-a59c-f0118d7743b6" is already in use`. That ID is the agent identity, and the portal-created bot already uses it, so azd could not create a second bot. Nothing was created: there is no bot resource in the subscription and no bot value in the azd env. The endpoint kept `activity` + `BotServiceRbac`, so Teams is unaffected. After this error, check that the new version is active, and set `AGENT_<SERVICE>_VERSION` in the azd env by hand, because azd stops before it records the version. Do not try to "fix" the error by giving azd a new bot.

## Scope: pilot on Shared, org rollout on Tenant

Version 1.0.2 (custom icons) was published on 2026-11-12 through the Foundry REST API (path D) with `publishScope: shared`. The icon fields worked: the package Foundry generated contains our exact PNGs.

- **Shared** maps to `BotServiceRbac`, needs no admin approval, and shows the agent only to the publisher under **Your agents**. The publisher shares the agent link. Each teammate also needs the **Foundry User** role on the Foundry project (use an Entra group), or they get an authorization error. This is the current pilot setup and matches `azure.yaml`.
- **Tenant** maps to `BotServiceTenant`, needs approval in the Microsoft 365 admin center, and lets anyone in the tenant call the agent. For the org rollout, set `publishScope: tenant` and `authorizationSchemes` to `Entra` + `BotServiceTenant`, then republish with a higher `appVersion`. Publishing replaces the Bot Service scheme to match the scope.
- Foundry sets the accent color itself (`#4464ee`) and enables the `personal`, `team`, `groupChat` and `copilot` scopes. `build_package.py` only changes the accent color for manual uploads.

## Share with colleagues (Shared scope)

Two things are needed for each colleague: the agent link, and permission on the Foundry project. Both are required. The link alone opens the agent but every message fails with an authorization error.

1. **Give access.** Assign the **Foundry User** role (shown as *Azure AI User* in older portal views) on the Foundry project, preferably to one Entra group for the team, so later joiners need no change:
   - Portal: Azure portal → the Foundry project → *Access control (IAM)* → *Add role assignment* → **Foundry User** → select the group.
   - CLI (needs Owner or User Access Administrator on the project):
     ```bash
     az role assignment create --assignee <group-or-user-object-id> --role "Foundry User" \
       --scope <project resource id>   # .../providers/Microsoft.CognitiveServices/accounts/foundry-aif-jvs7hw2v3w2ek/projects/proj-aif-jvs7hw2v3w2ek
     ```
   Role changes can take a few minutes to apply.
2. **Send the link.** In Teams open **Apps** → **Your agents**, open Supply Assistant and copy its link, then send it to the colleagues. The link looks like `https://teams.microsoft.com/l/app/<store app id>?source=app-profile-card&templateInstanceId=<manifest id>`. Only the publisher sees the app in the store, so everyone else needs the link. Send it exactly as copied.
3. **Colleague opens the link** and chooses *Add*, then chats in the personal chat. They must sign in with an account from the same tenant. Guest accounts cannot use it.

If the app does not show up for the publisher, wait for the store cache (about an hour) or sign out and back in. If a colleague gets an authorization error, check the role assignment first, then see Microsoft's [authorization troubleshooting](https://learn.microsoft.com/azure/foundry/agents/how-to/troubleshoot-publish-copilot-authorization). For the whole organization instead, switch to Tenant scope (see "Scope" above).

### Example: pilot share, 2026-11-12

Four pilot colleagues were given access to version 1.0.2 (Shared scope). Their accounts are in the project's tenant, and none is a guest.

1. **Check who already has the role.** Two of them already had **Foundry User** on the project, so nothing was changed for them:
   ```bash
   az role assignment list --scope <project resource id> \
     --query "[].{who:principalName,role:roleDefinitionName}" -o table
   ```
   The project resource id is `AZURE_AI_PROJECT_ID` in the azd environment (`azd env get-values`).
2. **Assign the role to the others.** Look up the object id, then assign. The object-id form needs no name lookup at assignment time:
   ```bash
   az ad user show --id <user email> --query id -o tsv
   az role assignment create --assignee-object-id <object id> --assignee-principal-type User \
     --role "Foundry User" --scope <project resource id>
   ```
   Check again with the list command above. `azd` has no role-assignment command, so this uses `az`, which shares azd's login. The signed-in account needs permission to create role assignments on the project.
3. **Send the link.** The publisher copied the Teams link from **Apps → Your agents → Supply Assistant**:
   ```
   https://teams.microsoft.com/l/app/1ef3c012-7ba0-4e29-b6e7-e724183ab93f?source=app-profile-card&templateInstanceId=272e7369-db4a-427f-a9c4-39dcde3880dc
   ```
   Two different ids appear in it. `1ef3c012-…` is the app's id in the Teams catalog (the store id). `272e7369-…` is the app `id` in the generated `manifest.json` (see the example below), so the app in the store is the one built from the 1.0.2 package. The `source=app-profile-card` part looks like a tracking parameter. It is kept because the shorter form was not tested.

   A second, optional link opens the agent in the browser in Microsoft 365 Copilot chat. It uses the `titleId` from the publish response:
   ```
   https://m365.cloud.microsoft/chat/?titleId=T_173f6ff7-d8f3-7f47-9994-ad75bd4191b0
   ```
   Microsoft documents the `chat/?titleId=` format on an Employee Self-Service page, not on the Foundry pages. It has not been confirmed to open this agent, and it does not add the app to Teams. Use the Teams link above for the team.
4. **Colleague adds and uses it.** They open the Teams link, check that the profile card shows the Supply Assistant, choose **Add**, then chat in the personal chat. Have one colleague try it first. If the first message fails with an authorization error, wait a few minutes after the role assignment and retry.

## Example: the published agent (version 1.0.2, 2026-11-12)

Everything below was captured from the real publish. Use it to compare after the next one. The `/zip` endpoint takes the same request body and returns the package without publishing it, so it is the safe way to preview.

**Request** (`POST <project endpoint>/agents/supply-assistant-hosted/microsoft365/publish?api-version=v1`; token audience `https://ai.azure.com`; values come from `activity.publish` in `azure.yaml`; no `performBotRegistration`, so the existing bot is not re-registered):

```json
{
  "agentDisplayName": "Supply Assistant",
  "publishScope": "Shared",
  "publishAsAutopilot": false,
  "appVersion": "1.0.2",
  "shortDescription": "Preferred suppliers, program equivalents and category contacts.",
  "fullDescription": "White Cap Supply Assistant answers associates' questions about preferred suppliers, program equivalents, category manager contacts and supplier program terms for the pilot categories (concrete repair, drywall). It is read-only and names the source of each answer in its reply.",
  "developerName": "White Cap",
  "developerWebsiteUrl": "https://www.whitecap.com/en-us",
  "privacyUrl": "https://www.whitecap.com/terms/privacy",
  "termsOfUseUrl": "https://www.whitecap.com/terms",
  "colorIconBase64": "<base64 of teams/icons/color.png>",
  "outlineIconBase64": "<base64 of teams/icons/outline.png>"
}
```

**Response:**

```json
{ "titleId": "T_173f6ff7-d8f3-7f47-9994-ad75bd4191b0", "teamsAppId": "205dd457-7020-4d2c-a6b5-ba12d029106d" }
```

The `/zip` preview run before it showed app id `272e7369-db4a-427f-a9c4-39dcde3880dc`, which differs from the `teamsAppId` in the publish response. The Teams link copied from the store contains `templateInstanceId=272e7369-…` and the catalog id `1ef3c012-7ba0-4e29-b6e7-e724183ab93f`, so the live app matches the package preview, and the publish response's `teamsAppId` appears to be a separate Foundry identifier (inferred from the matching id, not confirmed). Still check **Apps → Your agents** in Teams for duplicates after a publish.

**Generated `manifest.json`** (from the `/zip` preview, same body):

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.29/MicrosoftTeams.schema.json",
  "manifestVersion": "1.29",
  "version": "1.0.2",
  "id": "272e7369-db4a-427f-a9c4-39dcde3880dc",
  "developer": {
    "name": "White Cap",
    "websiteUrl": "https://www.whitecap.com/en-us",
    "privacyUrl": "https://www.whitecap.com/terms/privacy",
    "termsOfUseUrl": "https://www.whitecap.com/terms"
  },
  "name": { "short": "Supply Assistant", "full": "Supply Assistant" },
  "description": { "short": "Preferred suppliers, program equivalents and category contacts.", "full": "<same as fullDescription>" },
  "icons": { "color": "default-color-icon.png", "outline": "default-outline-icon.png" },
  "accentColor": "#4464ee",
  "validDomains": ["login.microsoftonline.com"],
  "webApplicationInfo": { "id": "b3d10754-77fb-4db1-a59c-f0118d7743b6", "resource": "api://example.com" },
  "bots": [{
    "botId": "b3d10754-77fb-4db1-a59c-f0118d7743b6",
    "scopes": ["personal", "team", "groupChat", "copilot"],
    "supportsFiles": true,
    "isNotificationOnly": false
  }],
  "supportsChannelFeatures": "tier1",
  "copilotAgents": { "customEngineAgents": [{
    "id": "b3d10754-77fb-4db1-a59c-f0118d7743b6",
    "type": "bot",
    "disclaimer": { "text": "This agent uses AI. Please verify important information." }
  }] }
}
```

Notes on what Foundry generated:
- The package contains `manifest.json`, `default-color-icon.png` and `default-outline-icon.png`. The icon files are named `default-*`, but their SHA-256 hashes match our `icons/color.png` and `icons/outline.png`.
- `name.full` is just the display name. Foundry does not add the "White Cap" prefix.
- `accentColor` is Foundry's `#4464ee`. Our `#171717` applies only to packages built with `build_package.py`.
- `webApplicationInfo.resource` is the placeholder `api://example.com`, written by Foundry. We do not control it. It has had no visible effect so far.

**Agent endpoint after publishing** (`GET <project endpoint>/agents/supply-assistant-hosted?api-version=v1` → `agent_endpoint`):

```json
{
  "version_selector": { "version_selection_rules": [{ "type": "FixedRatio", "agent_version": "@latest", "traffic_percentage": 100 }] },
  "protocols": ["activity", "responses"],
  "protocol_configuration": { "activity": {}, "responses": {} },
  "authorization_schemes": [
    { "type": "Entra", "isolation_key_source": { "kind": "Entra" } },
    { "type": "BotServiceRbac" }
  ],
  "publish_approval_status": "no_approval_needed"
}
```

## Known limits and support

From Microsoft's Foundry publishing docs:

- Published agents do not support streaming or citations. Answers must name their sources in the reply text.
- File uploads and image generation do not work in Microsoft 365 Copilot, but work in Teams.
- Guest users cannot use the agent.
- `BotServiceTenant` (tenant scope) lets anyone in the tenant call the agent. `BotServiceRbac` (individual scope) also needs Azure permissions on the Foundry project. The scope decides which scheme Foundry sets. Check that `agentEndpoint.authorizationSchemes` in `azure.yaml` matches the scope.
- Tenant scope needs approval in the Microsoft 365 admin center. The store cache refreshes about hourly.
- If a conversation gets stuck after a tool error (`no tool output found`), send `/foundry_new_preview` to the bot to start a fresh one.

## Sources

Microsoft documentation this folder was checked against (read 2026-11-12):

- Teams manifest and icons
  - [App manifest reference (root)](https://learn.microsoft.com/microsoft-365/extensibility/schema/root): field limits, `permissions`, `validDomains`, `accentColor`.
  - [App manifest `bots` object](https://learn.microsoft.com/microsoft-365/extensibility/schema/root-bots): `scopes`, `supportsFiles`, `commandLists`.
  - [Manifest schema versions](https://learn.microsoft.com/microsoftteams/platform/resources/schema/manifest-schema): latest version and supported range.
  - [Package your app](https://learn.microsoft.com/microsoftteams/platform/concepts/build-and-test/apps-package): package contents.
  - [App icon guidelines](https://learn.microsoft.com/microsoftteams/platform/concepts/design/design-teams-app-icon-store-appbar): 192×192 color icon, 120×120 safe area, no border or rounded corners, 4.5:1 contrast.
- Publishing the Foundry agent
  - [Publish agents to Microsoft Copilot and Teams](https://learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot): portal flow, scopes, Download and customize, updating a published agent.
  - [Publish by using the REST API](https://learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot-virtual-network): publish and zip endpoints, custom icon fields, scope to auth scheme mapping, limitations (no streaming or citations).
  - [Troubleshoot publishing](https://learn.microsoft.com/azure/foundry/agents/how-to/troubleshoot-publish-copilot): version and validation rules, `/foundry_new_preview`, store cache, guests.
- Sharing
  - [Agent handoff for Employee Self-Service agent](https://learn.microsoft.com/copilot/employee-self-service/agent-handoff): source of the `m365.cloud.microsoft/chat/?titleId=` link format (unverified for Foundry agents).
