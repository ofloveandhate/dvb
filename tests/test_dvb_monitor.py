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
        'fetch_in_background': False,
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent   # synchronous, so assertions can run right after refresh()
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
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent
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
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent
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
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent
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
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent
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
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent
        'verbosity': 0,
    }
    config.update(overrides)

    path = tmp_path / f'{name}.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    return str(path)


def css_with_cell_font(pixels):
    """
    Deliberately px rather than pt: pt is converted to pixels using the screen's DPI, which
    would make every pixel assertion below depend on whatever machine the suite runs on.
    """
    return f'''
QLabel[class="grid_cell"] {{
    font-size: {pixels}px;
    color: #ffffff;
    background-color: #1a1a1a;
    border: 1px solid #2a2a2a;
    padding: 2px;
}}
QLabel[class="grid_header"] {{ font-size: {pixels}px; }}
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
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent
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
        'scale_with_screen_dpi': False,   # keep pixel assertions DPI-independent
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


def test_backups_of_the_client_name_file_are_gitignored():
    """
    Editors and hand-made backups leave dvb_client_name.txt.bak / .save / ~ next to the real
    one.  An exact-filename ignore leaves those staged by `git add -A`, which leaks exactly the
    contact details this whole arrangement exists to keep out of the repo.
    """
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for suffix in ('.bak', '.save', '~', '.orig'):
        candidate = dm.CLIENT_NAME_FILENAME + suffix
        result = subprocess.run(['git', 'check-ignore', '-q', candidate],
                                cwd=root, capture_output=True)
        assert result.returncode == 0, f'{candidate} is not gitignored'


# --------------------------------------------------------------------------------------
# the shipped defaults and configs must fit their own windows
# --------------------------------------------------------------------------------------

def shipped_config_paths():
    import glob
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return sorted(glob.glob(os.path.join(root, 'config*.yaml')))


def build_from_settings(qapp, tmp_path, name, settings, scale_with_dpi=False):
    """
    Build a monitor from a settings dict, neutralising anything hardware-specific.

    scale_with_dpi defaults off so pixel assertions mean what they say on any screen. Tests
    about whether real usage warns should pass True, since that is what actually happens.
    """
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    settings = dict(settings)
    settings.update(
        use_backlight_control=False,   # no backlight device under pytest
        is_full_screen=False,
        is_touch=False,
        fetch_in_background=False,
        scale_with_screen_dpi=scale_with_dpi,
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
    monitor = build_from_settings(qapp, tmp_path, 'defaults', dm.DEFAULT_CONFIG,
                                  scale_with_dpi=True)
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
    monitor = build_from_settings(qapp, tmp_path, name, settings, scale_with_dpi=True)
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


# --------------------------------------------------------------------------------------
# high-DPI screens
# --------------------------------------------------------------------------------------

class FakeScreen:
    def __init__(self, dpi):
        self._dpi = dpi

    def logicalDotsPerInch(self):
        return self._dpi


def test_scale_is_one_on_an_ordinary_screen():
    assert dm.layout_scale_factor(FakeScreen(96)) == 1.0


def test_scale_follows_the_screen_dpi():
    """
    Font sizes are in pt, which Qt scales by screen DPI; the layout is in px, which it does not.
    On a 192dpi screen that made rows overflow and destinations get truncated.
    """
    assert dm.layout_scale_factor(FakeScreen(192)) == 2.0
    assert dm.layout_scale_factor(FakeScreen(144)) == 1.5


def test_scale_can_be_turned_off():
    assert dm.layout_scale_factor(FakeScreen(192), enabled=False) == 1.0


def test_scale_survives_a_nonsense_screen():
    assert dm.layout_scale_factor(None) == 1.0
    assert dm.layout_scale_factor(FakeScreen(0)) == 1.0
    assert dm.layout_scale_factor(FakeScreen(-5)) == 1.0
    # and it refuses to produce an unusable window
    assert dm.layout_scale_factor(FakeScreen(10000)) <= 4.0
    assert dm.layout_scale_factor(FakeScreen(1)) >= 0.5


def test_scale_survives_a_screen_that_raises():
    class Broken:
        def logicalDotsPerInch(self):
            raise RuntimeError('no screen')

    assert dm.layout_scale_factor(Broken()) == 1.0


@pytest.mark.parametrize('dpi,expected', [(96, 1.0), (144, 1.5), (192, 2.0)])
def test_pixel_sizes_scale_with_the_screen(qapp, tmp_path, dpi, expected, monkeypatch):
    """Config pixel sizes are written for a 96dpi screen and scaled to the real one."""
    monkeypatch.setattr(dm, 'layout_scale_factor',
                        lambda screen, reference_dpi=96.0, enabled=True: expected if enabled else 1.0)

    settings = dict(dm.DEFAULT_CONFIG)
    settings['scale_with_screen_dpi'] = True
    settings.update(use_backlight_control=False, is_full_screen=False, is_touch=False,
                    fetch_in_background=False, verbosity=0)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    settings['css_file'] = os.path.join(root, 'style.css')
    (tmp_path / dm.CLIENT_NAME_FILENAME).write_text('Me <me@example.com>', encoding='utf-8')

    import yaml
    path = tmp_path / f'dpi{dpi}.yaml'
    path.write_text(yaml.safe_dump(settings), encoding='utf-8')

    monitor = build_monitor(qapp, str(path), FakeClient())

    assert monitor.layout_scale == expected
    assert monitor.width == round(dm.DEFAULT_CONFIG['window_width'] * expected)
    assert monitor.height == round(dm.DEFAULT_CONFIG['window_height'] * expected)
    assert monitor.row_height == round(dm.DEFAULT_CONFIG['row_height'] * expected)
    assert monitor.columns[0].width == round(dm.DEFAULT_CONFIG['columns'][0]['width'] * expected)
    assert monitor.column_group_spacing == round(dm.DEFAULT_CONFIG['column_group_spacing'] * expected)


# --------------------------------------------------------------------------------------
# room around the bottom buttons
# --------------------------------------------------------------------------------------

def test_the_button_row_has_room_around_and_between(qapp, tmp_path):
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['A', 'B', 'C'], num_stops_per_page=1,
                    button_margin=8, button_spacing=6)

    monitor = build_from_settings(qapp, tmp_path, 'buttons', settings)

    assert monitor.bottom_layout.spacing() == 6
    margins = monitor.bottom_layout.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (8, 8, 8, 8)


def test_button_room_scales_with_the_screen(qapp, tmp_path, monkeypatch):
    """
    Room around the buttons is a config pixel size, not a stylesheet one, so that it grows with
    everything else on a high-dpi screen instead of staying stuck at one size.
    """
    monkeypatch.setattr(dm, 'layout_scale_factor',
                        lambda screen, reference_dpi=96.0, enabled=True: 2.0 if enabled else 1.0)

    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(button_margin=8, button_spacing=6, scale_with_screen_dpi=True)

    monitor = build_from_settings(qapp, tmp_path, 'buttons_hidpi', settings, scale_with_dpi=True)

    assert monitor.button_margin == 16
    assert monitor.button_spacing == 12
    assert monitor.bottom_layout.spacing() == 12


def test_the_defaults_fit_the_whole_window_not_just_the_table(qapp, tmp_path):
    """
    The grid fitting is not enough: the stop name, the timestamp and the button row all take
    height too.  Adding room around the buttons pushed the contents past the bottom edge, and
    the table-only check said nothing.
    """
    monitor = build_from_settings(qapp, tmp_path, 'whole_defaults', dm.DEFAULT_CONFIG,
                                  scale_with_dpi=True)
    needed = monitor.centralWidget().sizeHint()

    assert needed.height() <= monitor.height
    assert needed.width() <= monitor.width


@pytest.mark.parametrize('config_path', shipped_config_paths(),
                         ids=lambda p: os.path.basename(p))
def test_shipped_configs_fit_the_whole_window(qapp, tmp_path, config_path):
    import yaml

    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(yaml.safe_load(open(config_path, encoding='utf-8')) or {})

    name = os.path.splitext(os.path.basename(config_path))[0]
    monitor = build_from_settings(qapp, tmp_path, name, settings, scale_with_dpi=True)
    needed = monitor.centralWidget().sizeHint()

    assert needed.height() <= monitor.height, (
        f'{name}: contents need {needed.height()}px but window_height is {monitor.height}')
    assert needed.width() <= monitor.width, (
        f'{name}: contents need {needed.width()}px but window_width is {monitor.width}')


# --------------------------------------------------------------------------------------
# the refresh button, and waking from sleep
# --------------------------------------------------------------------------------------

def test_refresh_button_does_not_stretch_across_the_window(qapp, tmp_path):
    """
    With no stops to page between there are no nav buttons, and the refresh button used to
    spread across the entire window.
    """
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['Altmarkt'], num_stops_per_page=1, window_width=1300)

    monitor = build_from_settings(qapp, tmp_path, 'lonely_refresh', settings)
    monitor.show()
    qapp.processEvents()

    assert 'prev' not in monitor.buttons and 'next' not in monitor.buttons
    assert monitor.buttons['refresh'].width() <= monitor.refresh_button_width
    assert monitor.buttons['refresh'].width() < monitor.width / 4


def test_nav_buttons_get_the_room_the_refresh_button_does_not(qapp, tmp_path):
    """Prev/next carry stop names and should take the width; the glyph should not."""
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['Altmarkt', 'Postplatz', 'Pirnaischer Platz'],
                    num_stops_per_page=1, window_width=1300)

    monitor = build_from_settings(qapp, tmp_path, 'nav_room', settings)
    monitor.show()
    qapp.processEvents()

    assert monitor.buttons['prev'].width() > monitor.buttons['refresh'].width()
    assert monitor.buttons['next'].width() > monitor.buttons['refresh'].width()


def test_the_refresh_button_never_clips_its_glyph(qapp, tmp_path):
    """A silly-small configured width must not cut the icon in half."""
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['Altmarkt'], refresh_button_width=1)

    monitor = build_from_settings(qapp, tmp_path, 'tiny_refresh', settings)

    assert monitor.buttons['refresh'].width() >= monitor.buttons['refresh'].sizeHint().width()


def sleeping_monitor(qapp, tmp_path, client):
    """A monitor with backlight control, driven into the slept state."""
    backlight = tmp_path / 'brightness'
    backlight.write_text('1', encoding='utf-8')

    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['Altmarkt'], num_stops_per_page=1,
                    refresh_forever=False)

    monitor = build_from_settings(qapp, tmp_path, 'sleepy', settings)
    # switched on after construction, so validate_config does not demand the path up front
    monitor.use_backlight_control = True
    monitor.backlight_path = str(backlight)
    return monitor


def test_waking_from_sleep_refreshes_without_a_button_press(qapp, tmp_path):
    """
    The screen only sleeps after clear_stale_data has discarded the departures, so waking onto
    an empty table and waiting to be asked again is no use.
    """
    client = FakeClient()
    monitor = sleeping_monitor(qapp, tmp_path, client)
    monitor.client = client

    monitor.refresh()
    assert monitor.departures['Altmarkt']

    monitor.clear_stale_data()          # what the stale timer does
    assert monitor.departures == {}
    assert monitor.is_backlight_off

    woke = monitor.wake_if_sleeping()   # what a touch does

    assert woke is True                 # the waking tap is swallowed, not passed through
    assert not monitor.is_backlight_off
    assert monitor.departures['Altmarkt'], 'waking should have refetched the departures'


def test_touching_an_awake_screen_does_not_refresh(qapp, tmp_path):
    """An ordinary tap must pass through to the widget under it, not trigger a fetch."""
    client = FakeClient()
    monitor = sleeping_monitor(qapp, tmp_path, client)
    monitor.client = client
    monitor.refresh()

    calls_before = len(client.monitor_calls)
    woke = monitor.wake_if_sleeping()

    assert woke is False
    assert len(client.monitor_calls) == calls_before


def test_wake_is_a_no_op_without_backlight_control(qapp, tmp_path):
    client = FakeClient()
    monitor = sleeping_monitor(qapp, tmp_path, client)
    monitor.client = client
    monitor.use_backlight_control = False
    monitor.is_backlight_off = True     # cannot really happen, but must stay harmless

    assert monitor.wake_if_sleeping() is False


# --------------------------------------------------------------------------------------
# show_infinite_arrivals
# --------------------------------------------------------------------------------------

from fake_dvb_client import FakeDeparture


def mixed_departures(real=3, unknown=2):
    """Some departures the API timed, and some it did not."""
    timed = sample_departures(real)
    untimed = [FakeDeparture(line=str(90 + i), direction=f'Unknown {i}', real_time=None)
               for i in range(unknown)]
    return timed + untimed


def minutes_column(monitor, table_index=0):
    """The text of every non-empty cell in the minutes column."""
    col = next(i for i, c in enumerate(monitor.columns) if c.getter is dm.get_minutes)
    table = monitor.tables[table_index]
    values = [table.labels[(row, col)].text() for row in range(monitor.num_rows_per_table)]
    return [v for v in values if v]


def build_with_departures(qapp, tmp_path, name, departures, **overrides):
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['Altmarkt'], num_stops_per_page=1)
    settings.update(overrides)

    monitor = build_from_settings(qapp, tmp_path, name, settings)
    monitor.client = FakeClient(default=list(departures))
    monitor.refresh()
    return monitor


def test_show_infinite_arrivals_defaults_to_true():
    assert dm.DEFAULT_CONFIG['show_infinite_arrivals'] is True


def test_infinite_arrivals_are_shown_by_default(qapp, tmp_path):
    monitor = build_with_departures(qapp, tmp_path, 'inf_shown', mixed_departures())

    assert monitor.show_infinite_arrivals is True
    assert 'inf' in minutes_column(monitor)


def test_infinite_arrivals_can_be_hidden(qapp, tmp_path):
    monitor = build_with_departures(qapp, tmp_path, 'inf_hidden', mixed_departures(),
                                    show_infinite_arrivals=False)

    shown = minutes_column(monitor)

    assert 'inf' not in shown
    assert shown == ['2', '5', '8']   # the timed ones, still in order


def test_hiding_them_leaves_the_timed_departures_alone(qapp, tmp_path):
    """Filtering must not drop or reorder anything the API actually gave a time for."""
    with_them = build_with_departures(qapp, tmp_path, 'with', mixed_departures(4, 3))
    without   = build_with_departures(qapp, tmp_path, 'without', mixed_departures(4, 3),
                                      show_infinite_arrivals=False)

    timed = [v for v in minutes_column(with_them) if v != 'inf']

    assert minutes_column(without) == timed


def test_hiding_them_when_every_arrival_is_untimed(qapp, tmp_path):
    """An empty table, not a crash."""
    monitor = build_with_departures(qapp, tmp_path, 'all_inf', mixed_departures(0, 4),
                                    show_infinite_arrivals=False)

    assert minutes_column(monitor) == []


def test_showing_them_when_every_arrival_is_untimed(qapp, tmp_path):
    monitor = build_with_departures(qapp, tmp_path, 'all_inf_shown', mixed_departures(0, 4))

    assert minutes_column(monitor) == ['inf'] * 4


def test_hidden_infinite_arrivals_free_up_the_rows_they_occupied(qapp, tmp_path):
    """
    Untimed arrivals sort to the bottom, so they never push a timed one off the end -- but with
    more rows than timed departures they do take up the rows that are left.
    """
    departures = mixed_departures(real=4, unknown=4)
    common = dict(num_rows_per_table=6, num_departures_to_monitor=6)

    shown  = minutes_column(build_with_departures(qapp, tmp_path, 'budget_shown', departures,
                                                  **common))
    hidden = minutes_column(build_with_departures(qapp, tmp_path, 'budget_hidden', departures,
                                                  show_infinite_arrivals=False, **common))

    assert shown == ['2', '5', '8', '11', 'inf', 'inf']   # two rows spent on untimed arrivals
    assert hidden == ['2', '5', '8', '11']                # those rows now simply empty


# --------------------------------------------------------------------------------------
# direction arrows
# --------------------------------------------------------------------------------------

from fake_dvb_client import ORIGIN_LAT, ORIGIN_LNG, coords as fake_coords


@pytest.fixture(autouse=True)
def _clear_direction_caches():
    """The arrow caches are module level, so one test must not leak into the next."""
    dm._direction_cache.clear()
    dm._direction_by_stop.clear()
    dm.use_direction_arrows_for(None)
    yield
    dm._direction_cache.clear()
    dm._direction_by_stop.clear()
    dm.use_direction_arrows_for(None)


def street_departures(specs):
    """specs is [(trip_id, line, direction)], all trams."""
    now = datetime.now(timezone.utc)
    return [FakeDeparture(line=line, direction=direction, mode='Tram',
                          real_time=now + timedelta(minutes=2 + i), id=trip_id)
            for i, (trip_id, line, direction) in enumerate(specs)]


ORIGIN = fake_coords(ORIGIN_LAT, ORIGIN_LNG)


@pytest.mark.parametrize('dlat,dlng,expected', [
    ( 0.01,  0.00, '↑'),   # due north
    ( 0.00,  0.01, '→'),   # due east
    (-0.01,  0.00, '↓'),   # due south
    ( 0.00, -0.01, '←'),   # due west
    ( 0.01,  0.016, '↗'),  # north east
])
def test_bearing_becomes_the_right_arrow(dlat, dlng, expected):
    to = fake_coords(ORIGIN_LAT + dlat, ORIGIN_LNG + dlng)
    assert dm.arrow_for_bearing(dm.bearing_degrees(ORIGIN, to)) == expected


def test_bearing_wraps_around_north():
    assert dm.arrow_for_bearing(359.9) == '↑'
    assert dm.arrow_for_bearing(0.1) == '↑'
    assert dm.arrow_for_bearing(-90) == '←'


def test_arrow_is_blank_until_we_know_it():
    assert dm.get_direction_arrow(FakeDeparture(id='unknown-trip')) == ''
    assert dm.get_direction_arrow(object()) == ''


def test_one_lookup_serves_every_departure_of_a_route():
    """The whole point: fifteen departures must not cost fifteen calls."""
    client = FakeClient()
    departures = street_departures([
        ('t1', '9', 'Kaditz'), ('t2', '9', 'Kaditz'), ('t3', '9', 'Kaditz'),
        ('t4', '2', 'Gorbitz'), ('t5', '2', 'Gorbitz'),
    ])

    arrows, used = dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures,
                                               budget=10, recompute_interval=3600)

    assert used == 2                      # two distinct (line, direction), not five departures
    assert len(client.trip_calls) == 2
    assert len(arrows) == 5               # but every departure gets an arrow
    assert len(set(arrows.values())) == 1 # all east, per the fake's default next stop


def test_a_second_refresh_costs_nothing():
    client = FakeClient()
    departures = street_departures([('t1', '9', 'Kaditz'), ('t2', '2', 'Gorbitz')])

    dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 10, 3600)
    before = len(client.trip_calls)
    arrows, used = dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 10, 3600)

    assert used == 0
    assert len(client.trip_calls) == before
    assert len(arrows) == 2               # still drawn, straight from the cache


def test_the_budget_caps_lookups_per_refresh():
    client = FakeClient()
    departures = street_departures([(f't{i}', str(i), f'Dir {i}') for i in range(10)])

    arrows, used = dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures,
                                               budget=3, recompute_interval=3600)

    assert used == 3
    assert len(client.trip_calls) == 3
    assert len(arrows) == 3               # the rest stay blank and get picked up next refresh


def test_the_budget_picks_up_where_it_left_off():
    client = FakeClient()
    departures = street_departures([(f't{i}', str(i), f'Dir {i}') for i in range(5)])

    dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 2, 3600)
    arrows, used = dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 2, 3600)

    assert used == 2
    assert len(arrows) == 4               # two from the first pass, two from this one


def test_the_same_route_at_different_stops_is_cached_separately():
    """A line leaves one stop east and another west; the stop has to be part of the key."""
    client = FakeClient(next_stop_offsets={'t1': (0.0, 0.01), 't2': (0.0, -0.01)})
    a = street_departures([('t1', '9', 'Kaditz')])
    b = street_departures([('t2', '9', 'Kaditz')])

    arrows_a, _ = dm.resolve_direction_arrows(client, '11111', ORIGIN, a, 5, 3600)
    arrows_b, _ = dm.resolve_direction_arrows(client, '22222', ORIGIN, b, 5, 3600)

    assert arrows_a['t1'] == '→'
    assert arrows_b['t2'] == '←'


def test_trains_do_not_get_arrows():
    """Express and stopping services share a line and headsign, so one cached arrow would lie."""
    client = FakeClient()
    now = datetime.now(timezone.utc)
    departures = [FakeDeparture(line='RB60', direction='Görlitz', mode='SuburbanRailway',
                                real_time=now, id='train-1'),
                  FakeDeparture(line='9', direction='Kaditz', mode='Tram',
                                real_time=now, id='tram-1')]

    arrows, used = dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 10, 3600)

    assert used == 1
    assert 'train-1' not in arrows
    assert 'tram-1' in arrows


def test_a_terminus_gets_no_arrow():
    client = FakeClient(trip_behaviour={'t1': 'terminus'})
    arrows, used = dm.resolve_direction_arrows(
        client, '33000004', ORIGIN, street_departures([('t1', '9', 'Kaditz')]), 5, 3600)

    assert arrows == {}
    assert used == 1          # we spent the lookup, and we remember the answer


def test_a_stop_missing_from_the_trip_gets_no_arrow():
    client = FakeClient(trip_behaviour={'t1': 'absent'})
    arrows, _ = dm.resolve_direction_arrows(
        client, '33000004', ORIGIN, street_departures([('t1', '9', 'Kaditz')]), 5, 3600)

    assert arrows == {}


@pytest.mark.parametrize('failure', [
    dvb.ConnectionError('503 Service Unavailable'),
    dvb.APIError('API returned status: ServiceError'),
    KeyError('Stops'),
    RuntimeError('something nobody predicted'),
])
def test_a_failed_lookup_never_breaks_anything(failure):
    client = FakeClient(trip_behaviour={'t1': failure})
    arrows, used = dm.resolve_direction_arrows(
        client, '33000004', ORIGIN, street_departures([('t1', '9', 'Kaditz')]), 5, 3600)

    assert arrows == {}
    assert used == 1


def test_resolving_never_raises_on_an_unreadable_departure():
    class Exploding:
        mode = 'Tram'
        @property
        def line(self):
            raise RuntimeError('boom')

    arrows, used = dm.resolve_direction_arrows(FakeClient(), '33000004', ORIGIN,
                                               [Exploding()], 5, 3600)
    assert arrows == {}
    assert used == 0


def test_no_coordinates_means_no_arrows_and_no_calls():
    client = FakeClient()
    arrows, used = dm.resolve_direction_arrows(
        client, '33000004', None, street_departures([('t1', '9', 'Kaditz')]), 5, 3600)

    assert arrows == {}
    assert used == 0
    assert client.trip_calls == []


def test_recompute_interval_of_zero_never_looks_again():
    client = FakeClient()
    departures = street_departures([('t1', '9', 'Kaditz')])

    dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 5, 0)
    # pretend a very long time has passed
    key = ('33000004', '9', 'Kaditz')
    arrow, _ = dm._direction_cache[key]
    dm._direction_cache[key] = (arrow, -1e9)

    _, used = dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 5, 0)

    assert used == 0


def test_a_stale_entry_is_recomputed():
    client = FakeClient()
    departures = street_departures([('t1', '9', 'Kaditz')])

    dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 5, 3600)
    key = ('33000004', '9', 'Kaditz')
    arrow, computed_at = dm._direction_cache[key]
    dm._direction_cache[key] = (arrow, computed_at - 7200)   # two hours ago

    _, used = dm.resolve_direction_arrows(client, '33000004', ORIGIN, departures, 5, 3600)

    assert used == 1


def test_a_config_without_the_arrow_column_makes_no_extra_calls(qapp, tmp_path):
    """Nobody should pay for coordinates they are not going to draw."""
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['Altmarkt'], num_stops_per_page=1)

    monitor = build_from_settings(qapp, tmp_path, 'no_arrows', settings)
    client = FakeClient()
    monitor.client = client
    monitor.refresh()
    monitor.refresh()

    assert client.trip_calls == []
    assert len(client.find_calls) == 1     # once for the stop id, never again


def test_the_arrow_column_draws_through_the_getter_registry(qapp, tmp_path):
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(
        stops_to_monitor=['Altmarkt'], num_stops_per_page=1, window_width=900,
        columns=[{'header': '#', 'width': 60, 'getter': 'get_line', 'alignment': 'center'},
                 {'header': '', 'width': 60, 'getter': 'get_direction_arrow',
                  'alignment': 'center'}])

    monitor = build_from_settings(qapp, tmp_path, 'arrow_column', settings)
    monitor.client = FakeClient(default=street_departures([('t1', '9', 'Kaditz')]))
    monitor.refresh()

    table = monitor.tables[0]
    assert table.labels[(0, 1)].text() == '→'


def test_one_trip_at_two_stops_keeps_an_arrow_for_each():
    """
    A tram runs through several of the stops you might be watching under one trip id, and leaves
    each of them in a different direction. Keyed on the trip alone, whichever stop was fetched
    last overwrote the others and the arrows flickered as each refresh landed.
    """
    departure = FakeDeparture(line='9', direction='Kaditz', mode='Tram',
                              real_time=datetime.now(timezone.utc), id='SAME-TRIP')
    client = FakeClient(next_stop_offsets={'SAME-TRIP': (0.0, 0.01)})   # east

    east, _ = dm.resolve_direction_arrows(client, 'STOP-A', ORIGIN, [departure], 5, 3600)
    client.next_stop_offsets['SAME-TRIP'] = (0.01, 0.0)                 # north
    north, _ = dm.resolve_direction_arrows(client, 'STOP-B', ORIGIN, [departure], 5, 3600)

    assert east['SAME-TRIP'] == '→'
    assert north['SAME-TRIP'] == '↑'

    # what the monitor stores, and what the getter sees while drawing each stop
    dm._direction_by_stop['Altmarkt'] = east
    dm._direction_by_stop['Postplatz'] = north

    dm.use_direction_arrows_for('Altmarkt')
    assert dm.get_direction_arrow(departure) == '→'

    dm.use_direction_arrows_for('Postplatz')
    assert dm.get_direction_arrow(departure) == '↑'


def test_each_stop_draws_its_own_arrows(qapp, tmp_path):
    """The same check through the real drawing path, with two stops on one page."""
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(
        stops_to_monitor=['Altmarkt', 'Postplatz'], num_stops_per_page=2, window_width=900,
        num_rows_per_table=4, num_departures_to_monitor=4,
        columns=[{'header': '', 'width': 60, 'getter': 'get_direction_arrow',
                  'alignment': 'center'}])

    monitor = build_from_settings(qapp, tmp_path, 'two_stops', settings)

    shared = FakeDeparture(line='9', direction='Kaditz', mode='Tram',
                           real_time=datetime.now(timezone.utc), id='SAME-TRIP')
    monitor.client = FakeClient(default=[shared],
                                next_stop_offsets={'SAME-TRIP': (0.0, 0.01)})
    monitor.refresh()

    # both tables show the same trip; both must show the arrow worked out for their own stop
    assert monitor.tables[0].labels[(0, 0)].text() == '→'
    assert monitor.tables[1].labels[(0, 0)].text() == '→'

    # now let one stop's route differ, as a turning route really would
    dm._direction_by_stop['Postplatz'] = {'SAME-TRIP': '↑'}
    monitor.rebuild()

    assert monitor.tables[0].labels[(0, 0)].text() == '→'
    assert monitor.tables[1].labels[(0, 0)].text() == '↑'


def test_arrows_for_a_stop_are_replaced_not_accumulated(qapp, tmp_path):
    """Otherwise the table grows for as long as the app runs, trip ids never being reused."""
    settings = dict(dm.DEFAULT_CONFIG)
    settings.update(stops_to_monitor=['Altmarkt'], num_stops_per_page=1,
                    columns=[{'header': '', 'width': 60, 'getter': 'get_direction_arrow',
                              'alignment': 'center'}])
    monitor = build_from_settings(qapp, tmp_path, 'replaced', settings)

    now = datetime.now(timezone.utc)
    monitor.client = FakeClient(default=[FakeDeparture(mode='Tram', real_time=now, id='trip-a')])
    monitor.refresh()
    monitor.client = FakeClient(default=[FakeDeparture(mode='Tram', real_time=now, id='trip-b')])
    monitor.refresh()

    assert set(dm._direction_by_stop['Altmarkt']) == {'trip-b'}
