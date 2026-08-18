"""
Robustness tests: the app must survive whatever the DVB api throws at it.

Run headless:  QT_QPA_PLATFORM=offscreen pytest -q
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import dvb
import requests
import numpy as np

import DVB_Monitor as dm
from fake_dvb_client import FakeClient, FakeDeparture, sample_departures


STOPS = ['Altmarkt', 'Postplatz', 'Pirnaischer Platz']


# --------------------------------------------------------------------------------------
# tier 1: the module-level pure functions.  no Qt needed.
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize('mode', [
    'Tram', 'SuburbanRailway', 'Ferry', 'Cableway', 'HailedSharedTaxi',
    '', 'SomethingDVBInventedYesterday', None,
])
def test_get_mode_emoji_always_returns_a_string(mode):
    """The old version raised NameError for any mode not in the dict."""
    result = dm.get_mode_emoji(FakeDeparture(mode=mode))
    assert isinstance(result, str)


def test_get_mode_emoji_survives_a_departure_with_no_mode():
    assert isinstance(dm.get_mode_emoji(object()), str)


def test_get_mode_emoji_warns_only_once_per_unknown_mode(capsys):
    dm._warned_modes.discard('Hovercraft')
    dm.get_mode_emoji(FakeDeparture(mode='Hovercraft'))
    dm.get_mode_emoji(FakeDeparture(mode='Hovercraft'))
    assert capsys.readouterr().out.count('Hovercraft') == 1


def test_get_minutes_handles_missing_real_time():
    assert dm.get_minutes(FakeDeparture(real_time=None)) == np.inf


def test_get_minutes_handles_a_naive_datetime():
    minutes = dm.get_minutes(FakeDeparture(real_time=datetime.now()))
    assert isinstance(minutes, int)


def test_get_minutes_handles_garbage():
    assert dm.get_minutes(FakeDeparture(real_time='not a datetime')) == np.inf


def test_get_minutes_is_sortable_across_mixed_departures():
    departures = [
        FakeDeparture(real_time=None),
        FakeDeparture(real_time=datetime.now(timezone.utc) + timedelta(minutes=5)),
        FakeDeparture(real_time=datetime.now(timezone.utc) + timedelta(minutes=1)),
    ]
    departures.sort(key=dm.get_minutes)
    assert departures[-1].real_time is None   # unknowns sink to the bottom


@pytest.mark.parametrize('failure', [
    dvb.ConnectionError('503 Server Error: Service Unavailable'),
    dvb.APIError('API returned status: ServiceError'),
    requests.exceptions.JSONDecodeError('Expecting value', 'doc', 0),
    KeyError('ScheduledTime'),
    ValueError('bad date'),
    dvb.ConnectionError('Read timed out'),
    RuntimeError('something nobody predicted'),
])
def test_fetch_never_raises(failure):
    client = FakeClient(default=failure)
    result = dm.fetch_departures_for_stop(client, 'Altmarkt')
    assert result.error is not None
    assert result.departures is None
    assert result.stop_name == 'Altmarkt'


def test_fetch_rejects_a_non_list_response():
    """dvb returns a dict when raw=True; anything but a list means something is wrong."""
    result = dm.fetch_departures_for_stop(FakeClient(default={'Departures': []}), 'Altmarkt')
    assert 'unexpected response type' in result.error


def test_fetch_accepts_an_empty_list():
    result = dm.fetch_departures_for_stop(FakeClient(default=[]), 'Altmarkt')
    assert result.error is None
    assert result.departures == []


def test_fetch_sorts_and_reports_the_resolved_stop_id():
    result = dm.fetch_departures_for_stop(FakeClient(), 'Altmarkt')
    assert result.error is None
    assert result.stop_id.isdigit()
    minutes = [dm.get_minutes(d) for d in result.departures]
    assert minutes == sorted(minutes)


def test_fetch_uses_a_cached_stop_id_without_a_lookup():
    client = FakeClient()
    dm.fetch_departures_for_stop(client, 'Altmarkt', stop_id='33000013')
    assert client.find_calls == []
    assert client.monitor_calls == ['33000013']


# --------------------------------------------------------------------------------------
# tier 2: the widget.  needs a QApplication.
# --------------------------------------------------------------------------------------

from PyQt5.QtWidgets import QApplication


@pytest.fixture(scope='session')
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def config_path(tmp_path):
    import yaml
    config = {
        'dvb_client_name': 'pytest <test@example.com>',
        'stops_to_monitor': list(STOPS),
        'num_stops_per_page': 3,
        'num_rows_per_table': 12,
        'window_width': 1300,
        'window_height': 400,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': False,   # synchronous, so assertions can run right after refresh()
        'verbosity': 0,
    }
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return str(path)


# widgets built during a test.  without explicit teardown, python garbage collects them at
# interpreter shutdown while Qt is tearing the QApplication down, which segfaults now and then.
_monitors = []


@pytest.fixture(autouse=True)
def _close_monitors(qapp):
    yield
    while _monitors:
        monitor = _monitors.pop()
        monitor._stop_fetcher()
        monitor.close()
        monitor.deleteLater()
    qapp.processEvents()


def build_monitor(qapp, config_path, client):
    class TestableMonitor(dm.DVB_Monitor):
        def setup_dvb_client(self):
            self.client = client

    monitor = TestableMonitor(qapp, config_path=config_path)
    _monitors.append(monitor)
    return monitor


def test_total_outage_does_not_crash_and_shows_it(qapp, config_path):
    client = FakeClient(default=dvb.ConnectionError('503 Server Error: Service Unavailable'))
    monitor = build_monitor(qapp, config_path, client)

    monitor.refresh()   # must not raise

    assert all(monitor.stop_status[s] == 'error' for s in STOPS)
    assert monitor.time_last_updated is None
    assert 'cannot reach DVB' in monitor.time_updated_widget.text()


def test_one_bad_stop_does_not_block_the_others(qapp, config_path):
    """The isolation requirement: a failure on Postplatz must not hide Altmarkt."""
    client = FakeClient(behaviour={'Postplatz': dvb.ConnectionError('503 Service Unavailable')})
    monitor = build_monitor(qapp, config_path, client)

    monitor.refresh()

    assert monitor.stop_status['Altmarkt'] == 'ok'
    assert monitor.stop_status['Postplatz'] == 'error'
    assert monitor.stop_status['Pirnaischer Platz'] == 'ok'
    assert monitor.departures['Altmarkt']
    assert monitor.departures['Pirnaischer Platz']
    assert '1/3 stops failed' in monitor.time_updated_widget.text()


def test_stale_data_is_kept_and_marked_on_failure(qapp, config_path):
    client = FakeClient()
    monitor = build_monitor(qapp, config_path, client)
    monitor.refresh()
    assert monitor.departures['Altmarkt']

    client.default = dvb.ConnectionError('503 Server Error: Service Unavailable')
    monitor.refresh()

    # data retained, per show_stale_data_on_error
    assert monitor.departures['Altmarkt']
    assert monitor.stop_status['Altmarkt'] == 'error'
    assert '⚠️' in monitor.header_widgets[0].text()


def test_recovery_after_an_outage(qapp, config_path):
    client = FakeClient(default=dvb.ConnectionError('503 Service Unavailable'))
    monitor = build_monitor(qapp, config_path, client)
    monitor.refresh()
    assert monitor.num_consecutive_failures == 1

    client.default = sample_departures()
    monitor.refresh()

    assert monitor.num_consecutive_failures == 0
    assert all(monitor.stop_status[s] == 'ok' for s in STOPS)
    assert monitor._next_refresh_interval_ms() == monitor.refresh_interval_ms


def test_backoff_grows_and_is_capped(qapp, config_path):
    client = FakeClient(default=dvb.ConnectionError('503 Service Unavailable'))
    monitor = build_monitor(qapp, config_path, client)

    intervals = []
    for _ in range(12):
        monitor.refresh()
        intervals.append(monitor._next_refresh_interval_ms())

    assert intervals[0] > monitor.refresh_interval_ms      # backed off
    assert intervals == sorted(intervals)                  # monotonically increasing
    assert max(intervals) <= monitor.retry_backoff_max * 1000


def test_a_single_flaky_stop_does_not_trigger_backoff(qapp, config_path):
    client = FakeClient(behaviour={'Postplatz': dvb.ConnectionError('503')})
    monitor = build_monitor(qapp, config_path, client)

    monitor.refresh()

    assert monitor.num_consecutive_failures == 0
    assert monitor._next_refresh_interval_ms() == monitor.refresh_interval_ms


def test_empty_departure_list_is_not_an_error(qapp, config_path):
    monitor = build_monitor(qapp, config_path, FakeClient(default=[]))

    monitor.refresh()

    assert all(monitor.stop_status[s] == 'ok' for s in STOPS)
    assert monitor.time_last_updated is not None


def test_unknown_mode_does_not_crash_the_repaint(qapp, config_path):
    client = FakeClient(default=sample_departures(mode='SuburbanRailway'))
    monitor = build_monitor(qapp, config_path, client)

    monitor.refresh()   # the old get_mode_emoji raised NameError here

    assert monitor.stop_status['Altmarkt'] == 'ok'


def test_malformed_departures_degrade_to_placeholder_cells(qapp, config_path):
    class Exploding:
        @property
        def line(self):
            raise RuntimeError('boom')
        direction = 'Somewhere'
        mode = 'Tram'
        real_time = None

    monitor = build_monitor(qapp, config_path, FakeClient(default=[Exploding()]))

    monitor.refresh()   # must not raise

    assert monitor.stop_status['Altmarkt'] == 'ok'


def test_stop_ids_are_cached_so_lookups_happen_once(qapp, config_path):
    client = FakeClient()
    monitor = build_monitor(qapp, config_path, client)

    monitor.refresh()
    monitor.refresh()
    monitor.refresh()

    assert sorted(client.find_calls) == sorted(STOPS)          # one lookup per stop, total
    assert all(str(s).isdigit() for s in client.monitor_calls[len(STOPS):])


def test_change_page_does_not_divide_by_zero(qapp, tmp_path):
    """num_stops_per_page > len(stops) used to make num_pages_needed zero."""
    import yaml
    config = {
        'dvb_client_name': 'pytest <test@example.com>',
        'stops_to_monitor': ['Altmarkt'],
        'num_stops_per_page': 3,
        'num_rows_per_table': 12,
        'window_width': 1300,
        'window_height': 400,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': False,
        'verbosity': 0,
    }
    path = tmp_path / 'one_stop.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())

    assert monitor.num_pages_needed >= 1
    monitor.change_page(+1)   # used to be ZeroDivisionError
    monitor.change_page(-1)


def test_change_page_before_any_successful_fetch(qapp, config_path):
    """time_last_updated is None until something succeeds; change_page used to subtract from it."""
    client = FakeClient(default=dvb.ConnectionError('503 Service Unavailable'))
    monitor = build_monitor(qapp, config_path, client)
    monitor.refresh()

    monitor.change_page(+1)   # must not raise


def test_all_pages_are_reachable(qapp, tmp_path):
    """floor division used to make a partial last page unreachable."""
    import yaml
    config = {
        'dvb_client_name': 'pytest <test@example.com>',
        'stops_to_monitor': ['A', 'B', 'C', 'D'],
        'num_stops_per_page': 3,
        'num_rows_per_table': 12,
        'window_width': 1300,
        'window_height': 400,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': False,
        'verbosity': 0,
    }
    path = tmp_path / 'four.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())

    assert monitor.num_pages_needed == 2


# --------------------------------------------------------------------------------------
# config loading
# --------------------------------------------------------------------------------------

def test_empty_config_file_falls_back_to_defaults(qapp, tmp_path):
    """
    An empty yaml parses to None; `"x" not in None` used to raise a bare TypeError.

    It also used to be rejected outright for lacking dvb_client_name. Now that the client name
    lives outside the config, an empty config is simply every default.
    """
    (tmp_path / dm.CLIENT_NAME_FILENAME).write_text('Me <me@example.com>', encoding='utf-8')

    path = tmp_path / 'empty.yaml'
    path.write_text('', encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())

    assert monitor.stops_to_monitor == dm.DEFAULT_CONFIG['stops_to_monitor']
    assert monitor.dvb_client_name == 'Me <me@example.com>'


def test_config_that_is_a_list_exits_cleanly(qapp, tmp_path):
    path = tmp_path / 'list.yaml'
    path.write_text('- one\n- two\n', encoding='utf-8')

    with pytest.raises(SystemExit):
        build_monitor(qapp, str(path), FakeClient())


def test_missing_config_file_exits_cleanly(qapp, tmp_path):
    with pytest.raises(SystemExit):
        build_monitor(qapp, str(tmp_path / 'nope.yaml'), FakeClient())


# --------------------------------------------------------------------------------------
# the background fetch path
# --------------------------------------------------------------------------------------

from PyQt5.QtCore import QEventLoop, QTimer


@pytest.fixture
def background_config_path(tmp_path):
    import yaml
    config = {
        'dvb_client_name': 'pytest <test@example.com>',
        'stops_to_monitor': list(STOPS),
        'num_stops_per_page': 3,
        'num_rows_per_table': 12,
        'window_width': 1300,
        'window_height': 400,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': True,
        'verbosity': 0,
    }
    path = tmp_path / 'background.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return str(path)


def wait_for_fetch(monitor, timeout_ms=10000):
    """Spin the event loop until the in-flight background fetch reports it is done."""
    loop = QEventLoop()
    monitor.fetcher.all_finished.connect(loop.quit)

    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(timeout_ms)

    loop.exec_()
    monitor.fetcher.wait(2000)


def test_background_fetch_delivers_results(qapp, background_config_path):
    monitor = build_monitor(qapp, background_config_path, FakeClient())

    monitor.refresh()
    assert monitor.fetcher is not None
    wait_for_fetch(monitor)
    qapp.processEvents()   # let the queued signals land on the gui thread

    assert all(monitor.stop_status[s] == 'ok' for s in STOPS)
    assert all(monitor.departures[s] for s in STOPS)


def test_background_fetch_survives_an_outage(qapp, background_config_path):
    client = FakeClient(default=dvb.ConnectionError('503 Server Error: Service Unavailable'))
    monitor = build_monitor(qapp, background_config_path, client)

    monitor.refresh()
    wait_for_fetch(monitor)
    qapp.processEvents()

    assert all(monitor.stop_status[s] == 'error' for s in STOPS)
    assert 'cannot reach DVB' in monitor.time_updated_widget.text()


def test_background_fetch_isolates_a_single_bad_stop(qapp, background_config_path):
    client = FakeClient(behaviour={'Postplatz': dvb.ConnectionError('503 Service Unavailable')})
    monitor = build_monitor(qapp, background_config_path, client)

    monitor.refresh()
    wait_for_fetch(monitor)
    qapp.processEvents()

    assert monitor.stop_status['Altmarkt'] == 'ok'
    assert monitor.stop_status['Postplatz'] == 'error'
    assert monitor.departures['Altmarkt']


def test_overlapping_refresh_is_skipped(qapp, background_config_path):
    """A second tick while a fetch is in flight must not start a second thread."""
    monitor = build_monitor(qapp, background_config_path, FakeClient())

    monitor.refresh()
    first = monitor.fetcher
    monitor.refresh()

    assert monitor.fetcher is first

    wait_for_fetch(monitor)
    qapp.processEvents()


def test_stopping_the_fetcher_mid_flight_is_clean(qapp, background_config_path):
    """Escape quits via QApplication.quit, which bypasses closeEvent; _stop_fetcher covers it."""
    import time as _time
    import threading

    entered = threading.Event()

    def slow():
        entered.set()
        _time.sleep(1.0)
        return sample_departures()

    monitor = build_monitor(qapp, background_config_path, FakeClient(default=slow))

    monitor.refresh()

    # make sure the worker really is mid-request before we pull the rug out
    assert entered.wait(5), 'the background fetch never started'
    assert monitor.fetcher.isRunning()

    monitor._stop_fetcher()   # must not raise, and must not leave a running thread

    assert not monitor.fetcher.isRunning()


def test_quitting_stops_the_fetcher(qapp, background_config_path):
    """aboutToQuit must be wired, since the escape shortcut never reaches closeEvent."""
    monitor = build_monitor(qapp, background_config_path, FakeClient())

    monitor.refresh()
    qapp.aboutToQuit.emit()   # what QApplication.quit() triggers

    assert not monitor.fetcher.isRunning()


def test_fewer_stops_than_slots_per_page(qapp, tmp_path):
    """init_tables builds one table per stop, not one per page slot; rebuild must cope."""
    import yaml
    config = {
        'dvb_client_name': 'pytest <test@example.com>',
        'stops_to_monitor': ['Altmarkt'],
        'num_stops_per_page': 3,
        'num_rows_per_table': 12,
        'window_width': 1300,
        'window_height': 400,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': False,
        'verbosity': 0,
    }
    path = tmp_path / 'one_stop_three_slots.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())

    monitor.refresh()   # used to KeyError on self.tables[1]

    assert monitor.departures['Altmarkt']


def test_partial_last_page_blanks_the_unused_table(qapp, tmp_path):
    import yaml
    config = {
        'dvb_client_name': 'pytest <test@example.com>',
        'stops_to_monitor': ['A', 'B', 'C'],
        'num_stops_per_page': 2,
        'num_rows_per_table': 12,
        'window_width': 1300,
        'window_height': 400,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': False,
        'verbosity': 0,
    }
    path = tmp_path / 'three_two.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())
    monitor.refresh()

    assert monitor.num_pages_needed == 2
    monitor.current_page = 1
    monitor.rebuild()   # page 2 holds only 'C'; the second table must not keep showing 'B'

    assert monitor.header_widgets[0].text().startswith('C')
    assert monitor.header_widgets[1].text() == ''


def test_watchdog_reschedules_a_fetch_that_never_reports(qapp, background_config_path):
    """If all_finished never arrives, the refresh loop must not silently freeze."""
    monitor = build_monitor(qapp, background_config_path, FakeClient())

    monitor.refresh()
    wait_for_fetch(monitor)
    qapp.processEvents()

    assert not monitor.timer_fetch_watchdog.isActive()   # cleared on normal completion

    # simulate the signal going missing: the watchdog must still re-arm the refresh timer
    monitor.timer_refresh.stop()
    monitor._on_fetch_watchdog()

    assert monitor.timer_refresh.isActive()


# --------------------------------------------------------------------------------------
# stylesheet / layout
# --------------------------------------------------------------------------------------

def write_config(tmp_path, name, css, **overrides):
    """A config pointing at a stylesheet written just for this test."""
    import yaml
    css_path = tmp_path / f'{name}.css'
    css_path.write_text(css, encoding='utf-8')

    config = {
        'dvb_client_name': 'pytest <test@example.com>',
        'stops_to_monitor': ['Altmarkt'],
        'num_stops_per_page': 1,
        'num_rows_per_table': 6,
        'num_departures_to_monitor': 6,
        'row_height': 30,
        'window_width': 1300,
        'window_height': 900,
        'css_file': str(css_path),
        'fetch_in_background': False,
        'verbosity': 0,
    }
    config.update(overrides)

    path = tmp_path / f'{name}.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return str(path)


def css_with_cell_font(points):
    return f'''
QLabel[class="grid_cell"] {{
    font-size: {points}pt;
    color: #ffffff;
    background-color: #1a1a1a;
    border: 1px solid #2a2a2a;
    padding: 2px;
}}
QLabel[class="grid_header"] {{ font-size: {points}pt; }}
'''


def test_rows_grow_to_fit_a_larger_font(qapp, tmp_path):
    """Raising font-size used to clip the text, because cells were pinned to row_height."""
    small = build_monitor(qapp, write_config(tmp_path, 'small', css_with_cell_font(10)), FakeClient())
    big   = build_monitor(qapp, write_config(tmp_path, 'big',   css_with_cell_font(28)), FakeClient())

    assert big.tables[0].effective_row_height > small.tables[0].effective_row_height


def test_row_height_from_config_is_a_minimum(qapp, tmp_path):
    """A tiny font must not shrink the rows below what the config asked for."""
    monitor = build_monitor(qapp, write_config(tmp_path, 'tiny', css_with_cell_font(6)), FakeClient())

    assert monitor.tables[0].effective_row_height == 30


def test_a_large_font_actually_fits_its_row(qapp, tmp_path):
    """The whole point: the glyphs must fit inside the cell, not get guillotined."""
    from PyQt5.QtGui import QFontMetrics

    monitor = build_monitor(qapp, write_config(tmp_path, 'fits', css_with_cell_font(28)), FakeClient())
    monitor.refresh()

    cell = monitor.tables[0].labels[(0, 0)]
    assert QFontMetrics(cell.font()).height() <= cell.height()


def test_narrow_columns_are_reported(qapp, tmp_path):
    """Column widths come from config and can't auto-grow, so they get reported instead."""
    path = write_config(tmp_path, 'narrow', css_with_cell_font(28), columns=[
        {'header': 'Destination', 'width': 20, 'getter': 'get_destination', 'alignment': 'left'},
    ])
    monitor = build_monitor(qapp, path, FakeClient())

    reported = monitor.tables[0].narrow_columns()

    assert [name for name, _, _ in reported] == ['Destination']


def test_wide_enough_columns_are_not_reported(qapp, tmp_path):
    path = write_config(tmp_path, 'wide', css_with_cell_font(10), columns=[
        {'header': 'Dest', 'width': 300, 'getter': 'get_destination', 'alignment': 'left'},
    ])
    monitor = build_monitor(qapp, path, FakeClient())

    assert monitor.tables[0].narrow_columns() == []


def test_elided_label_shortens_but_remembers_the_full_text(qapp):
    label = dm.ElidedLabel('Wilder Mann via Somewhere Long')
    label.setFixedWidth(60)
    label.show()
    qapp.processEvents()

    assert label.text() == 'Wilder Mann via Somewhere Long'   # what was set
    assert '…' in dm.QLabel.text(label)                        # what is on screen
    label.close()


def test_elided_label_leaves_short_text_alone(qapp):
    label = dm.ElidedLabel('Ok')
    label.setFixedWidth(300)
    label.show()
    qapp.processEvents()

    assert dm.QLabel.text(label) == 'Ok'
    label.close()


def test_elided_cells_still_render_through_the_stylesheet(qapp, tmp_path):
    """
    Elided columns used to paint their own text and so never drew the css background/border,
    leaving them visibly transparent next to every other column.
    """
    path = write_config(tmp_path, 'elide', css_with_cell_font(12), columns=[
        {'header': 'Dest', 'width': 60, 'getter': 'get_destination',
         'alignment': 'left', 'elide': True},
    ])
    monitor = build_monitor(qapp, path, FakeClient())
    monitor.refresh()

    cell = monitor.tables[0].labels[(0, 0)]

    assert isinstance(cell, dm.ElidedLabel)
    # QLabel does the painting now, rather than a custom paintEvent that skipped the background
    assert 'paintEvent' not in dm.ElidedLabel.__dict__
    # and the text really is being shortened to fit
    assert '…' in dm.QLabel.text(cell)


def test_stylesheets_shipped_with_the_app_only_style_classes_that_exist(qapp):
    """
    style.css used to define header/title/body rules that were never applied to any widget, so
    editing them did nothing at all.  Keep the shipped stylesheets honest.
    """
    import re

    applied = {'grid_cell', 'grid_header', 'haltestelle_header',
               'haltestelle_header_stale', 'haltestelle_header_error', 'footer'}

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ('style.css', 'style_pitft.css'):
        css = open(os.path.join(root, name), encoding='utf-8').read()
        defined = set(re.findall(r'class="([^"]+)"', css))
        assert defined <= applied, f'{name} styles classes no widget ever gets: {defined - applied}'


# --------------------------------------------------------------------------------------
# where the DVB client name comes from
# --------------------------------------------------------------------------------------

def test_client_name_file_reads_the_first_meaningful_line(tmp_path):
    path = tmp_path / dm.CLIENT_NAME_FILENAME
    path.write_text('# put your details below\n\n  Me <me@example.com>  \nignored second line\n',
                    encoding='utf-8')

    assert dm.read_client_name_file(str(path)) == 'Me <me@example.com>'


def test_client_name_file_that_is_missing_or_empty(tmp_path):
    assert dm.read_client_name_file(str(tmp_path / 'nope.txt')) is None

    blank = tmp_path / 'blank.txt'
    blank.write_text('\n\n# only a comment\n', encoding='utf-8')
    assert dm.read_client_name_file(str(blank)) is None


def test_search_path_prefers_the_config_directory(tmp_path):
    search = dm.client_name_search_path(str(tmp_path / 'config.yaml'))

    assert search[0] == str(tmp_path / dm.CLIENT_NAME_FILENAME)
    # and the checkout itself is the fallback, which is the usual case
    assert search[-1].endswith(dm.CLIENT_NAME_FILENAME)
    assert len(search) == len(set(search))   # no duplicate when both dirs coincide


def test_the_file_beats_a_config_entry(tmp_path):
    path = tmp_path / dm.CLIENT_NAME_FILENAME
    path.write_text('From The File <file@example.com>', encoding='utf-8')

    found = dm.find_client_name([str(path)], config_value='From The Config <cfg@example.com>')

    assert found == 'From The File <file@example.com>'


def test_a_config_entry_still_works_when_there_is_no_file(tmp_path):
    """Back-compat: setups that predate the separate file must keep working."""
    found = dm.find_client_name([str(tmp_path / dm.CLIENT_NAME_FILENAME)],
                                config_value='From The Config <cfg@example.com>')

    assert found == 'From The Config <cfg@example.com>'


def test_nothing_anywhere_returns_none(tmp_path):
    assert dm.find_client_name([str(tmp_path / dm.CLIENT_NAME_FILENAME)], config_value=None) is None
    assert dm.find_client_name([str(tmp_path / dm.CLIENT_NAME_FILENAME)], config_value='') is None


def test_the_missing_message_names_a_real_path_and_the_fallback(tmp_path):
    search = dm.client_name_search_path(str(tmp_path / 'config.yaml'))
    message = dm.missing_client_name_message(search)

    assert dm.CLIENT_NAME_FILENAME in message
    assert 'gitignored' in message
    assert 'dvb_client_name:' in message   # tells you the config entry still works


def test_config_without_a_client_name_entry_starts(qapp, tmp_path):
    """
    The whole point: a config file you can commit. This used to raise
    'required entry `dvb_client_name` not found'.
    """
    import yaml
    (tmp_path / dm.CLIENT_NAME_FILENAME).write_text('Me <me@example.com>', encoding='utf-8')

    config = {
        'stops_to_monitor': ['Altmarkt'],
        'num_stops_per_page': 1,
        'num_rows_per_table': 6,
        'window_width': 1300,
        'window_height': 900,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': False,
        'verbosity': 0,
    }
    path = tmp_path / 'no_client_name.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())

    assert monitor.dvb_client_name == 'Me <me@example.com>'


def test_the_file_beside_the_config_wins_end_to_end(qapp, tmp_path):
    import yaml
    (tmp_path / dm.CLIENT_NAME_FILENAME).write_text('File Wins <file@example.com>', encoding='utf-8')

    config = {
        'dvb_client_name': 'Config Loses <cfg@example.com>',
        'stops_to_monitor': ['Altmarkt'],
        'num_stops_per_page': 1,
        'num_rows_per_table': 6,
        'window_width': 1300,
        'window_height': 900,
        'css_file': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'style.css'),
        'fetch_in_background': False,
        'verbosity': 0,
    }
    path = tmp_path / 'both.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())

    assert monitor.dvb_client_name == 'File Wins <file@example.com>'


def test_shipped_configs_carry_no_contact_details():
    """
    Every committed config must be safe to commit and to share. This is the regression that
    started all of it -- one of them used to ship a blank dvb_client_name placeholder.
    """
    import glob, yaml

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shipped = glob.glob(os.path.join(root, 'config*.yaml'))

    assert shipped, 'expected some committed config files'

    for path in shipped:
        loaded = yaml.safe_load(open(path, encoding='utf-8')) or {}
        assert 'dvb_client_name' not in loaded, (
            f'{os.path.basename(path)} carries a dvb_client_name; it belongs in '
            f'{dm.CLIENT_NAME_FILENAME}, which is gitignored')


def test_the_client_name_file_is_gitignored():
    """A file we tell people to create must actually be ignored, or this all backfires."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ignored = open(os.path.join(root, '.gitignore'), encoding='utf-8').read()

    assert dm.CLIENT_NAME_FILENAME in ignored


# --------------------------------------------------------------------------------------
# the shipped defaults and configs must fit their own windows
# --------------------------------------------------------------------------------------

def shipped_config_paths():
    import glob
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return sorted(glob.glob(os.path.join(root, 'config*.yaml')))


def build_from_settings(qapp, tmp_path, name, settings):
    """Build a monitor from a settings dict, neutralising anything hardware-specific."""
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    settings = dict(settings)
    settings.update(
        use_backlight_control=False,   # no backlight device under pytest
        is_full_screen=False,
        is_touch=False,
        fetch_in_background=False,
        verbosity=0,
    )
    settings.setdefault('css_file', 'style.css')
    # css_file is resolved relative to the working directory; anchor it to the checkout
    if not os.path.isabs(settings['css_file']):
        settings['css_file'] = os.path.join(root, settings['css_file'])

    (tmp_path / dm.CLIENT_NAME_FILENAME).write_text('Me <me@example.com>', encoding='utf-8')

    path = tmp_path / f'{name}.yaml'
    path.write_text(yaml.safe_dump(settings), encoding='utf-8')
    return build_monitor(qapp, str(path), FakeClient())


def test_the_built_in_defaults_produce_no_size_warnings(qapp, tmp_path):
    """
    --generate-config used to emit a config whose "Mins" column was too narrow for the word
    "Mins", and whose grid was 490px wide in a 480px window.
    """
    monitor = build_from_settings(qapp, tmp_path, 'defaults', dm.DEFAULT_CONFIG)
    table = monitor.tables[0]

    assert table.narrow_columns() == []
    assert table.width() <= monitor.width
    assert table.total_height() <= monitor.height


@pytest.mark.parametrize('config_path', shipped_config_paths(),
                         ids=lambda p: os.path.basename(p))
def test_shipped_configs_produce_no_size_warnings(qapp, tmp_path, config_path):
    import yaml

    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(yaml.safe_load(open(config_path, encoding='utf-8')) or {})

    name = os.path.splitext(os.path.basename(config_path))[0]
    monitor = build_from_settings(qapp, tmp_path, name, settings)
    table = monitor.tables[0]

    assert table.narrow_columns() == [], f'{name}: columns too narrow for their headings'
    assert table.width() <= monitor.width, f'{name}: grid wider than the window'
    assert table.total_height() <= monitor.height, f'{name}: grid taller than the window'


def test_width_validation_counts_the_gaps_between_column_groups(qapp, tmp_path):
    """
    validate_config used to ignore column_group_spacing, so it approved configs whose grid was
    genuinely wider than the window -- which is how the bad default shipped.
    """
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(
        num_departures_to_monitor=12,
        num_rows_per_table=6,          # -> two column groups, so one gap
        column_group_spacing=200,      # gaps alone now overflow the window
        window_width=480,
    )

    with pytest.raises(SystemExit):
        build_from_settings(qapp, tmp_path, 'gappy', settings)


def test_a_config_that_fits_exactly_is_accepted(qapp, tmp_path):
    """The gap accounting must not be off by one in the rejecting direction."""
    settings = dict(dm.DEFAULT_CONFIG)
    columns = [{'header': 'A', 'width': 100, 'getter': 'get_line', 'alignment': 'left'}]
    settings.update(
        columns=columns,
        num_departures_to_monitor=12,
        num_rows_per_table=6,          # two groups
        column_group_spacing=20,
        window_width=220,              # 100 + 100 + 20, exactly
    )

    monitor = build_from_settings(qapp, tmp_path, 'exact', settings)

    assert monitor.tables[0].width() == 220
