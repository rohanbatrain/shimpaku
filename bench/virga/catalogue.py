"""Load the catalogue (plus the private holdout file), expand variables and probe sets."""

from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from virga import schema


class CatalogueError(Exception):
    pass


@dataclass
class Catalogue:
    lab: dict
    scenarios: dict[str, dict]
    sha256: str
    holdout_ids: set[str] = field(default_factory=set)

    def runnable(self, have: set[str], include_holdout: bool = True) -> list[str]:
        return [i for i, s in self.scenarios.items()
                if s.get("executable") and set(s["requires"]) <= have
                and (include_holdout or i not in self.holdout_ids)]

    def benchable(self, have: set[str], include_holdout: bool) -> list[str]:
        return [i for i in self.runnable(have, include_holdout)
                if self.scenarios[i].get("bench", True) and not self.scenarios[i].get("compound")]


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def load(path: Path, holdout: Path | None = None, check: bool = True) -> Catalogue:
    raw = path.read_bytes()
    docs = _docs(path)
    labs = [d for d in docs if d.get("kind") == "lab"]
    if len(labs) != 1:
        raise CatalogueError(f"{path}: expected exactly one `kind: lab` document, found {len(labs)}")
    scen = [d for d in docs if d.get("kind") == "scenario"]
    h = hashlib.sha256(raw)
    hold: set[str] = set()
    if holdout is not None and holdout.exists():
        extra = [d for d in _docs(holdout) if d.get("kind") == "scenario"]
        for d in extra:
            d["holdout"] = True
        hold = {d["id"] for d in extra}
        scen += extra
        h.update(holdout.read_bytes())
    hold |= {d["id"] for d in scen if d.get("holdout")}
    if check:
        errs = schema.validate(labs[0], scen)
        if errs:
            raise CatalogueError("catalogue invalid:\n  " + "\n  ".join(errs))
    return Catalogue(labs[0], {s["id"]: s for s in scen}, h.hexdigest(), hold)


def subst(obj, env: dict):
    """${var} substitution in every string of a nested structure (Junos and shell text never use ${)."""
    if isinstance(obj, str):
        return string.Template(obj).safe_substitute(env)
    if isinstance(obj, list):
        return [subst(x, env) for x in obj]
    if isinstance(obj, dict):
        return {k: subst(v, env) for k, v in obj.items()}
    return obj


def scenario_env(lab: dict, sc: dict, intensity: dict | None = None) -> dict:
    import os

    # VIRGA_VAR_<name> overrides a lab var (e.g. intent_dir when running as user aiops-bench)
    env = {k: os.environ.get(f"VIRGA_VAR_{k}", str(v)) for k, v in (lab.get("vars") or {}).items()}
    for name, spec in ((sc.get("toggle") or {}).get("intensity") or {}).items():
        env[name] = str((intensity or {}).get(name, spec.get("default")))
    return env


def intensity_of(sc: dict, env: dict) -> dict:
    return {k: env[k] for k in ((sc.get("toggle") or {}).get("intensity") or {})}


def expand_probes(lab: dict, sc: dict, env: dict) -> list[dict]:
    probes = []
    for set_name in sc.get("health", []) or []:
        for p in lab["health_sets"][set_name]:
            probes.append({**p, "id": f"{set_name}:{p['id']}", "guard": True})
    probes += [dict(p) for p in sc.get("probes", []) or []]
    over = {o["id"]: o for o in sc.get("health_override", []) or []}
    return [subst({**p, **over[p["id"]]} if p["id"] in over else p, env) for p in probes]


def touched_nodes(steps: list[dict]) -> list[str]:
    seen: list[str] = []
    for st in steps:
        for key in ("junos", "junos_op", "auto", "flap"):
            if key in st and st[key].get("node") and st[key]["node"] not in seen:
                seen.append(st[key]["node"])
    return seen


def timing(lab: dict, sc: dict) -> dict:
    return {**(lab.get("timing_defaults") or {}), **(sc.get("timing") or {})}
