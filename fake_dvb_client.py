"""
A stand-in for dvb.Client, so the failure paths can be exercised without touching the network.

Used two ways:
  - `python DVB_Monitor.py --fake-client fail` to eyeball the degraded states on the real layout
  - by tests/test_dvb_monitor.py, to assert we never crash
"""

import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace


class FakeDeparture:
    """Shaped like dvb.Departure, for the bits the getters actually touch."""

    def __init__(self, line='3', direction='Wilder Mann', mode='Tram', real_time=None):
        self.line      = line
        self.direction = direction
        self.mode      = mode
        self.real_time = real_time
        self.scheduled = real_time


def sample_departures(count=8, mode='Tram'):
    now = datetime.now(timezone.utc)
    return [
        FakeDeparture(line=str(3 + (ii % 5)),
                      direction=['Wilder Mann', 'Gorbitz', 'Prohlis', 'Weixdorf'][ii % 4],
                      mode=mode,
                      real_time=now + timedelta(minutes=2 + ii * 3))
        for ii in range(count)
    ]


class FakeClient:
    """
    Drop-in for dvb.Client.

    `behaviour` maps a stop name to what monitor() should do for it: a list to return, an
    Exception instance to raise, or a callable producing either.  `default` covers the rest.
    """

    def __init__(self, behaviour=None, default=None):
        self.behaviour = behaviour or {}
        self.default   = default if default is not None else sample_departures()
        self.monitor_calls = []   # every `stop` argument monitor() was called with
        self.find_calls    = []

    def find(self, query, **kwargs):
        self.find_calls.append(query)
        return [SimpleNamespace(id=str(33000000 + len(query)), name=query, city='Dresden')]

    def _resolve_stop_id(self, stop):
        if str(stop).isdigit():
            return str(stop)
        return self.find(stop)[0].id

    def monitor(self, stop, **kwargs):
        self.monitor_calls.append(stop)

        outcome = self.behaviour.get(stop, self.default)

        # the behaviour dict is keyed by stop name, but by the time monitor() is called we may
        # have resolved that name to an id, so fall back to matching on the resolved id too
        if stop not in self.behaviour and str(stop).isdigit():
            for name, configured in self.behaviour.items():
                if self._resolve_stop_id(name) == str(stop):
                    outcome = configured
                    break

        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_fake_client(flavor, stops):
    """Build the fake client behind the --fake-client CLI flag."""
    import dvb

    if flavor == 'ok':
        return FakeClient()

    if flavor == 'empty':
        return FakeClient(default=[])

    if flavor == 'fail':
        return FakeClient(default=dvb.ConnectionError(
            '503 Server Error: Service Unavailable for url: https://webapi.vvo-online.de/dm'))

    if flavor == 'mixed':
        # first stop healthy, second broken, the rest healthy: proves per-stop isolation
        behaviour = {}
        if len(stops) > 1:
            behaviour[stops[1]] = dvb.ConnectionError('503 Server Error: Service Unavailable')
        return FakeClient(behaviour=behaviour)

    if flavor == 'slow':
        def slow():
            time.sleep(20)
            return sample_departures()
        return FakeClient(default=slow)

    raise ValueError(f'unknown fake client flavor {flavor!r}')
