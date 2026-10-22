"""Be polite: only work when the laptop is plugged in and not busy, at low priority."""

from __future__ import annotations

import logging
import os
from typing import Any

from .config import Config

log = logging.getLogger("slothdesk.idle")
_UNSET: Any = object()


def _psutil() -> Any:
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def should_run(cfg: Config, psutil_mod: Any = _UNSET) -> tuple[bool, str]:
    ps = _psutil() if psutil_mod is _UNSET else psutil_mod
    if ps is None:
        return True, "psutil_not_installed"  # gating needs: pip install 'slothdesk[idle]'
    try:
        if cfg.require_ac_power:
            battery = ps.sensors_battery()
            if battery is not None and not battery.power_plugged:
                return False, "on_battery"
        cpu = float(ps.cpu_percent(interval=0.5))
    except (OSError, AttributeError, NotImplementedError):
        return True, "gate_unavailable"
    if cpu > cfg.max_cpu_percent:
        return False, f"cpu_busy_{cpu:.0f}pct"
    return True, "ok"


def lower_priority(psutil_mod: Any = _UNSET) -> bool:
    ps = _psutil() if psutil_mod is _UNSET else psutil_mod
    if ps is None:
        return False
    try:
        proc = ps.Process(os.getpid())
        if os.name == "nt":
            proc.nice(ps.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            proc.nice(10)
    except (ps.Error, OSError, AttributeError):
        log.warning("could not lower process priority")
        return False
    return True
