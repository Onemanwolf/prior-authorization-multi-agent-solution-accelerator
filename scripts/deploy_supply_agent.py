"""Deploy the isolated supply agent only after the Databricks bootstrap succeeds."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SUPPLY_PROJECT = ROOT / "agents" / "supply"
SYNC_KEYS = (
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_LOCATION",
    "AZURE_TENANT_ID",
    "AZURE_RESOURCE_GROUP",
    "AZURE_CONTAINER_REGISTRY_NAME",
    "AZURE_CONTAINER_REGISTRY_ENDPOINT",
    "AZURE_CONTAINER_REGISTRY_RESOURCE_ID",
    "AI_FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_AI_PROJECT_ID",
    "AZURE_AI_PROJECT_ENDPOINT",
    "FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT_NAME",
    "DATABRICKS_HOST",
    "DATABRICKS_GENIE_SPACE_ID",
)


def deploy(
    get_value: Callable[[str], str],
    set_value: Callable[[str, str], None],
    run: Callable[[list[str], Path], bool],
) -> bool:
    set_value("SUPPLY_AGENT_DEPLOYED", "false")
    if get_value("DATABRICKS_READY").lower() != "true":
        print("Databricks is not ready; skipping the optional supply-pricing agent.")
        return False

    required = ("DATABRICKS_HOST", "DATABRICKS_GENIE_SPACE_ID")
    missing = [key for key in required if not get_value(key)]
    if missing:
        print(f"Databricks readiness is incomplete ({', '.join(missing)}); skipping the supply agent.")
        return False

    root_env = get_value("AZURE_ENV_NAME") or "dev"
    suffix = re.sub(r"[^a-zA-Z0-9_-]", "-", root_env)
    child_env = f"{suffix[:55]}-supply"
    subscription = get_value("AZURE_SUBSCRIPTION_ID")
    location = get_value("AZURE_LOCATION")
    if not subscription or not location:
        print("Azure subscription or location is missing; skipping the supply agent.")
        return False

    created = run(
        ["azd", "env", "new", child_env, "--subscription", subscription, "--location", location, "--no-prompt"],
        SUPPLY_PROJECT,
    )
    if not created:
        # An existing nested environment is expected on repeated azd up runs.
        print(f"Using existing supply-agent azd environment {child_env}.")

    for key in SYNC_KEYS:
        value = get_value(key)
        if value:
            if not run(["azd", "env", "set", key, value, "--environment", child_env], SUPPLY_PROJECT):
                print(f"Could not synchronize {key}; skipping the supply agent.")
                return False

    if not run(["azd", "deploy", "--environment", child_env, "--no-prompt"], SUPPLY_PROJECT):
        print("Supply-agent deployment failed; the existing clinical deployment remains available.")
        return False
    set_value("SUPPLY_AGENT_DEPLOYED", "true")
    print(f"Supply-pricing agent deployed in azd environment {child_env}.")
    return True


def _azd_value(name: str) -> str:
    result = subprocess.run(
        ["azd", "env", "get-value", name], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _set_azd_value(name: str, value: str) -> None:
    result = subprocess.run(
        ["azd", "env", "set", name, value], cwd=ROOT,
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Could not update root azd environment value {name}: {result.stderr.strip()}")


def _run(arguments: list[str], cwd: Path) -> bool:
    result = subprocess.run(arguments, cwd=cwd, check=False)
    return result.returncode == 0


def main() -> int:
    try:
        deploy(_azd_value, _set_azd_value, _run)
    except (OSError, RuntimeError) as exc:
        print(f"Supply-agent hook could not run azd: {exc}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())