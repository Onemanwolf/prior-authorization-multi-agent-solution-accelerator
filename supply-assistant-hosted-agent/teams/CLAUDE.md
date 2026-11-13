# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Scope: this folder (`agent/teams/`) is the Teams app package as code for the Supply Assistant. The parent `agent/CLAUDE.md` and `AGENTS.md` still apply (publishing rules, `azd` workflow). `README.md` here is the long-form runbook; read it before changing anything.

## Commands

Scripts are PEP 723 files with inline dependencies. Run them with `uv` from the `agent/` folder, not from `teams/`.

```bash
uv run teams/icons/render_icons.py              # render all icon options + icons/options/preview.png
uv run teams/icons/render_icons.py --select 3   # also copy option 3 to icons/color.png and outline.png
uv run teams/build_package.py --base <zip>      # swap our icons into a Foundry-downloaded package -> dist/appPackage.zip
uv run teams/build_package.py                   # same, but base comes from `azd ai agent pack`
uv run teams/build_package.py --version 1.0.3   # override the app version
```

There are no tests or linter. Validate by running the scripts: `render_icons.py` fails if the outline icon is not white on transparent, and `build_package.py` checks manifest length limits, developer name length, https URLs, accent color and the version format.

## How the pieces fit

- `../azure.yaml` → `agentEndpoint` and `activity.publish` is the **single source of truth** for Teams metadata (name, descriptions, URLs, `appVersion`, `publishScope`). `azd ai agent publish` and the Foundry REST publish body take their values from it. `build_package.py` does **not** read it: its metadata comes from the base package, so keep the two in step by publishing or downloading after every change. Do not duplicate these values elsewhere.
- `build_package.py` never writes a manifest from scratch. It always starts from a service-generated package, keeps every generated id and section (`id`, `bots`, `webApplicationInfo`, `copilotAgents`), and replaces only the icons and the accent color. A hand-written manifest is unsupported on purpose, because Teams rejects altered ids.
- `icons/options/<n>-<name>/` holds `color.svg` (192×192) and `outline.svg` (32×32, white on transparent) sources plus rendered PNGs. `icons/color.png` and `icons/outline.png` are the active icon, written only by `--select`. Edit the SVG, then re-run the render command.
- `dist/` is build output and is git-ignored. Do not commit `__pycache__/` either.
- **REST publish has no script here.** The route that published 1.0.2 is a documented `curl`/REST call (`POST .../agents/<name>/microsoft365/publish`, with `/zip` as a no-publish preview). The request body, response and generated manifest are in the "Example: the published agent" section of `README.md`. Foundry sets its own accent color (`#4464ee`) on that route, so `build_package.py`'s `#171717` only applies to manually uploaded packages.

## Pitfalls

- **Version bump:** any name, description or icon change needs a higher `activity.publish.appVersion` (digits and periods, no leading 0). Foundry rejects an existing version.
- **Do not run `azd ai agent publish` or Foundry direct publish** after a custom-icon upload. Both bring back default icons. The custom-icon route that worked (1.0.2) was the Foundry REST API with `colorIconBase64` and `outlineIconBase64`. The portal route is "Download & customize", then `--base`.
- **Bot and app ids:** the Teams app and bot were created by the Foundry portal and are not recorded in the azd environment. `azd ai agent pack` and `publish` currently fail with *no recorded Teams bot name*, and the first `azd deploy` with `activity` settings tries to create a new bot. On 2026-11-12 that step failed with `MsaAppID ... already in use` after the agent itself was updated, and it created nothing. Treat that error as expected, and record the agent version by hand. Read "Bot and app ids" in `README.md` first.
- **Scope:** pilot is `shared` with `BotServiceRbac`, and teammates need the Foundry User role. Tenant rollout changes `publishScope` and `authorizationSchemes` together (see "Scope" in `README.md`). Publishing replaces the Bot Service scheme to match the scope, so never publish with a scope you do not intend to apply. Use the `/zip` preview first.
- **Sharing:** the Teams store link is the one to send (see "Share with colleagues" in `README.md`). The `m365.cloud.microsoft/chat/?titleId=` link is unconfirmed for this agent. `az` creates the role assignments, because `azd` cannot.
- **Personal data:** the README uses placeholders for people. Keep colleagues' emails, object ids and tokens out of the repo.
- **Icons:** colors come from whitecap.com (`#FEC526`, `#171717`, white). The artwork is original and must not reuse the White Cap logo or wordmark. Keep the artwork inside the 120×120 center of the 192×192 color icon, with no rounded corners or border (Microsoft rounds and frames it). Re-render and `--select` after editing an SVG.
