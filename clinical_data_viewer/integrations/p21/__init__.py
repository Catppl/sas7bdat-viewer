"""Thin host integration for the optional P21 Issue Explorer side-car."""

from .host import P21HostController
from .manifest import P21SidecarManifest, discover_sidecar_manifest

__all__ = [
    "P21HostController",
    "P21SidecarManifest",
    "discover_sidecar_manifest",
]
