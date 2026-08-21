"""Join canonical SolveSpec to the byte-preserved M0 patch vocabulary."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openfoam.common.schema_adapter import validate


FEATURE_TO_GEOM = {
    "port:hot-inlet": "port.hot.inlet",
    "port:cold-inlet": "port.cold.inlet",
    "port:hot-outlet": "port.hot.outlet",
    "port:cold-outlet": "port.cold.outlet",
}
OUTLET_BINDINGS = {
    "port.hot.outlet": "hot_outlet",
    "port.cold.outlet": "cold_outlet",
}
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates" / "case"


@dataclass(frozen=True)
class RenderedCase:
    fingerprint: str
    report: dict[str, Any]
    joined_patches: dict[str, str]


def _payload(document: dict[str, Any]) -> dict[str, Any]:
    validate("solve-spec", document)
    return document["payload"]


def _patch_index(patch_map: list[dict[str, Any]]) -> dict[str, str]:
    validate("patch-map", patch_map)
    return {row["geomRef"]: row["patchName"] for row in patch_map}


def join_patches(spec: dict[str, Any], patch_map: list[dict[str, Any]]) -> dict[str, str]:
    index = _patch_index(patch_map)
    joined: dict[str, str] = {}
    for bc in spec["bc"]:
        geom_ref = FEATURE_TO_GEOM[bc["featureRef"]]
        joined[bc["featureRef"]] = index[geom_ref]
    for geom_ref, expected_patch in OUTLET_BINDINGS.items():
        if index.get(geom_ref) != expected_patch:
            raise ValueError(f"outlet binding mismatch for {geom_ref}")
        joined[geom_ref] = expected_patch
    return joined


def _hierarchical_factors(cores: int) -> tuple[int, int, int]:
    best = (cores, 1, 1)
    best_spread = cores
    for x in range(1, cores + 1):
        if cores % x:
            continue
        rest = cores // x
        for y in range(1, rest + 1):
            if rest % y:
                continue
            z = rest // y
            values = tuple(sorted((x, y, z), reverse=True))
            spread = values[0] - values[-1]
            if spread < best_spread:
                best, best_spread = values, spread
    return best


def _replace(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        if old not in text:
            raise ValueError(f"template token absent in {path}: {old!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def _thermo(region: str, material: dict[str, float]) -> str:
    if region == "solid":
        thermo_type = """    type            heSolidThermo;
    mixture         pureMixture;
    transport       constIso;
    thermo          hConst;
    equationOfState rhoConst;
    specie          specie;
    energy          sensibleEnthalpy;"""
        mixture = f"""    specie          {{ molWeight 26.98; }}
    transport       {{ kappa {material['kWMK']:.10g}; }}
    thermodynamics  {{ Hf 0; Cp {material['cpJKgK']:.10g}; }}
    equationOfState {{ rho {material['rhoKgM3']:.10g}; }}"""
    else:
        thermo_type = """    type            heRhoThermo;
    mixture         pureMixture;
    transport       const;
    thermo          hConst;
    equationOfState rhoConst;
    specie          specie;
    energy          sensibleEnthalpy;"""
        mixture = f"""    specie          {{ molWeight 18.0153; }}
    equationOfState {{ rho {material['rhoKgM3']:.10g}; }}
    thermodynamics  {{ Cp {material['cpJKgK']:.10g}; Hf 0; }}
    transport       {{ mu {material['muPaS']:.10g}; Pr {material['prandtl']:.10g}; }}"""
    return f"""// Source model selectors: Phase C CL2 thermophysicalProperties.
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      thermophysicalProperties;
}}

thermoType
{{
{thermo_type}
}}

mixture
{{
{mixture}
}}
"""


def _normalize_foam(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return " ".join(text.split())


def _fingerprint(case: Path, spec: dict[str, Any]) -> str:
    schemes = {}
    for relative in (
        "system/fvSchemes", "system/hot/fvSchemes", "system/cold/fvSchemes", "system/solid/fvSchemes",
    ):
        schemes[relative] = _normalize_foam((case / relative).read_text(encoding="utf-8"))
    thermo = {
        region: _normalize_foam((case / "constant" / region / "thermophysicalProperties").read_text(encoding="utf-8"))
        for region in ("hot", "cold", "solid")
    }
    solutions = {
        relative: _normalize_foam((case / relative).read_text(encoding="utf-8"))
        for relative in ("system/fvSolution", "system/hot/fvSolution", "system/cold/fvSolution", "system/solid/fvSolution")
    }
    boundary_dictionaries = {
        relative: _normalize_foam((case / relative).read_text(encoding="utf-8"))
        for relative in (
            "0/hot/T", "0/hot/U", "0/hot/p", "0/hot/p_rgh",
            "0/cold/T", "0/cold/U", "0/cold/p", "0/cold/p_rgh",
            "0/solid/T", "0/solid/p",
        )
    }
    whitelist = {
        "schemes": schemes,
        "solutions": solutions,
        "relaxation": spec["numerics"]["relaxation"],
        "energyCouplingIters": spec["numerics"]["energyCouplingIters"],
        "hTol": spec["numerics"]["hTol"],
        "writePrecision": spec["numerics"]["writePrecision"],
        "boundaryConditions": spec["bc"],
        "boundaryDictionaries": boundary_dictionaries,
        "outletTemplate": {"kind": "inletOutlet", "pressure": "fixedValue"},
        "thermophysicalProperties": thermo,
        "decompositionMethod": "hierarchical",
    }
    canonical = json.dumps(whitelist, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def render_case(
    solve_document: dict[str, Any],
    patch_map: list[dict[str, Any]],
    case: Path,
    *,
    cores: int,
    write_interval: int,
) -> RenderedCase:
    spec = _payload(solve_document)
    joined = join_patches(spec, patch_map)
    shutil.copytree(TEMPLATE_ROOT, case, dirs_exist_ok=True)
    numerics = spec["numerics"]

    control = case / "system/controlDict"
    _replace(control, {
        "endTime         3000;": f"endTime         {spec['limits']['maxIters']};",
        "writeInterval   500;": f"writeInterval   {write_interval};",
        "writePrecision  10;": f"writePrecision  {numerics['writePrecision']};",
    })
    _replace(case / "system/fvSolution", {
        "iterations      25;": f"iterations      {numerics['energyCouplingIters']};",
        "h           1e-6;": f"h           {numerics['hTol']:.10g};",
    })
    factors = _hierarchical_factors(cores)
    for path in [case / "system/decomposeParDict", *(case / "system" / r / "decomposeParDict" for r in ("hot", "cold", "solid"))]:
        _replace(path, {
            "numberOfSubdomains 16;": f"numberOfSubdomains {cores};",
            "coeffs { n (4 2 2); }": f"coeffs {{ n ({factors[0]} {factors[1]} {factors[2]}); }}",
        })

    fluid_relax = numerics["relaxation"]["fluid"]
    for region in ("hot", "cold"):
        _replace(case / "system" / region / "fvSolution", {
            "fields    { rho 1.0; p_rgh 0.7; }": f"fields    {{ rho {fluid_relax['rho']}; p_rgh {fluid_relax['pRgh']}; }}",
            "equations { U 0.3; h 0.5; }": f"equations {{ U {fluid_relax['U']}; h {fluid_relax['h']}; }}",
        })
    _replace(case / "system/solid/fvSolution", {
        "equations { h 0.7; }": f"equations {{ h {numerics['relaxation']['solid']['h']}; }}",
    })

    bc_by_ref = {item["featureRef"]: item for item in spec["bc"]}
    for region, feature, fixture_mdot, fixture_rho, fixture_t in (
        ("hot", "port:hot-inlet", "0.023490376702", "983.2", "333.15"),
        ("cold", "port:cold-inlet", "0.023821510837", "998.2", "293.15"),
    ):
        bc = bc_by_ref[feature]
        material = spec["materials"][region]
        _replace(case / "0" / region / "U", {
            f"massFlowRate constant {fixture_mdot};": f"massFlowRate constant {bc['mdotKgS']:.12g};",
            f"rhoInlet {fixture_rho};": f"rhoInlet {material['rhoKgM3']:.10g};",
        })
        _replace(case / "0" / region / "T", {
            f"value uniform {fixture_t};": f"value uniform {bc['tK']:.10g};",
            f"inletValue uniform {fixture_t};": f"inletValue uniform {bc['tK']:.10g};",
        })

    for region, material in spec["materials"].items():
        (case / "constant" / region / "thermophysicalProperties").write_text(_thermo(region, material), encoding="utf-8")

    fingerprint = _fingerprint(case, spec)
    report = {
        "schemaVersion": 1,
        "renderFingerprint": fingerprint,
        "diagnostics": {"template": "phase-c-cl2", "renderedFileCount": sum(1 for path in case.rglob("*") if path.is_file())},
    }
    validate("solve-report", report)
    return RenderedCase(fingerprint, report, joined)
