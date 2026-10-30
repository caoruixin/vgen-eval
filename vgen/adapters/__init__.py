from __future__ import annotations

from vgen.adapters import alibaba, seedance, veo
from vgen.adapters.base import AssetResolver, BuildResult, GenerationSpec, Unsupported
from vgen.catalog.registry import ModelInfo

ADAPTERS = {"alibaba": alibaba.build, "veo": veo.build, "seedance": seedance.build}


def build_request(model: ModelInfo, spec: GenerationSpec, assets: AssetResolver,
                  allow_experimental: bool = False) -> BuildResult:
    try:
        builder = ADAPTERS[model.adapter]
    except KeyError as exc:
        raise Unsupported(f"no adapter for family '{model.family}'") from exc
    return builder(model, spec, assets, allow_experimental)
