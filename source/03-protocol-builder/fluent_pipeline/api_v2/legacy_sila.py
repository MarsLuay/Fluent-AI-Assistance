"""Legacy SiLA / WCF bootstrap helpers (api-v2-076)."""

from __future__ import annotations

from typing import Any


def resolve_legacy_service_endpoint(
    host: str,
    port: int,
    *,
    use_generate_service_endpoint: bool = False,
) -> dict[str, Any]:
    """Resolve the legacy SiLA service endpoint for observability only.

    ``HelperAPI.GenerateServiceEndpoint()`` is intentionally **not** called: WCF
    workspace APIs are version-sensitive and a native VisionX API V2 COM provider
    must not depend on endpoint discovery. Callers should keep using
    ``TECAN_FLUENT_HOST`` / ``TECAN_FLUENT_PORT`` (default ``127.0.0.1:50052``).
    """
    endpoint = f"http://{host}:{port}"
    return {
        "strategy": "env_configured",
        "endpoint": endpoint,
        "host": host,
        "port": port,
        "discovered": False,
        "generate_service_endpoint_requested": bool(use_generate_service_endpoint),
        "skipped_reason": (
            "HelperAPI.GenerateServiceEndpoint() is design-only; native API V2 "
            "providers must not depend on WCF endpoint discovery."
        ),
    }
