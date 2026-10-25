"""OWNER: B. Decision logic for congestion / heavy hitters (docs/interfaces.md §10).
Pure python, no Ryu import — app.py feeds it rates and acts on the result."""
from common.constants import CONGEST_OFF, CONGEST_ON, CONGEST_SAMPLES, HEAVY_SHARE


def bps(prev_bytes, cur_bytes, dt):
    """Rate in bits/s from two byte counters; 0 on counter reset or bad dt."""
    if dt <= 0 or cur_bytes < prev_bytes:
        return 0.0
    return (cur_bytes - prev_bytes) * 8.0 / dt


class Congestion:
    """Hysteresis detector: on after CONGEST_SAMPLES samples >= CONGEST_ON, off after as many <= CONGEST_OFF."""

    def __init__(self, on=CONGEST_ON, off=CONGEST_OFF, samples=CONGEST_SAMPLES):
        self.on, self.off, self.samples = on, off, samples
        self.active = False
        self._hi = 0
        self._lo = 0

    def update(self, util):
        """Feed one utilisation sample (0..1+). Returns 'on', 'off' or None."""
        if util >= self.on:
            self._hi += 1
            self._lo = 0
        elif util <= self.off:
            self._lo += 1
            self._hi = 0
        else:
            self._hi = self._lo = 0
        if not self.active and self._hi >= self.samples:
            self.active = True
            return "on"
        if self.active and self._lo >= self.samples:
            self.active = False
            return "off"
        return None


def heavy_hosts(rates, capacity_bps, share=HEAVY_SHARE):
    """rates: {ip: bits/s}. Returns the set of ips using >= share of the bottleneck capacity."""
    return {ip for ip, r in rates.items() if r >= share * capacity_bps}
