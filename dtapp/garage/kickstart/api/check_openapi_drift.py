"""Standalone OpenAPI-drift check — fails (exit 1) if the hand-authored spec drifts from the actual
Flask routes. Builds a THROWAWAY app registering ONLY our blueprints (kickstart_bp + assets_bp); the
routes live on the blueprint objects, so a scratch app enumerates them faithfully. Does NOT import
dtapp.main (that would boot all of Axle + start the consumer/scheduler threads).

Run:  python -m dtapp.garage.kickstart.api.check_openapi_drift
CI:   exit code 0 = in sync, 1 = drift (message lists each offending operation).
"""
import re
import sys

from flask import Flask

from dtapp.garage.assets.api import assets_bp
from dtapp.garage.kickstart.api.kickoff_api import kickstart_bp
from dtapp.garage.kickstart.api.openapi import _build_paths, app_undocumented_operations

# Swagger UI + the spec itself aren't business operations (see openapi.app_undocumented_operations).
_META = {"/garage/kickstart/docs", "/garage/kickstart/openapi.json"}


def _scratch_app() -> Flask:
    """A bare app with only our two blueprints — enough to enumerate our routes, no Axle boot."""
    app = Flask(__name__)
    app.register_blueprint(kickstart_bp)
    app.register_blueprint(assets_bp)
    return app


def _app_operations(app) -> set:
    """Actual (path, METHOD) business operations under our namespaces (sans meta / HEAD / OPTIONS)."""
    ops = set()
    for rule in app.url_map.iter_rules():
        path = re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", str(rule.rule))
        if not path.startswith(("/garage/kickstart/", "/garage/assets")) or path in _META:
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            ops.add((path, method))
    return ops


def check() -> list[str]:
    """Return a list of drift problems (empty = in sync). Both directions:
    route-not-in-spec (undocumented) and spec-op-with-no-route (stale)."""
    app = _scratch_app()
    problems = [f"UNDOCUMENTED (route not in spec): {op}" for op in app_undocumented_operations(app)]
    documented = {(p, m.upper()) for p, ops in _build_paths().items() for m in ops}
    for path, method in sorted(documented - _app_operations(app)):
        problems.append(f"STALE (spec op has no route): {method} {path}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("OpenAPI drift detected:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("openapi in sync — no drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
