import sys
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QGroupBox, QDialog, QVBoxLayout, QGridLayout, QMainWindow, QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QShortcut
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtCore import pyqtSlot, QTimer, QThread, pyqtSignal
from PyQt5.QtCore import Qt

from PyQt5.QtGui import QPainter, QFontMetrics

import dvb
from datetime import datetime, timezone, timedelta

import argparse
from collections import namedtuple

import numpy as np # for infinity


import os
import copy
import math
import time
import traceback

DEFAULT_CONFIG = {
    "stops_to_monitor": ["Altmarkt"],
    "row_height": 30,
    "num_rows_per_table": 6,
    "num_stops_per_page": 1,
    "consecutive_autorefresh_timeout_threshold": 10,
    "refresh_interval": 60,
    "clear_interval": 120,
    "window_width": 480,
    "window_height": 340,   # 329 is the content with 8px button margins; this leaves a little slack
    "window_loc_x": 0,
    "window_loc_y": 0,
    "mock_update": False,
    "window_title": "DVB Local Stop Monitor",
    "num_departures_to_monitor":12,

    # a departure the API gives no real time for gets an infinite number of minutes, which sorts
    # it to the bottom of the table and shows as "inf".  set this false to leave those out.
    "show_infinite_arrivals": True,
    "verbosity": 0,
    "refresh_forever": False,

    # widths are in pixels, and have to fit the window: with num_departures_to_monitor=12 and
    # num_rows_per_table=6 these are laid out as two groups side by side, so the budget is
    # (window_width - column_group_spacing) / 2 = (480 - 20) / 2 = 230 per group.  these come to
    # 228.  each is also wide enough for its own heading at the default stylesheet font, which
    # is what the startup warnings check.
    "columns": [
        {"header": "#",    "width": 32,  "getter": "get_line",        "alignment": "center", "margin_right": 0, "elide": False},
        {"header": "",     "width": 28,  "getter": "get_mode_emoji",  "alignment": "left", "margin_right": 0, "elide": False},
        {"header": "min",  "width": 40,  "getter": "get_minutes",     "alignment": "right", "margin_right": 0, "elide": False},
        {"header": "Dest", "width": 128, "getter": "get_destination", "alignment": "left", "margin_right": 0, "elide": True},
    ],

    "column_group_spacing": 20,

    # room around the row of buttons along the bottom, and between them.  in pixels, and scaled
    # with the screen like every other pixel size here -- putting it in the stylesheet instead
    # would leave it stuck at one size on a high-dpi screen while everything around it grew.
    "button_spacing": 6,
    "button_margin":  8,

    # the refresh button holds one small glyph, so it gets a fixed width and the prev/next
    # buttons -- which carry stop names -- take whatever is left.  without this the refresh
    # button stretches across the whole window whenever there are no stops to page between.
    "refresh_button_width": 64,

    "is_full_screen": False,
    "is_touch": False,
    "touch_rotation": 270,
    "is_touch_calibrated": False,
    "touch_raw_x_min": 46,
    "touch_raw_x_max": 434,
    "touch_raw_y_min": 22,
    "touch_raw_y_max": 287,

    "backlight_path":        "/sys/class/backlight/soc:backlight/brightness",
    "backlight_max":         1,    # ADD THIS so it's configurable
    "use_backlight_control": False,

    "css_file": "style.css",

    # pixel sizes in a config are written for a REFERENCE_DPI screen and scaled to whatever you
    # actually have, so the layout keeps up with the pt font sizes in the stylesheet.
    # set scale_with_screen_dpi to false to take the numbers literally.
    "scale_with_screen_dpi": True,
    "reference_dpi":         96,

    # --- network robustness ---
    "request_timeout":          8,     # seconds to wait for the response body.  dvb's own default is 15.
    "request_connect_timeout":  4,     # seconds to wait for the tcp connection
    "cache_stop_ids":           True,  # resolve stop name -> id once.  halves the number of requests.
    "retry_backoff_factor":     2.0,   # after a total failure, wait interval * factor**n before retrying
    "retry_backoff_max":        600,   # seconds.  cap on the backoff.
    "retry_when_stale":         False, # keep retrying (slowly) even after stale data has been cleared
    "fetch_in_background":      True,  # fetch off the gui thread, so the ui never freezes

    # --- what to show when the api misbehaves ---
    "stale_data_threshold":     300,   # seconds.  data older than this is marked stale.
    "show_stale_data_on_error": True,  # keep the last good departures on screen when a fetch fails
    "error_placeholder":        "—",   # drawn in a cell whose getter raised
    "unknown_mode_emoji":       "",    # drawn for a mode of transit we have no emoji for

    # there is no default dvb client name, i want my user to have to make the entry themselves, so they don't use my email address.
}






occupancy_emoji = {
    'StandingOnly': '🕴️',
    'ManySeats': '💺',
    'Unknown': ''
}

mode_emoji = {
    'Tram': '🚋',
    'CityBus': '🚌',
    'PlusBus': '🚎',
    'IntercityBus': '🚍',
    # the dvb api returns these too; without them we used to fall into the broken except branch
    'SuburbanRailway': '🚆',
    'Train': '🚆',
    'Ferry': '⛴️',
    'Cableway': '🚡',
    'HailedSharedTaxi': '🚕',
    '': '',
}

# set from config in setup_from_yaml.  the getters are module-level functions reached through
# GETTER_REGISTRY, so they have no `self` to read config from.
UNKNOWN_MODE_EMOJI = ''

# so an unknown mode gets reported once, not once per refresh, forever.
_warned_modes = set()



Column = namedtuple("Column", ["header", "width", "getter", "alignment", "margin_right", "elide"])

# DVB asks every client to identify itself with a name and contact address.  that is personal
# information, so it does NOT belong in a config file you commit -- keep it in this file instead,
# which is gitignored.
CLIENT_NAME_FILENAME = 'dvb_client_name.txt'


# Every pixel size in a config file -- window size, row height, column widths -- is written as if
# the screen were REFERENCE_DPI.  Font sizes in the stylesheets are in pt, which Qt converts to
# pixels using the screen's DPI, so on a high-DPI screen the text doubles while a hard-coded pixel
# width does not.  That mismatch is what made rows overflow and destinations get truncated.
# Scaling the pixel sizes by the same factor keeps the two in proportion at any DPI.
REFERENCE_DPI = 96.0


def layout_scale_factor(screen, reference_dpi=REFERENCE_DPI, enabled=True):
    """how much to multiply config pixel sizes by, for this screen.  1.0 on an ordinary display."""
    if not enabled or screen is None:
        return 1.0

    try:
        dpi = float(screen.logicalDotsPerInch())
    except Exception:
        return 1.0

    if dpi <= 0 or reference_dpi <= 0:
        return 1.0

    scale = dpi / reference_dpi

    # a screen claiming something absurd shouldn't produce an unusable window
    return min(max(scale, 0.5), 4.0)


def read_client_name_file(path):
    """the first meaningful line of a client name file, or None if there isn't one."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    return line
    except OSError:
        return None

    return None


def client_name_search_path(config_path):
    """
    where to look for dvb_client_name.txt, most specific first: beside the config file being
    used, then beside this script.  the second is the usual case -- one file in your checkout,
    shared by every config you keep there.
    """
    candidates = []

    config_dir = os.path.dirname(os.path.abspath(config_path)) if config_path else None
    script_dir = os.path.dirname(os.path.abspath(__file__))

    for directory in (config_dir, script_dir):
        if directory and directory not in candidates:
            candidates.append(directory)

    return [os.path.join(directory, CLIENT_NAME_FILENAME) for directory in candidates]


def find_client_name(search_path, config_value=None, verbosity=0):
    """
    resolve the DVB client name.  a dvb_client_name.txt anywhere on the search path wins;
    a dvb_client_name entry in the config file is honoured as a fallback, for setups that
    predate the separate file.  returns None if nothing supplied one.
    """
    for path in search_path:
        name = read_client_name_file(path)
        if name:
            if verbosity >= 1:
                print(f'ℹ️ using the DVB client name from {path}')
            return name

    if config_value:
        if verbosity >= 1:
            print('ℹ️ using the DVB client name from your config file')
        return config_value

    return None


def missing_client_name_message(search_path):
    preferred = search_path[-1] if search_path else CLIENT_NAME_FILENAME

    return (
        "no DVB client name found.\n"
        "\n"
        "  DVB asks every client to identify itself with a name and contact address.\n"
        "  It is yours, so it is kept out of the repo rather than in a config file.\n"
        "\n"
        "  Create it with:\n"
        f"      echo 'DVB Monitor - your name <you@example.com>' > {preferred}\n"
        "\n"
        f"  ({CLIENT_NAME_FILENAME} is gitignored, so it will not be committed.)\n"
        "  A dvb_client_name: entry in your config file also still works."
    )


# the border and padding the stylesheet asks of grid cells.  used to work out how tall a row has
# to be for a given font, so that raising font-size in the css doesn't clip the text.
# keep these in step with the `border` and `padding` in style.css.
CELL_BORDER  = 1
CELL_PADDING = 2

def get_line(departure):
    return getattr(departure, 'line', '?')

def get_mode_emoji(departure):
    """
    map the departure's mode of transit onto an emoji.

    never raises.  an unrecognized mode gets the configured placeholder, and is reported once.
    """
    mode = getattr(departure, 'mode', '') or ''

    if mode not in mode_emoji and mode not in _warned_modes:
        _warned_modes.add(mode)
        print(f'ℹ️ no emoji known for mode {mode!r}, using {UNKNOWN_MODE_EMOJI!r}')

    return mode_emoji.get(mode, UNKNOWN_MODE_EMOJI)

def get_line_w_mode(departure):
    line = get_line(departure)
    e = get_mode_emoji(departure)

    return f'{line} {e}'
    

def get_destination(departure):
    return getattr(departure, 'direction', '?')

def get_minutes(departure):
    """
    compute the number of minutes, rounded down via integer arithmetic, to departure.

    problem: if the real_time is none, then this may fail.  so use a try/except around this.
    """

    try:
        real_time = getattr(departure, 'real_time', None)

        if not real_time:
            return np.inf

        # real dvb data is always tz-aware.  this is purely defensive, so that a naive datetime
        # sinks to the bottom of the sort instead of raising and killing the whole refresh.
        if real_time.tzinfo is None:
            real_time = real_time.replace(tzinfo=timezone.utc)

        minutes = int((real_time - datetime.now(timezone.utc)).total_seconds() // 60 + 1 )
        # adding +1 to make match the iphone app
    except Exception as e:
        print(f'⚠️ could not compute minutes to departure: {type(e).__name__}: {e}')
        return np.inf

    return minutes


# Define all possible getter functions
GETTER_REGISTRY = {
    "get_line"        : get_line,
    "get_mode_emoji"  : get_mode_emoji,
    "get_line_w_mode" : get_line_w_mode,
    "get_destination" : get_destination,
    "get_minutes"     : get_minutes,
}

ALIGNMENT_REGISTRY = {
    "left"   : Qt.AlignLeft    | Qt.AlignBottom,
    "right"  : Qt.AlignRight   | Qt.AlignBottom,
    "center" : Qt.AlignHCenter | Qt.AlignBottom,
}




# what one attempt at one stop produced.  `error` is None on success.
# immutable on purpose: it gets handed across the thread boundary in the background fetcher.
FetchResult = namedtuple("FetchResult", ["stop_name", "departures", "stop_id", "error", "duration"])


def _short_err(e, limit=48):
    """
    boil an exception down to something that fits in a footer on a 480px screen.

    requests tacks ' for url: https://...' onto its messages, which is pure noise here: there
    is only one api, and the whole string then overflows the widget.
    """
    text = ' '.join(str(e).split())

    for marker in (' for url:', ' (Caused by'):
        if marker in text:
            text = text.split(marker)[0]

    if len(text) > limit:
        text = text[:limit - 1] + '…'

    return text


def fetch_departures_for_stop(client, stop_name, stop_id=None, verbosity=0):
    """
    fetch and sort the departures for one stop.

    NEVER raises.  always returns a FetchResult, so that one sick stop can't take down the
    refresh for the others, and so an api hiccup can't kill the app from inside a qt slot.

    this takes no `self` and mutates nothing, which is what makes it safe to call from the
    background fetcher thread.
    """
    t_start = time.monotonic()

    def failed(msg):
        return FetchResult(stop_name, None, stop_id, msg, time.monotonic() - t_start)

    try:
        # resolve the name to a numeric id ourselves, so it can be cached by the caller.  dvb's
        # monitor() would otherwise do this lookup internally on every single call -- that's two
        # http requests per stop per refresh instead of one.
        if not stop_id:
            try:
                stop_id = client._resolve_stop_id(stop_name)
            except AttributeError:
                stop_id = None   # private api gone in some future dvb; fall back to the name

        departures = client.monitor(stop=stop_id or stop_name, limit=0)

        if not isinstance(departures, list):
            return failed(f'unexpected response type {type(departures).__name__}')

        departures = list(departures)

        # sorting lives in here, inside the guard, so a malformed departure can't kill the refresh
        departures.sort(key=get_minutes)

        return FetchResult(stop_name, departures, stop_id, None, time.monotonic() - t_start)

    # these two subclass dvb.DVBError, so they must be caught before the bare Exception below.
    # referenced qualified, because a bare `from dvb import ConnectionError` shadows the builtin.
    except dvb.ConnectionError as e:
        return failed(f'network: {_short_err(e)}')
    except dvb.APIError as e:
        return failed(f'api: {_short_err(e)}')
    except Exception as e:
        # the bare catch is required, not lazy: dvb only wraps requests exceptions.  the r.json()
        # call and its date parsing sit outside that wrapping, so JSONDecodeError, KeyError and
        # ValueError all escape it untouched.
        if verbosity >= 2:
            traceback.print_exc()
        return failed(f'{type(e).__name__}: {_short_err(e)}')








from PyQt5.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout
from PyQt5.QtCore import QObject, QEvent, Qt
from PyQt5.QtGui import QMouseEvent, QCursor
from PyQt5.QtCore import QObject, QEvent, QPoint, Qt

def transform_coords(x, y, rotation, w, h):
    if rotation == 90:
        return y, w - x
    elif rotation == 180:
        return w - x, h - y
    elif rotation == 270:
        return h - y, x
    else:
        return x, y

def transform_coords_calibrated(x, y, w, h, x_min, x_max, y_min, y_max):
    nx = (x - x_min) / (x_max - x_min)
    ny = (y - y_min) / (y_max - y_min)
    screen_x = int((1.0 - ny) * w)
    screen_y = int(nx * h)
    return screen_x, screen_y


class TouchFilter(QObject):
    def __init__(self, app, ROTATION, SCREEN_W, SCREEN_H,
                 is_calibrated=False,
                 x_min=0, x_max=1, y_min=0, y_max=1):
        super().__init__()
        self.app           = app
        self.ROTATION      = ROTATION
        self.SCREEN_W      = SCREEN_W
        self.SCREEN_H      = SCREEN_H
        self.processing    = False
        self.is_calibrated = is_calibrated
        self.x_min         = x_min
        self.x_max         = x_max
        self.y_min         = y_min
        self.y_max         = y_max
        self.wake_callback = None  # set this to backlight_on function

    def _transform(self, x, y):
        if self.is_calibrated:
            return transform_coords_calibrated(
                x, y,
                self.SCREEN_W, self.SCREEN_H,
                self.x_min, self.x_max,
                self.y_min, self.y_max,
            )
        else:
            return transform_coords(x, y, self.ROTATION, self.SCREEN_W, self.SCREEN_H)

    def eventFilter(self, obj, event):
        if self.processing:
            return False

        if event.type() in (
            QEvent.MouseButtonPress,
            QEvent.MouseButtonRelease,
            QEvent.MouseMove,
        ):
            # if screen is off, any touch just wakes it up and blocks the event
            if self.wake_callback and self.wake_callback():
                return True  # block the event, just wake

            raw_x = event.globalPos().x()
            raw_y = event.globalPos().y()
            new_x, new_y = self._transform(raw_x, raw_y)

            target = self.app.widgetAt(new_x, new_y)
            if target is None:
                return True

            local_pos = target.mapFromGlobal(QPoint(new_x, new_y))

            new_event = QMouseEvent(
                event.type(),
                local_pos,
                QPoint(new_x, new_y),
                event.button(),
                event.buttons(),
                event.modifiers(),
            )

            self.processing = True
            self.app.sendEvent(target, new_event)
            self.processing = False
            return True

        return False


class DepartureFetcher(QThread):
    """
    fetches stops off the gui thread, so a slow or hung api never freezes the display.

    one serial worker on purpose, not a pool: dvb.Client holds a single requests.Session, which
    isn't documented as thread safe, and serial fetching also avoids bursting the vvo api.
    if you ever want them in parallel, give each worker its OWN dvb.Client -- never share one.
    """

    stop_fetched = pyqtSignal(object)  # one FetchResult, emitted as each stop lands
    all_finished = pyqtSignal()

    def __init__(self, client, jobs, verbosity=0, parent=None):
        super().__init__(parent)
        self.client    = client
        self.jobs      = jobs       # [(stop_name, stop_id_or_None)], snapshotted on the gui thread
        self.verbosity = verbosity

    def run(self):
        for stop_name, stop_id in self.jobs:
            if self.isInterruptionRequested():
                break

            # the FetchResult carries back the resolved stop id, so the gui thread can cache it
            self.stop_fetched.emit(
                fetch_departures_for_stop(self.client, stop_name, stop_id, self.verbosity))

        self.all_finished.emit()


class StopDisplay(QWidget):
    def __init__(self, columns, num_rows, num_cols_needed, row_height, column_group_spacing=0):
        super().__init__()
        self.columns              = columns
        self.num_rows             = num_rows
        self.num_cols_needed      = num_cols_needed
        self.row_height           = row_height   # a minimum; the font may need more
        self.column_group_spacing = column_group_spacing
        self.labels               = {}
        self.sized_widgets        = []   # (widget, width) for everything on the fixed grid
        self.effective_row_height = row_height

        self.grid = QGridLayout()
        self.grid.setSpacing(0)
        self.grid.setHorizontalSpacing(0)
        self.grid.setVerticalSpacing(0)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.grid)

        self._build_grid()

    def _build_grid(self):
        total_w = sum(col.width + col.margin_right for col in self.columns) * self.num_cols_needed
        total_w += self.column_group_spacing * (self.num_cols_needed - 1)
        self.setFixedWidth(total_w)

        grid_col = 0  # track grid column manually

        for col_group in range(self.num_cols_needed):
            for col_ind, col in enumerate(self.columns):

                # header
                header = ElidedLabel(col.header) if col.elide else QLabel(col.header)
                header.setAlignment(Qt.AlignCenter)
                header.setFixedWidth(col.width)
                header.setProperty('class', 'grid_header')
                self.grid.addWidget(header, 0, grid_col)
                self.sized_widgets.append((header, col.width))

                # data rows
                for row in range(self.num_rows):
                    label = ElidedLabel('?') if col.elide else QLabel('?')
                    label.setAlignment(col.alignment)
                    label.setFixedWidth(col.width)
                    label.setProperty('class', 'grid_cell')
                    self.grid.addWidget(label, row + 1, grid_col)
                    self.labels[(row, grid_col)] = label
                    self.sized_widgets.append((label, col.width))

                grid_col += 1

            # insert a spacer column between groups (not after the last one)
            is_last_group = (col_group == self.num_cols_needed - 1)
            if not is_last_group and self.column_group_spacing > 0:
                for row in range(self.num_rows + 1):  # +1 for header
                    spacer = QWidget()
                    spacer.setFixedWidth(self.column_group_spacing)
                    self.grid.addWidget(spacer, row, grid_col)
                    self.sized_widgets.append((spacer, self.column_group_spacing))
                grid_col += 1

        self.apply_row_height()

    def apply_row_height(self):
        """
        size the rows to the stylesheet's font.

        `row_height` from config.yaml is a MINIMUM, not a cap.  bumping font-size in the css
        used to just guillotine the text, because every cell was pinned to row_height and the
        text is drawn bottom-aligned.  now the rows grow to fit whatever the font needs.
        """
        tallest_text = 0

        for widget, _ in self.sized_widgets:
            if not isinstance(widget, QLabel):
                continue
            # the stylesheet is applied at polish time, so the font isn't final until then
            widget.ensurePolished()
            tallest_text = max(tallest_text, QFontMetrics(widget.font()).height())

        # room for the 1px border and 2px padding the stylesheet asks for, top and bottom
        self.effective_row_height = max(self.row_height, tallest_text + 2 * (CELL_BORDER + CELL_PADDING))

        for widget, width in self.sized_widgets:
            widget.setFixedSize(width, self.effective_row_height)

    def total_height(self):
        """actual pixel height of the grid, once the font has had its say."""
        return self.effective_row_height * (self.num_rows + 1)   # +1 for the header row

    def narrow_columns(self):
        """
        columns whose header no longer fits the stylesheet's font.

        column widths come from config.yaml and cannot safely grow on their own -- the window
        width is fixed and already validated -- so the app reports them instead of silently
        chopping characters off.
        """
        too_narrow = []

        for col in self.columns:
            if not col.header:
                continue
            probe = self.labels.get((0, 0))
            if probe is None:
                break
            needed = QFontMetrics(probe.font()).horizontalAdvance(col.header) + 2 * (CELL_BORDER + CELL_PADDING)
            if needed > col.width:
                too_narrow.append((col.header, col.width, needed))

        return too_narrow

    def set_cell(self, row, col, text):
        if (row, col) in self.labels:
            self.labels[(row, col)].setText(text)

    def clear(self):
        for label in self.labels.values():
            label.setText('')

    def set_cell_style(self, row, col, style_class):
        if (row, col) in self.labels:
            w = self.labels[(row, col)]
            w.setProperty('class', style_class)
            w.style().unpolish(w)
            w.style().polish(w)


class ElidedLabel(QLabel):
    """
    a QLabel that shortens its text with an ellipsis when it doesn't fit.

    this works by handing QLabel an already-shortened string, rather than by taking over
    paintEvent.  an earlier version painted the text itself, which meant the stylesheet's
    background and border were never drawn -- elided columns came out transparent while every
    other column picked up the css.  letting QLabel do all the painting avoids that entirely.
    """

    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full_text = text
        self._eliding   = False
        self._apply_elision()

    def setText(self, text):
        self._full_text = text
        self._apply_elision()

    def text(self):
        """the text as set, not the shortened version actually on screen."""
        return self._full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elision()

    def changeEvent(self, event):
        super().changeEvent(event)
        # the stylesheet sets the font at polish time, which changes how much fits
        if event.type() == QEvent.FontChange:
            self._apply_elision()

    def _apply_elision(self):
        if self._eliding:
            return   # super().setText below can re-enter via changeEvent

        self._eliding = True
        try:
            width  = self.contentsRect().width() or self.width()
            elided = QFontMetrics(self.font()).elidedText(self._full_text, Qt.ElideRight, width)
            super().setText(elided)
        finally:
            self._eliding = False





class DVB_Monitor(QMainWindow):

    def __init__(self, app, config_path):
        super().__init__()

        self.app = app

        self.setup_from_yaml(path=config_path)

        self.setup_internal_state()

        self.validate_config()                   # check everything is sane

        self.setup_dvb_client()
        self.initUI()

    def showEvent(self, event):
        super().showEvent(event)
        # This runs after window is fully shown
        geo = self.geometry()
        frame = self.frameGeometry()
        screen = QApplication.instance().primaryScreen().size()
        print(f"Window geometry: {geo.x()}, {geo.y()}, {geo.width()}x{geo.height()}")
        print(f"Frame geometry:  {frame.x()}, {frame.y()}, {frame.width()}x{frame.height()}")
        print(f"Screen size:     {screen.width()}x{screen.height()}")

    def setup_internal_state(self):

        ##########
        # holds some state through the loop
        self.time_last_updated = None
        self.current_page      = 0
        self.departures        = {} # holds the departures, per-stop.
        self.num_consecutive_autorefreshes = 0
        self.is_data_cleared = True

        # per-stop health, so one sick stop is visible without hiding the healthy ones.
        # status is one of 'never' (not fetched yet), 'ok', 'error'.
        self.stop_status       = {name: 'never' for name in self.stops_to_monitor}
        self.stop_error        = {}   # stop name -> short message from the last failure
        self.stop_last_success = {}   # stop name -> datetime of the last good fetch
        self.stop_id_cache     = {}   # stop name -> numeric dvb id

        self.num_consecutive_failures = 0   # counts refreshes where EVERY stop failed
        self.fetcher                  = None # the background QThread, when one is in flight
        self.was_last_refresh_automatic = True

        self.is_backlight_off = False

        #############
        #  internal variables for holding Qt objects
        #  
        self.main_layout            = None # will hold all the other layouts
        self.tables_layout          = None # holds the layouts per table on page.
        self.buttons                = None
        self.layout_per_haltestelle = None
        self.header_widgets         = None
        self.time_updated_widget    = None
        self.horizontalGroupBox     = None

        ###########
        # some helper variables so don't need to keep recomputing them

        self.num_cols_per_col = len(self.columns)  # because each departure gets this many, and we use multiple cols of departures

        self.num_cols_needed = math.ceil(self.num_departures_to_monitor / self.num_rows_per_table)

        self.is_nav_needed = len(self.stops_to_monitor) > self.num_stops_per_page
        self.is_nav_needed_prev = len(self.stops_to_monitor) > self.num_stops_per_page
        self.is_nav_needed_next = len(self.stops_to_monitor) > self.num_stops_per_page

        # ceil, not floor: floor silently dropped a partial last page (4 stops at 3/page made the
        # 4th unreachable).  max(1, ...) because floor gave 0 when num_stops_per_page > len(stops),
        # and change_page then divided by it.
        self.num_pages_needed = max(1, math.ceil(len(self.stops_to_monitor) / self.num_stops_per_page))
        self.refresh_interval_ms = self.refresh_interval * 1000
        self.clear_interval_ms = self.clear_interval * 1000

    def scaled_px(self, value):
        """a pixel size from the config, in this screen's pixels."""
        return int(round(value * self.layout_scale))

    def setup_dvb_client(self):
        # the core of this display.  use this object to make queries into the DVB api.
        self.client = dvb.Client(user_agent=self.dvb_client_name)
        self._install_request_timeout()

    def _install_request_timeout(self):
        """
        make the dvb package honor our timeout instead of its own.

        dvb 3.0.0 hardcodes `_TIMEOUT = 15` as a module global and passes it explicitly to
        session.get/post.  fifteen seconds per request, with one request per stop, is a long
        time to sit there when the api is black-holing us.

        two independent hooks, both optional and both guarded.  if a future dvb renames either
        private name we print a warning and run on dvb's own timeout rather than crashing.
        """
        timeout = (self.request_connect_timeout, self.request_timeout)

        # hook 1, the primary.  Session.request is stable public `requests` api; only the
        # `_session` attribute name is private.  this must OVERWRITE rather than set a default,
        # because dvb passes timeout= explicitly on every call.
        session = getattr(self.client, '_session', None)
        if session is not None and hasattr(session, 'request'):
            original_request = session.request

            def request_with_timeout(method, url, **kwargs):
                kwargs['timeout'] = timeout
                return original_request(method, url, **kwargs)

            session.request = request_with_timeout
            if self.verbosity >= 1:
                print(f'ℹ️ dvb request timeout set to {timeout} (connect, read)')
        else:
            print('⚠️ could not find the dvb client session; using the dvb package default timeout')

        # hook 2, the fallback.  read at call time, so assigning to it works.
        try:
            import dvb.dvb as dvb_impl
            if isinstance(getattr(dvb_impl, '_TIMEOUT', None), (int, float)):
                dvb_impl._TIMEOUT = self.request_timeout
        except Exception as e:
            if self.verbosity >= 1:
                print(f'ℹ️ could not lower dvb._TIMEOUT ({e}); relying on the session wrapper')

    def _resolve_stop_id(self, stop_name):
        """the cached numeric id for a stop, or None to fall back to querying by name."""
        if not self.cache_stop_ids:
            return None
        return self.stop_id_cache.get(stop_name)


    def setup_from_yaml(self, path):
        import yaml # pip install pyyaml

        def load_config(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    user_config =  yaml.safe_load(f)
            except FileNotFoundError:
                print(f"❌ no config file found at '{path}'")
                print(f"   generate one with: python DVB_Monitor.py --generate-config {path}")
                sys.exit(-12039)
            except yaml.YAMLError as e:
                print(f"❌ '{path}' is not valid YAML:")
                print(e)
                sys.exit(-12039)
            except Exception as e:
                print(f"❌ could not read '{path}'")
                print(e)
                sys.exit(-12039)

            # an empty file parses to None, and a top-level list/scalar parses to the wrong type.
            # either way `"..." not in user_config` below would raise something unhelpful.
            if user_config is None:
                user_config = {}
            if not isinstance(user_config, dict):
                print(f"❌ '{path}' must contain a YAML mapping at the top level, "
                      f"got {type(user_config).__name__}")
                sys.exit(-12039)

            return user_config

        user_config = load_config(path)

        # read from defaults.  deep, because `columns` is a nested list of dicts.
        config = copy.deepcopy(DEFAULT_CONFIG)

        # then overwrite with the items from the user's yaml file
        config.update(user_config)

        # work out the screen scale before reading any pixel size, so every one of them can be
        # scaled on the way in and the rest of the app can stay in plain pixels
        self.verbosity = config["verbosity"]
        self.scale_with_screen_dpi = bool(config["scale_with_screen_dpi"])
        self.reference_dpi = float(config["reference_dpi"]) or REFERENCE_DPI

        screen = self.app.primaryScreen() if self.app is not None else None
        self.layout_scale = layout_scale_factor(screen, self.reference_dpi, self.scale_with_screen_dpi)

        if self.verbosity >= 1 and abs(self.layout_scale - 1.0) > 0.01:
            dpi = screen.logicalDotsPerInch() if screen else self.reference_dpi
            print(f'ℹ️ screen reports {dpi:.0f} dpi, so pixel sizes from your config are scaled '
                  f'by {self.layout_scale:.2f} to keep up with the fonts')

        # finally, set the values of internal things from the YAML file.
        # i forbid myself to use `eval`.
        self.stops_to_monitor = config["stops_to_monitor"]
        self.row_height = self.scaled_px(config["row_height"])
        self.num_rows_per_table = config["num_rows_per_table"]
        self.num_stops_per_page = config["num_stops_per_page"]
        self.consecutive_autorefresh_timeout_threshold = config["consecutive_autorefresh_timeout_threshold"]
        self.refresh_forever = config["refresh_forever"]
        self.refresh_interval = config["refresh_interval"]
        self.clear_interval = config["clear_interval"]

        self.width = self.scaled_px(config["window_width"])
        self.height = self.scaled_px(config["window_height"])
        self.left = config["window_loc_x"]
        self.top = config["window_loc_y"]

        # columns are already merged from DEFAULT_CONFIG + user yaml
        self.columns = []
        for col in config["columns"]:
            getter_name = col["getter"]
            alignment_name = col["alignment"]

            if getter_name not in GETTER_REGISTRY:
                raise RuntimeError(f"Unknown getter '{getter_name}' in config.yaml. "
                                   f"Valid options: {list(GETTER_REGISTRY.keys())}")

            if alignment_name not in ALIGNMENT_REGISTRY:
                raise RuntimeError(f"Unknown alignment '{alignment_name}' in config.yaml. "
                                   f"Valid options: {list(ALIGNMENT_REGISTRY.keys())}")

            self.columns.append(Column(
                header      = col["header"],
                width       = self.scaled_px(col["width"]),
                getter      = GETTER_REGISTRY[getter_name],
                alignment   = ALIGNMENT_REGISTRY[alignment_name],
                margin_right= self.scaled_px(col.get("margin_right", 0)),
                elide       = col.get("elide", False),  # default to False
            ))

        self.column_group_spacing = self.scaled_px(config["column_group_spacing"])
        self.button_spacing       = self.scaled_px(config["button_spacing"])
        self.button_margin        = self.scaled_px(config["button_margin"])
        self.refresh_button_width = self.scaled_px(config["refresh_button_width"])

        self.mock_update = config["mock_update"]
        self.title = config["window_title"]

        self.num_departures_to_monitor = config["num_departures_to_monitor"]

        self.show_infinite_arrivals = bool(config["show_infinite_arrivals"])

        self.is_full_screen = config["is_full_screen"]
        self.is_touch = config["is_touch"]
        self.touch_rotation = config["touch_rotation"]

        self.is_touch_calibrated = config["is_touch_calibrated"]
        self.touch_raw_x_min     = config["touch_raw_x_min"]
        self.touch_raw_x_max     = config["touch_raw_x_max"]
        self.touch_raw_y_min     = config["touch_raw_y_min"]
        self.touch_raw_y_max     = config["touch_raw_y_max"]

        self.backlight_path        = config["backlight_path"]
        self.backlight_max         = config["backlight_max"]
        self.use_backlight_control = config["use_backlight_control"]

        self.css_file = config["css_file"]

        # --- network robustness ---
        self.request_timeout         = max(1.0, float(config["request_timeout"]))
        self.request_connect_timeout = max(1.0, float(config["request_connect_timeout"]))
        self.cache_stop_ids          = bool(config["cache_stop_ids"])
        self.retry_backoff_factor    = max(1.0, float(config["retry_backoff_factor"]))
        self.retry_backoff_max       = max(1.0, float(config["retry_backoff_max"]))
        self.retry_when_stale        = bool(config["retry_when_stale"])
        self.fetch_in_background     = bool(config["fetch_in_background"])

        # --- what to show when the api misbehaves ---
        self.stale_data_threshold     = max(1.0, float(config["stale_data_threshold"]))
        self.show_stale_data_on_error = bool(config["show_stale_data_on_error"])
        self.error_placeholder        = config["error_placeholder"]

        global UNKNOWN_MODE_EMOJI
        UNKNOWN_MODE_EMOJI = config["unknown_mode_emoji"]

        # deliberately not in DEFAULT_CONFIG: there can be no default for someone's contact
        # details.  it comes from dvb_client_name.txt, or from the config file for setups that
        # predate that.
        self.client_name_search_path = client_name_search_path(path)
        self.dvb_client_name = find_client_name(
            self.client_name_search_path,
            config_value = config.get("dvb_client_name"),
            verbosity    = self.verbosity,
        )

        if not self.dvb_client_name:
            raise RuntimeError(missing_client_name_message(self.client_name_search_path))

        if self.mock_update:
            print('ℹ️ `mock_update` is set to true, which is good for development, but bad for actual use.  set to false so it actually updates data')

    def validate_config(self):
        import math
        errors = []
        warnings = []

        # compute total column width.  this has to match what StopDisplay._build_grid actually
        # lays out, gaps included -- it used to leave the gaps out and so passed configs whose
        # grid was wider than the window.
        col_width_per_group = sum(col.width + col.margin_right for col in self.columns)
        num_cols_needed = math.ceil(self.num_departures_to_monitor / self.num_rows_per_table)
        total_gap_width = self.column_group_spacing * (num_cols_needed - 1)
        total_table_width = col_width_per_group * num_cols_needed + total_gap_width

        # check table fits in window
        if total_table_width > self.width:
            gap_note = (f" plus {num_cols_needed - 1} x {self.column_group_spacing}px between groups"
                        if total_gap_width else "")
            errors.append(
                f"Table is too wide: {num_cols_needed} column groups x {col_width_per_group}px"
                f"{gap_note} = {total_table_width}px, but window is only {self.width}px wide.\n"
                f"  Possible fixes:\n"
                f"    - reduce num_departures_to_monitor (currently {self.num_departures_to_monitor})\n"
                f"    - increase num_rows_per_table (currently {self.num_rows_per_table})\n"
                f"    - reduce column widths in config\n"
                f"    - reduce column_group_spacing (currently {self.column_group_spacing})\n"
                f"    - increase window_width (currently {self.width})"
            )

        # check table fits vertically
        total_table_height = self.row_height * (self.num_rows_per_table + 1)  # +1 for header
        if total_table_height > self.height:
            errors.append(
                f"Table is too tall: {self.num_rows_per_table} rows x {self.row_height}px = "
                f"{total_table_height}px, but window is only {self.height}px tall.\n"
                f"  Possible fixes:\n"
                f"    - reduce num_rows_per_table (currently {self.num_rows_per_table})\n"
                f"    - reduce row_height (currently {self.row_height})\n"
                f"    - increase window_height (currently {self.height})"
            )

        # check num_departures_to_monitor is sensible
        if self.num_departures_to_monitor < 1:
            errors.append(f"num_departures_to_monitor must be at least 1, got {self.num_departures_to_monitor}")

        # check num_rows_per_table is sensible
        if self.num_rows_per_table < 1:
            errors.append(f"num_rows_per_table must be at least 1, got {self.num_rows_per_table}")

        # check refresh interval
        if self.refresh_interval < 10:
            warnings.append(f"refresh_interval is {self.refresh_interval}s which is very fast, DVB api may rate limit you")

        # check stops list
        if not self.stops_to_monitor:
            errors.append("stops_to_monitor is empty, add at least one stop")

        if self.num_stops_per_page < 1:
            errors.append(f"num_stops_per_page must be at least 1, got {self.num_stops_per_page}")

        if self.request_timeout > self.refresh_interval:
            warnings.append(f"request_timeout ({self.request_timeout}s) exceeds refresh_interval "
                            f"({self.refresh_interval}s), so refreshes may overlap")

        if self.stale_data_threshold < self.refresh_interval:
            warnings.append(f"stale_data_threshold ({self.stale_data_threshold}s) is shorter than "
                            f"refresh_interval ({self.refresh_interval}s), so data will look stale constantly")

        # check window size is sensible
        if self.width < 100 or self.height < 100:
            errors.append(f"window size {self.width}x{self.height} seems too small")

        # report warnings
        for w in warnings:
            print(f"⚠️  WARNING: {w}")

        # check css file exists
        if not os.path.exists(self.css_file):
            errors.append(f"css_file '{self.css_file}' not found")

        if self.use_backlight_control:
            if not os.path.exists(self.backlight_path):
                errors.append(f"backlight_path '{self.backlight_path}' not found. "
                              f"Run: ls /sys/class/backlight/ to find correct path")

        # report errors and exit if any
        if errors:
            print(f"\n❌ Found {len(errors)} configuration error(s):\n")
            for i, e in enumerate(errors, 1):
                print(f"  {i}. {e}\n")
            sys.exit(1)

        if self.verbosity>0:
            print(f"✅ config OK: {num_cols_needed} column groups x {col_width_per_group}px = {total_table_width}px wide")


    def initUI(self):

        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)
        
        
        with open(self.css_file, 'r') as f:
            self.app.setStyleSheet(f.read())


        if self.is_full_screen:
            self.showFullScreen()
            self.setWindowFlags(Qt.FramelessWindowHint)

        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)


        self.escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.escape_shortcut.activated.connect(QApplication.quit)
        print('ℹ️ press escape to close window (when it has focus)')

        # gated the same way the nav buttons are.  these used to be wired unconditionally, so on
        # a single-page config an arrow key divided by a num_pages_needed of zero.
        if self.is_nav_needed:
            self.shortcut_prev = QShortcut(QKeySequence(Qt.Key_Left), self)
            self.shortcut_prev.activated.connect(lambda: self.change_page(-1))

            self.shortcut_next = QShortcut(QKeySequence(Qt.Key_Right), self)
            self.shortcut_next.activated.connect(lambda: self.change_page(+1))

        self.shortcut_refresh = QShortcut(QKeySequence(Qt.Key_Up), self)
        self.shortcut_refresh.activated.connect(self.manual_refresh)

        # constructing it with central_widget already installs it there.  calling
        # self.setLayout() as well made Qt print "Attempting to set QLayout on DVB_Monitor,
        # which already has a layout" on every start, and did nothing -- a QMainWindow manages
        # its own layout and the content belongs to the central widget.
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)  # This removes padding around the layout
        
        self.init_tables()
        self.setup_bottom()
        self.setup_timers()
        
        if self.is_touch:
            self.setup_touch()

        # show the window BEFORE the first fetch.  previously the only self.show() was the one at
        # the end of rebuild(), so a failed first fetch meant no window ever appeared at all.
        self.time_updated_widget.setText('starting up…')
        if not self.is_full_screen:
            self.show()
        self.app.processEvents()   # paint the empty grid now, rather than after the first fetch

        self._warn_if_window_too_small()

        # via the event loop, so it is already running before the worker thread starts
        QTimer.singleShot(0, self.auto_refresh) # kick it off!

    def _warn_if_window_too_small(self):
        """
        check the WHOLE window, not just the table.

        _warn_if_layout_overflows only measures the grid, so the timestamp and the row of
        buttons could push past the bottom edge without anything saying so.  on a normal
        desktop the window simply grows, which is fine; in full screen it cannot, and the
        buttons are what falls off.
        """
        central = self.centralWidget()
        if central is None:
            return

        needed = central.sizeHint()

        if needed.height() <= self.height and needed.width() <= self.width:
            return

        detail = (f'the contents need {needed.width()}x{needed.height()}px but the window is '
                  f'{self.width}x{self.height}px')

        if self.is_full_screen:
            print(f'⚠️  {detail}, and in full screen it cannot grow, so the bottom will be cut '
                  f'off.  Fixes: reduce font-size in {self.css_file}, lower button_margin '
                  f'(currently {self.button_margin}), or reduce num_rows_per_table '
                  f'(currently {self.num_rows_per_table}).')
        elif self.verbosity >= 1:
            print(f'ℹ️ {detail}, so the window was grown to fit')


    def setup_touch(self):
        self.touch_filter = TouchFilter(
            self.app,
            self.touch_rotation,
            self.width,
            self.height,
            is_calibrated = self.is_touch_calibrated,
            x_min         = self.touch_raw_x_min,
            x_max         = self.touch_raw_x_max,
            y_min         = self.touch_raw_y_min,
            y_max         = self.touch_raw_y_max,
        )

        self.touch_filter.wake_callback = self.wake_if_sleeping
        
        self.installEventFilter(self.touch_filter)
        self._install_filter_on_children()

    def _install_filter_on_children(self):
        # Install on every child widget recursively
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self.touch_filter)

    def init_tables(self):
        self.tables_layout = QHBoxLayout()
        self.tables = {}
        self.layout_per_haltestelle = {}
        self.header_widgets = {}

        for ii in range(min(self.num_stops_per_page, len(self.stops_to_monitor))):
            self.layout_per_haltestelle[ii] = QVBoxLayout()
            this_layout = self.layout_per_haltestelle[ii]

            # create StopDisplay instead of QTableWidget
            self.tables[ii] = StopDisplay(
                columns        = self.columns,
                num_rows       = self.num_rows_per_table,
                num_cols_needed= self.num_cols_needed,
                row_height     = self.row_height,
                column_group_spacing = self.column_group_spacing,
            )

            self.header_widgets[ii] = QLabel()
            w = self.header_widgets[ii]
            w.setProperty('class', 'haltestelle_header')
            w.setAlignment(Qt.AlignCenter)

            this_layout.addWidget(w)
            this_layout.addWidget(self.tables[ii])
            self.tables_layout.addLayout(this_layout)

        self.main_layout.addLayout(self.tables_layout)

        self._warn_if_layout_overflows()

    def _warn_if_layout_overflows(self):
        """
        validate_config runs before the stylesheet is loaded, so it can only guess at sizes from
        row_height.  now that the grid is built and the font is known, check for real.
        """
        table = self.tables.get(0)
        if table is None:
            return

        if table.effective_row_height > self.row_height:
            print(f'ℹ️ the stylesheet font needs {table.effective_row_height}px rows, more than '
                  f'row_height={self.row_height}; rows were grown to fit')

        if table.total_height() > self.height:
            print(f'⚠️  the table is {table.total_height()}px tall but the window is only '
                  f'{self.height}px.  Fixes: reduce font-size in {self.css_file}, '
                  f'reduce num_rows_per_table (currently {self.num_rows_per_table}), '
                  f'or raise window_height.')

        if table.width() > self.width:
            print(f'⚠️  the table is {table.width()}px wide but the window is only {self.width}px.  '
                  f'Fixes: narrow the columns in your config, '
                  f'reduce column_group_spacing (currently {self.column_group_spacing}), '
                  f'or raise window_width.')

        for header, width, needed in table.narrow_columns():
            print(f'⚠️  column "{header}" is {width}px wide but its heading needs {needed}px at '
                  f'the current font; text will be cut off.  Widen it in your config, '
                  f'or reduce font-size in {self.css_file}.')



    def setup_table(self, ii):

        self.tables[ii] = QTableWidget() # make and assign into dict
        table = self.tables[ii] # get a reference

        self.initialize_table_contents(table)

        # Make table non-editable
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        # Hide row labels (vertical header)
        table.verticalHeader().setVisible(False)

        table.horizontalHeader().setStretchLastSection(False)
        table.verticalHeader().setStretchLastSection(False)

        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)

        return table

    def initialize_table_contents(self,table):

        table.setRowCount(self.num_rows_per_table)
        table.setColumnCount(self.num_cols_per_col * self.num_cols_needed)
        for jj in range(self.num_rows_per_table):
            table.setRowHeight(jj, self.row_height)

        col_ind = 0
        for ii in range(self.num_cols_needed):
            for col in self.columns:

                
                table.setHorizontalHeaderItem(col_ind, QTableWidgetItem(col.header))
                table.setColumnWidth(col_ind, col.width)

                
                for r in range(self.num_rows_per_table):

                    table.setItem(r, col_ind, QTableWidgetItem(  ))

                    item = table.item(r, col_ind)

                    item.setText('?')
                    item.setTextAlignment(col.alignment)


                col_ind += 1

        total_width = sum( table.columnWidth(col) for col in range(table.columnCount()))
        table.setFixedSize(total_width + 20, self.row_height * (self.num_rows_per_table+1))



    def setup_bottom(self):
        self.buttons = {}

        self.bottom_layout = QHBoxLayout()
        self.bottom_layout.setSpacing(self.button_spacing)
        self.bottom_layout.setContentsMargins(self.button_margin, self.button_margin,
                                              self.button_margin, self.button_margin)

        self.setup_timeupdated()

        if self.is_nav_needed_prev:
            self.buttons['prev'] = QPushButton()
            self.buttons['prev'].clicked.connect(lambda x: self.change_page(-1))

        if self.is_nav_needed_next:
            self.buttons['next'] = QPushButton()
            self.buttons['next'].clicked.connect(lambda x: self.change_page(+1))

        self.buttons['refresh'] = QPushButton("🥀")
        self.buttons['refresh'].clicked.connect(self.manual_refresh)

        # never narrower than the glyph needs, whatever the config or the stylesheet say
        self.buttons['refresh'].setFixedWidth(
            max(self.refresh_button_width, self.buttons['refresh'].sizeHint().width()))

        # stretch of 1 on the nav buttons, 0 on refresh: the stop names get the room, the glyph
        # does not.  where a nav button is absent, an empty stretch takes its place so that the
        # refresh button stays centred instead of spreading out to fill the window.
        if self.is_nav_needed_prev:
            self.bottom_layout.addWidget(self.buttons['prev'], 1)
        else:
            self.bottom_layout.addStretch(1)

        self.bottom_layout.addWidget(self.buttons['refresh'], 0)

        if self.is_nav_needed_next:
            self.bottom_layout.addWidget(self.buttons['next'], 1)
        else:
            self.bottom_layout.addStretch(1)

        self.main_layout.addLayout(self.bottom_layout)

    def setup_timeupdated(self):
        self.time_updated_widget = QLabel()

        w = self.time_updated_widget
        w.setProperty('class', 'footer')
        w.setAlignment(Qt.AlignCenter)

        self.main_layout.addWidget(self.time_updated_widget)  # Add text widget here


        

    def setup_timers(self):
        self.timer_refresh = QTimer(self)
        self.timer_refresh.setSingleShot(True)
        self.timer_refresh.timeout.connect(self.auto_refresh)

        self.timer_stale_data = QTimer(self)
        self.timer_stale_data.setSingleShot(True)
        self.timer_stale_data.timeout.connect(self.clear_stale_data)

        # the background fetch is what re-arms timer_refresh when it completes.  if it somehow
        # never reports completion, nothing would ever schedule another refresh and the display
        # would quietly freeze -- which is the failure mode this whole change exists to remove.
        self.timer_fetch_watchdog = QTimer(self)
        self.timer_fetch_watchdog.setSingleShot(True)
        self.timer_fetch_watchdog.timeout.connect(self._on_fetch_watchdog)

        # the escape shortcut calls QApplication.quit directly, which does NOT go through
        # closeEvent.  without this, quitting mid-fetch aborts on a still-running QThread.
        self.app.aboutToQuit.connect(self._stop_fetcher)
    



    def clear_stale_data(self):
        if self.verbosity >= 1:
            print('clearing stale data, waiting for refresh button push.')

        self.is_data_cleared = True
        self.departures = {}
        # the per-stop status dicts survive, so the footer can still explain WHY there is nothing

        for ind, t in self.tables.items():
            t.clear()  # StopDisplay already has this method!

        self.backlight_off()
        self.time_updated_widget.setText(f'Stale data cleared. Refresh to start again.')

        if self.retry_when_stale:
            self.was_last_refresh_automatic = True
            self.timer_refresh.start(max(self.refresh_interval_ms, self._next_refresh_interval_ms()))


    def auto_refresh(self):
        if self.verbosity>=1:
            print('auto refreshing.')

        self.num_consecutive_autorefreshes += 1
        self.was_last_refresh_automatic = True

        self.refresh()

        # the next tick is armed in _finish_refresh, not here.  when fetching in the background
        # refresh() returns before any data has arrived, and the backoff can't be computed until
        # we know whether the fetch succeeded.

    def manual_refresh(self):
        if self.verbosity>=1:
            print('manually refreshing.')

        self.num_consecutive_autorefreshes = 0
        # a human pressing refresh means "try now", so drop any accumulated backoff
        self.num_consecutive_failures = 0
        self.was_last_refresh_automatic = False

        self.refresh()

        self.timer_refresh.start(self.refresh_interval_ms)
        if not self.refresh_forever:
            self.timer_stale_data.start(self.clear_interval_ms)

    def _schedule_next_auto_refresh(self):
        """
        arm the next automatic refresh.  same decision tree as before, just relocated out of
        auto_refresh so it can run after an asynchronous fetch has actually finished.
        """
        if not self.was_last_refresh_automatic:
            return   # manual_refresh sets its own timers

        if self.refresh_forever or self.num_consecutive_autorefreshes < self.consecutive_autorefresh_timeout_threshold:
            interval_ms = self._next_refresh_interval_ms()

            if self.num_consecutive_failures > 0:
                print(f'ℹ️ {self.num_consecutive_failures} consecutive failed refreshes, '
                      f'next try in {interval_ms//1000}s')

            self.timer_refresh.start(interval_ms)
            self.timer_stale_data.stop()
        else:
            self.timer_stale_data.start(self.clear_interval_ms)

    def _next_refresh_interval_ms(self):
        """
        normally the configured interval.  while every stop is failing, back off exponentially
        so we stop hammering an api that is plainly down.
        """
        if self.num_consecutive_failures <= 0:
            return self.refresh_interval_ms

        delay = min(self.refresh_interval * (self.retry_backoff_factor ** self.num_consecutive_failures),
                    self.retry_backoff_max)

        return int(delay * 1000)



    def refresh(self):
        self.backlight_on()

        if self.fetch_in_background:
            # returns immediately.  results arrive on _on_stop_fetched, completion on
            # _on_fetch_finished, both back on the gui thread.
            self._start_background_refresh()
        else:
            try:
                self._refresh_all_departures()
            except Exception as e:
                # _refresh_all_departures is written not to raise.  this is the last-ditch net
                # that keeps an unforeseen bug from killing the app from inside a qt slot.
                print(f'⚠️ unexpected error during refresh: {type(e).__name__}: {e}')
                if self.verbosity >= 2:
                    traceback.print_exc()

            self._finish_refresh()

    def _pending_jobs(self):
        """the (stop_name, cached_stop_id) pairs that actually need fetching this cycle."""
        jobs = []
        for stop_name in self.stops_to_monitor:
            if self.mock_update and stop_name in self.departures:
                if self.verbosity>=1:
                    print(f'mock getting departures for {stop_name}')
                continue
            jobs.append((stop_name, self._resolve_stop_id(stop_name)))
        return jobs

    def _refresh_all_departures(self):
        """
        synchronous fetch of every stop, used when fetch_in_background is false.

        per-stop isolated: a failure on one stop no longer prevents the others from updating,
        and the loop as a whole cannot raise.
        """
        for stop_name, stop_id in self._pending_jobs():
            if self.verbosity>=1:
                print(f'getting departures for {stop_name}')

            self._record_stop_result(
                fetch_departures_for_stop(self.client, stop_name, stop_id, self.verbosity))

    def _record_stop_result(self, result):
        """
        fold one FetchResult into our state.  the only writer of self.departures, and it always
        runs on the gui thread -- including when the background fetcher produced the result.
        """
        if result.error is None:
            self.departures[result.stop_name]        = result.departures
            self.stop_status[result.stop_name]       = 'ok'
            self.stop_error[result.stop_name]        = None
            self.stop_last_success[result.stop_name] = datetime.now()
            self.time_last_updated                   = datetime.now()

            if result.stop_id:
                self.stop_id_cache[result.stop_name] = result.stop_id

            if self.verbosity>=1:
                print(f'✅ {result.stop_name}: {len(result.departures)} departures '
                      f'in {result.duration:.1f}s')
        else:
            self.stop_status[result.stop_name] = 'error'
            self.stop_error[result.stop_name]  = result.error

            if not self.show_stale_data_on_error:
                self.departures.pop(result.stop_name, None)

            # printed regardless of verbosity.  a display that silently shows nothing is exactly
            # the symptom this whole change is about.
            print(f'⚠️ {result.stop_name}: {result.error}')

    def _update_failure_bookkeeping(self):
        """
        back off only when EVERY stop failed, i.e. a real outage.  one flaky stop among three
        keeps the normal cadence, so the healthy stops stay fresh.
        """
        statuses = [self.stop_status.get(name, 'never') for name in self.stops_to_monitor]

        if statuses and all(s == 'error' for s in statuses):
            self.num_consecutive_failures += 1
        else:
            self.num_consecutive_failures = 0

    def _finish_refresh(self):
        """everything that has to happen once a refresh cycle is complete, however it ran."""
        self.timer_fetch_watchdog.stop()

        self._update_failure_bookkeeping()

        # reflect what we actually have, rather than assuming the fetch worked
        self.is_data_cleared = not any(self.departures.get(name) for name in self.stops_to_monitor)

        self._refresh_time()
        self.rebuild()
        self._schedule_next_auto_refresh()

    def _refresh_time(self):
        """the footer: when we last had good data, and what is currently broken."""
        more_text = ''
        if self.mock_update:
            more_text = "`mock_update` is set to true. "

        num_failed = sum(1 for name in self.stops_to_monitor
                         if self.stop_status.get(name) == 'error')
        num_total  = len(self.stops_to_monitor)

        if self.time_last_updated is None:
            if num_failed:
                message = f'❌ cannot reach DVB ({self._last_error_summary()})'
                if self.num_consecutive_failures:
                    message += f' · retry in {self._next_refresh_interval_ms()//1000}s'
            else:
                message = 'no data yet…'
            self.time_updated_widget.setText(f'{more_text}{message}')
            return

        timestamp = self.time_last_updated.strftime("%Y-%m-%d %H:%M:%S")

        if num_failed:
            self.time_updated_widget.setText(
                f'{more_text}⚠️ {num_failed}/{num_total} stops failed · last ok {timestamp}')
        else:
            # unchanged from before, so the happy path looks exactly as it always has
            self.time_updated_widget.setText(f'{more_text}{timestamp}')

    def _start_background_refresh(self):
        """kick off a fetch on the worker thread.  returns immediately."""
        if self.fetcher is not None and self.fetcher.isRunning():
            if self.verbosity>=1:
                print('ℹ️ a refresh is already in flight, skipping this tick')
            return

        jobs = self._pending_jobs()

        if not jobs:
            self._finish_refresh()
            return

        if self.verbosity>=1:
            for stop_name, _ in jobs:
                print(f'getting departures for {stop_name}')

        self.fetcher = DepartureFetcher(self.client, jobs, self.verbosity, parent=self)
        self.fetcher.stop_fetched.connect(self._on_stop_fetched)
        self.fetcher.all_finished.connect(self._on_fetch_finished)
        self.fetcher.start()

        # generous: every job timing out, plus slack.  this should never fire.
        watchdog_ms = int((len(jobs) * (self.request_timeout + self.request_connect_timeout) + 30) * 1000)
        self.timer_fetch_watchdog.start(watchdog_ms)

    def _on_fetch_watchdog(self):
        print('⚠️ background fetch never reported completion; rescheduling anyway')
        self._finish_refresh()

    def _on_stop_fetched(self, result):
        """one stop landed.  runs on the gui thread.  repaint right away, so stops appear as
        they arrive rather than all at once at the end."""
        self._record_stop_result(result)
        self._refresh_time()
        self.rebuild()

    def _on_fetch_finished(self):
        """the whole cycle landed.  runs on the gui thread."""
        self._finish_refresh()

    def _stop_fetcher(self):
        """
        stop the worker before the app goes away.

        without this, quitting mid-fetch destroys a running QThread, which Qt turns into
        'QThread: Destroyed while thread is still running' and an abort.
        """
        self.timer_refresh.stop()
        self.timer_stale_data.stop()
        self.timer_fetch_watchdog.stop()

        if self.fetcher is not None and self.fetcher.isRunning():
            self.fetcher.requestInterruption()

            # the worker only checks for interruption between stops, so the wait has to cover the
            # request currently in flight.  our own timeout caps that at request_timeout.
            if not self.fetcher.wait(int(self.request_timeout * 1000) + 2000):
                # last resort.  terminate() is ugly, but a QThread still running when it gets
                # destroyed makes Qt abort the process, which is worse.
                print('⚠️ background fetch did not stop in time, terminating it')
                self.fetcher.terminate()
                self.fetcher.wait(2000)

    def closeEvent(self, event):
        self._stop_fetcher()
        super().closeEvent(event)

    def _last_error_summary(self):
        for name in self.stops_to_monitor:
            error = self.stop_error.get(name)
            if error:
                return error
        return 'unknown error'




    def change_page(self, increment):
        if self.num_pages_needed <= 1:
            return

        self.current_page = (increment + self.current_page + self.num_pages_needed) % self.num_pages_needed

        is_stale = (self.time_last_updated is None
                    or (datetime.now()-self.time_last_updated) > timedelta(milliseconds=self.refresh_interval_ms))

        if self.is_data_cleared or is_stale:
            self.manual_refresh()
        else:
            self.rebuild()

    def rebuild(self):
        """
        rebuilding uses existing data. it just adjusts what's displayed in the tables and buttons
        """

        self._rebuild_stops()
        self._rebuild_nav()
        self.update()
        self.show()#FullScreen()
    
    def _rebuild_stops(self):

        for table_ind in range(self.num_stops_per_page):
            stop_ind = self.current_page*self.num_stops_per_page + table_ind

            self.rebuild_table(stop_ind)



    def _rebuild_nav(self):
        if self.is_nav_needed_prev:
            self.buttons['prev'].setText(self.stops_to_monitor[(self.current_page*self.num_stops_per_page-1) % len(self.stops_to_monitor)])

        if self.is_nav_needed_next:
            self.buttons['next'].setText(self.stops_to_monitor[(self.current_page*self.num_stops_per_page+self.num_stops_per_page) % len(self.stops_to_monitor)])





    def rebuild_table(self, stop_ind):
        table_ind = stop_ind % self.num_stops_per_page

        # init_tables only builds min(num_stops_per_page, len(stops)) tables, so there is not
        # necessarily a widget for every slot on the page
        table = self.tables.get(table_ind)
        if table is None:
            return

        table.clear()

        # the last page can be partial, in which case this table has no stop at all.  blank it,
        # rather than leaving the previous page's contents sitting there.
        if stop_ind >= len(self.stops_to_monitor):
            self.header_widgets[table_ind].setText('')
            return

        stop_name = self.stops_to_monitor[stop_ind]
        self._rebuild_header(table_ind, stop_name)

        # .get, because a stop that has never fetched successfully simply isn't in here
        departures = self.departures.get(stop_name) or []

        if not self.show_infinite_arrivals:
            departures = [d for d in departures if np.isfinite(get_minutes(d))]

        num_cols_per_group = len(self.columns)

        if not departures and self.stop_status.get(stop_name) == 'error':
            # nothing to show and we know why.  say so, rather than leaving an empty grid.
            # the message goes in the widest column, since a narrow one just renders as mush.
            widest = max(range(num_cols_per_group), key=lambda ii: self.columns[ii].width)
            table.set_cell(0, widest, self.stop_error.get(stop_name, 'error'))
            return

        for ii, departure in enumerate(departures[:self.num_departures_to_monitor]):
            row       = ii % self.num_rows_per_table
            col_group = ii // self.num_rows_per_table

            for shift, c in enumerate(self.columns):
                try:
                    val = c.getter(departure)
                except Exception as e:
                    # one malformed departure costs one cell, not the whole repaint
                    val = self.error_placeholder
                    if self.verbosity >= 1:
                        print(f'⚠️ getter {c.getter.__name__} failed on {stop_name} row {ii}: {e}')

                # account for spacer columns between groups
                # each group is num_cols_per_group wide, plus 1 spacer column after it
                grid_col = col_group * (num_cols_per_group + 1) + shift

                table.set_cell(row, grid_col, f'{val}')

    def _rebuild_header(self, table_ind, stop_name):
        """the stop name, plus how much to trust what is under it."""
        status = self.stop_status.get(stop_name, 'never')
        style  = 'haltestelle_header'
        text   = stop_name

        if status == 'never':
            text = f'{stop_name} …'
        elif status == 'error':
            if self.departures.get(stop_name):
                text  = f'{stop_name} ⚠️'   # stale data on screen, latest fetch failed
                style = 'haltestelle_header_stale'
            else:
                text  = f'{stop_name} ❌'   # failed, and nothing to fall back on
                style = 'haltestelle_header_error'
        else:
            age = self._stop_age_seconds(stop_name)
            if age is not None and age > self.stale_data_threshold:
                text  = f'{stop_name} (stale {int(age)//60}m)'
                style = 'haltestelle_header_stale'

        w = self.header_widgets[table_ind]
        w.setText(text)
        if w.property('class') != style:
            w.setProperty('class', style)
            w.style().unpolish(w)
            w.style().polish(w)

    def _stop_age_seconds(self, stop_name):
        last = self.stop_last_success.get(stop_name)
        if last is None:
            return None
        return (datetime.now() - last).total_seconds()

    def backlight_on(self):
        if self.use_backlight_control:
            try:
                with open(self.backlight_path, 'w') as f:
                    f.write(str(self.backlight_max))
                self.is_backlight_off = False
            except Exception as e:
                print(f"⚠️ could not turn backlight on: {e}")

    def backlight_off(self):

        if self.refresh_forever:
                return

        if self.use_backlight_control:
            try:
                with open(self.backlight_path, 'w') as f:
                    f.write('0')
                self.is_backlight_off = True
            except Exception as e:
                print(f"⚠️ could not turn backlight off: {e}")

    def wake_if_sleeping(self):
        """
        Called by TouchFilter on every touch.
        Returns True if screen was off (touch should be blocked).
        Returns False if screen was on (touch should be processed normally).
        """
        if self.use_backlight_control and self.is_backlight_off:
            self.backlight_on()

            # the screen only sleeps after clear_stale_data has thrown the departures away, so
            # waking it up onto an empty table and waiting to be asked again is no use to
            # anybody.  a tap to wake means "show me the departures".
            #
            # with fetch_in_background this returns before the network is touched, so the touch
            # handler is not held up.  synchronously it will block for the length of the fetch.
            self.manual_refresh()

            return True   # was sleeping, block the touch
        return False      # was on, process normally


def generate_default_config(path):
    import yaml

    config = copy.deepcopy(DEFAULT_CONFIG)

    # add a commented reminder - yaml doesn't support comments via dump,
    # so we write the file manually for the important ones
    with open(path, 'w', encoding='utf-8') as f:
        f.write("# DVB Monitor configuration file\n")
        f.write("# generated by DVB_Monitor.py --generate-config\n")
        f.write("\n")
        f.write("# Your DVB client name does NOT go in here -- it is your contact information,\n")
        f.write(f"# so it lives in {CLIENT_NAME_FILENAME}, which is gitignored.  This file is\n")
        f.write("# therefore safe to commit and share.\n")
        f.write("\n")
        f.write("# REQUIRED: set this to your stop name(s)\n")
        f.write("# stops_to_monitor:\n")
        f.write("#   - Altmarkt\n")
        f.write("#   - Postplatz\n")
        f.write("\n")
        f.write("# remaining settings with defaults:\n")
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print(f"✅ default config written to {path}")
    print(f"ℹ️  edit '{path}' and set stops_to_monitor")

    if not find_client_name(client_name_search_path(path)):
        print()
        print(f"⚠️  {missing_client_name_message(client_name_search_path(path))}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DVB Monitor')
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='path to config yaml file (default: config.yaml)'
    )
    parser.add_argument(
        '--generate-config',
        default=None,
        help='generate a default config file with name of your choice and exit.  Requires an argument.  like `python DVB_Monitor.py --generate-config myconfig.yaml`'
    )
    parser.add_argument(
        '--fake-client',
        default=None,
        choices=['ok', 'fail', 'mixed', 'empty', 'slow'],
        help='do not touch the network; use a fake client instead, to eyeball the degraded states'
    )
    args = parser.parse_args()


    if args.generate_config:
        generate_default_config(args.generate_config)
        sys.exit(0)


    # PyQt5 turns an unhandled exception in a slot into qFatal()/abort().  this keeps the app
    # alive and prints instead.  belt and braces only -- the real fixes are the try/excepts
    # around the api calls themselves.
    def excepthook(exc_type, exc_value, exc_tb):
        print('❌ unhandled exception (app kept alive):', file=sys.stderr)
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook


    app = QApplication(sys.argv)

    try:
        if args.fake_client:
            from fake_dvb_client import make_fake_client

            print(f'⚠️ RUNNING WITH FAKE CLIENT ({args.fake_client}) — NO REAL DATA')

            class FakeClientMonitor(DVB_Monitor):
                def setup_dvb_client(self):
                    self.client = make_fake_client(args.fake_client, self.stops_to_monitor)

            ex = FakeClientMonitor(app, config_path=args.config)
        else:
            ex = DVB_Monitor(app, config_path=args.config)
    except SystemExit:
        raise
    except RuntimeError as e:
        # this file raises RuntimeError for "your config is wrong", where a traceback is noise
        print(f'❌ {e}')
        sys.exit(2)
    except Exception as e:
        print(f'❌ failed to start: {type(e).__name__}: {e}')
        traceback.print_exc()
        sys.exit(2)

    sys.exit(app.exec_())
    
