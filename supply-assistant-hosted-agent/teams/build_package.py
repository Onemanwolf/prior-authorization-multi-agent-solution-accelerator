# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10"]
# ///
"""Build the Teams app package (appPackage.zip) with the Supply Assistant's own icons.

`azd ai agent pack` and `azd ai agent publish` build the package on the Microsoft 365
service with default icons, and azure.yaml has no icon field. This script produces a
package with teams/icons/color.png and outline.png instead.

The package always starts from a service-generated one and keeps its manifest: the Teams
app id, bot id and the generated sections (webApplicationInfo, copilotAgents) stay as they
are, because Microsoft says not to change generated identifiers or remove generated
sections (Teams rejects such packages). The script then replaces only the icons and the
accent color. The base is either the ZIP from Foundry portal > Publish > Download &
customize (--base), or the output of `azd ai agent pack` (run automatically without --base).

A hand-written manifest (the old "template mode") is not supported on purpose: it cannot
reproduce the generated sections.

Usage (from the agent/ folder):

    uv run teams/build_package.py                          # azd ai agent pack, then swap icons
    uv run teams/build_package.py --base path/appPackage.zip
    uv run teams/build_package.py --version 1.0.2          # override the app version

The output is teams/dist/appPackage.zip. Upload it as described in teams/README.md.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image

TEAMS = Path(__file__).resolve().parent
AGENT_ROOT = TEAMS.parent
ICONS = TEAMS / "icons"
DIST = TEAMS / "dist"
ACCENT = "#171717"

# Manifest limits: https://learn.microsoft.com/microsoft-365/extensibility/schema/root
# Version and https rules are enforced by Foundry (see its troubleshooting page).
LIMITS = {("name", "short"): 30, ("name", "full"): 100,
          ("description", "short"): 80, ("description", "full"): 4000,
          ("developer", "name"): 32}
HTTPS_FIELDS = ("websiteUrl", "privacyUrl", "termsOfUseUrl")


def fail(msg: str) -> None:
    sys.exit(f"error: {msg}")


def manifest_from_pack(base: Path | None) -> dict:
    if base is None:
        work = DIST / "pack"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        print("running: azd ai agent pack")
        res = subprocess.run(["azd", "ai", "agent", "pack", "--output-dir", str(work), "--no-prompt"],
                             cwd=AGENT_ROOT)
        if res.returncode != 0:
            fail("`azd ai agent pack` failed. It needs a bot recorded by `azd deploy`; "
                 "otherwise download the package from Foundry and pass it with --base (see teams/README.md).")
        base = work / "appPackage.zip"
    if not base.exists():
        fail(f"{base} not found")
    with zipfile.ZipFile(base) as z:
        return json.loads(z.read("manifest.json"))


def check_icons() -> None:
    color, outline = ICONS / "color.png", ICONS / "outline.png"
    for p in (color, outline):
        if not p.exists():
            fail(f"{p} missing; run `uv run teams/icons/render_icons.py --select <n>`")
    with Image.open(color) as img:
        if img.size != (192, 192):
            fail(f"color.png must be 192x192, is {img.size}")
    with Image.open(outline) as img:
        if img.size != (32, 32):
            fail(f"outline.png must be 32x32, is {img.size}")
        raw = img.convert("RGBA").tobytes()
        for i in range(0, len(raw), 4):
            r, g, b, a = raw[i:i + 4]
            if a and (r, g, b) != (255, 255, 255):
                fail("outline.png must be white on a transparent background")


def check_manifest(m: dict) -> None:
    for (section, field), limit in LIMITS.items():
        value = m.get(section, {}).get(field, "")
        if not value:
            fail(f"manifest {section}.{field} is empty")
        if len(value) > limit:
            fail(f"manifest {section}.{field} is {len(value)} chars; Teams allows {limit}")
    version = m.get("version", "")
    if not re.fullmatch(r"[1-9]\d*(\.\d+){1,2}", version):
        fail(f"manifest version {version!r}: digits and periods only, must not start with 0 (Foundry rule)")
    for field in HTTPS_FIELDS:
        if not str(m.get("developer", {}).get(field, "")).startswith("https://"):
            fail(f"manifest developer.{field} must start with https://")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", m.get("accentColor", "")):
        fail("manifest accentColor must be a #RRGGBB color")
    if not m.get("bots") or not m["bots"][0].get("botId"):
        fail("manifest has no bots[0].botId")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=Path, help="appPackage.zip from Foundry 'Download & customize' or `azd ai agent pack`")
    parser.add_argument("--version", help="override the app version in the base package")
    args = parser.parse_args()

    check_icons()
    manifest = manifest_from_pack(args.base)
    if args.version:  # otherwise keep the version the base package was generated with
        manifest["version"] = args.version
    manifest["icons"] = {"color": "color.png", "outline": "outline.png"}
    manifest["accentColor"] = ACCENT
    check_manifest(manifest)

    DIST.mkdir(exist_ok=True)
    out = DIST / "appPackage.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:  # files at the zip root
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        z.write(ICONS / "color.png", "color.png")
        z.write(ICONS / "outline.png", "outline.png")
    print(f"wrote {out}  (app id {manifest['id']}, bot id {manifest['bots'][0]['botId']}, "
          f"version {manifest['version']})")


if __name__ == "__main__":
    main()
