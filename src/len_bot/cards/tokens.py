"""Versioned visual tokens shared by deterministic Pillow cards."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LightThemeV1:
    canvas: str = "#FAFAFA"
    card: str = "#FFFFFF"
    ink: str = "#1E293B"
    muted: str = "#64748B"
    border: str = "#E2E8F0"
    accent: str = "#FB7299"
    accent_deep: str = "#D13868"
    soft: str = "#FFF1F5"
    quote: str = "#EEF2FF"
    radius_outer: int = 28
    radius_inner: int = 18
    margin: int = 54
    gap: int = 18
    theme_version: str = "light-v1"


THEME = LightThemeV1()
