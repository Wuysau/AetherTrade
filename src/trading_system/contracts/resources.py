"""Resolve contracts in a checkout or in an installed distribution."""
from importlib.resources import files
from pathlib import Path


def contract_path(name: str) -> Path:
    if Path(name).name != name or not name.endswith((".json", ".yaml")):
        raise ValueError("A contract filename is required")
    checkout = Path(__file__).resolve().parents[3] / "contracts" / name
    if checkout.is_file():
        return checkout
    resource = files("trading_system").joinpath("schemas", name)
    path = Path(str(resource))
    if not path.is_file():
        raise FileNotFoundError(f"Packaged contract unavailable: {name}")
    return path
