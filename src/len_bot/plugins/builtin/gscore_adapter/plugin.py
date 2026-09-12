"""Compatibility import for the native GSUID Core bridge.

The catalog entry uses ``plugin_core`` directly; this module preserves the old
import path for local integrations without retaining the obsolete action-frame
implementation.
"""
from .plugin_core import GscoreAdapterPlugin

__all__ = ["GscoreAdapterPlugin"]
