"""
A stand-in for dvb.Client, so the failure paths can be exercised without touching the network.

Used two ways:
  - `python DVB_Monitor.py --fake-client fail` to eyeball the degraded states on the real layout
  - by tests/test_dvb_monitor.py, to assert we never crash
"""

import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace


# Altmarkt, near enough. Everything the fake makes up is placed relative to this.
ORIGIN_LAT = 51.0502
ORIGIN_LNG = 13.7384


def coords(lat, lng):
    return SimpleNamespace(lat=lat, lng=lng)


class FakeDeparture:
    """Shaped like dvb.Departure, for the bits the getters actually touch."""

    def __init__(self, line='3', direction='Wilder Mann', mode='Tram', real_time=None, id=None):
        self.line      = line
        self.direction = direction
        self.mode      = mode
        self.real_time = real_time
        self.scheduled = real_time
        # the trip id.  real ones look like 'voe:11009: :H:j26'; unique per departure.
        self.id        = id if id is not None else f'trip-{line}-{direction}'


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

    def __init__(self, behaviour=None, default=None,
                 next_stop_offsets=None, trip_behaviour=None):
        self.behaviour = behaviour or {}
        self.default   = default if default is not None else sample_departures()
        self.monitor_calls = []   # every `stop` argument monitor() was called with
        self.find_calls    = []
        self.trip_calls    = []

        # per trip id: where the next stop sits, and anything unusual we want to simulate
        self.next_stop_offsets = next_stop_offsets or {}
        self.trip_behaviour    = trip_behaviour or {}

    def find(self, query, **kwargs):
        self.find_calls.append(query)
        return [SimpleNamespace(id=str(33000000 + len(query)), name=query, city='Dresden',
                                coords=coords(ORIGIN_LAT, ORIGIN_LNG))]

    def trip_details(self, trip_id, time, stop_id, **kwargs):
        """
        A route running through stop_id. `next_stop_offsets` maps a trip id to the (dlat, dlng)
        of the stop after ours, so a test can ask for a known bearing; anything else heads due
        east, which is a '>' arrow.
        """
        self.trip_calls.append((trip_id, str(stop_id)))

        outcome = self.trip_behaviour.get(trip_id)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == 'terminus':
            # our stop is the last one, so there is nothing after it
            return [SimpleNamespace(id='1', name='Before', coords=coords(ORIGIN_LAT, ORIGIN_LNG - 0.01)),
                    SimpleNamespace(id=str(stop_id), name='Here', coords=coords(ORIGIN_LAT, ORIGIN_LNG))]
        if outcome == 'absent':
            # our stop is not in the trip at all
            return [SimpleNamespace(id='999', name='Elsewhere', coords=coords(ORIGIN_LAT, ORIGIN_LNG))]

        dlat, dlng = self.next_stop_offsets.get(trip_id, (0.0, 0.01))   # due east by default
        return [
            SimpleNamespace(id='1', name='Before', coords=coords(ORIGIN_LAT - 0.01, ORIGIN_LNG)),
            SimpleNamespace(id=str(stop_id), name='Here', coords=coords(ORIGIN_LAT, ORIGIN_LNG)),
            SimpleNamespace(id='2', name='Next', coords=coords(ORIGIN_LAT + dlat, ORIGIN_LNG + dlng)),
        ]

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
