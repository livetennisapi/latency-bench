"""Clock sanity: we stamp every source on one machine, so only *that* machine's offset matters."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

log = logging.getLogger(__name__)


def ntp_check(timeout: float = 10.0) -> float | None:
    """Log and return the local clock offset in seconds (None if no tool available).

    Tries, in order: `chronyc tracking`, `ntpdate -q pool.ntp.org`, `ntpq -p`.
    Never raises; a failed check is logged and returns None. The offset is only
    informational — cross-source deltas use time.monotonic() on this host.
    """
    if shutil.which("chronyc"):
        out = _run(["chronyc", "tracking"], timeout)
        m = re.search(r"System time\s*:\s*([0-9.]+) seconds (slow|fast)", out or "")
        if m:
            off = float(m.group(1)) * (-1 if m.group(2) == "slow" else 1)
            log.info("ntp_check chronyc offset=%+.6fs", off)
            return off
    if shutil.which("ntpdate"):
        out = _run(["ntpdate", "-q", "pool.ntp.org"], timeout)
        m = re.search(r"offset\s+([-+]?[0-9.]+)", out or "")
        if m:
            off = float(m.group(1))
            log.info("ntp_check ntpdate offset=%+.6fs", off)
            return off
    if shutil.which("ntpq"):
        out = _run(["ntpq", "-p"], timeout)
        for line in (out or "").splitlines():
            if line.startswith("*"):
                parts = line.split()
                if len(parts) >= 9:
                    off = float(parts[8]) / 1000.0  # ntpq prints ms
                    log.info("ntp_check ntpq offset=%+.6fs", off)
                    return off
    log.warning("ntp_check: no chronyc/ntpdate/ntpq available or no offset parsed")
    return None


def _run(cmd: list[str], timeout: float) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env dependent
        log.warning("ntp_check %s failed: %s", cmd[0], exc)
        return None
