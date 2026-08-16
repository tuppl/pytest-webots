"""Webots process lifecycle: launch, readiness, reset, reload, shutdown."""

from __future__ import annotations


class WebotsInstance:
    """A running (or restartable) Webots process for one world."""
