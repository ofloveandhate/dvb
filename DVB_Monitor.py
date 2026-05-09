import sys
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QGroupBox, QDialog, QVBoxLayout, QGridLayout, QMainWindow, QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QShortcut
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtCore import pyqtSlot, QTimer
from PyQt5.QtCore import Qt

from PyQt5.QtGui import QPainter, QFontMetrics

import dvb
from datetime import datetime, timezone, timedelta

import argparse
from collections import namedtuple

import numpy as np # for infinity


import os

DEFAULT_CONFIG = {
    "stops_to_monitor": ["Altmarkt"],
    "row_height": 30,
    "num_rows_per_table": 6,
    "num_stops_per_page": 1,
    "consecutive_autorefresh_timeout_threshold": 10,
    "refresh_interval": 60,
    "clear_interval": 120,
    "window_width": 480,
    "window_height": 320,
    "window_loc_x": 0,
    "window_loc_y": 0,
    "mock_update": False,
    "window_title": "DVB Local Stop Monitor",
    "num_departures_to_monitor":12,
    "verbosity": 0,

    "columns": [
        {"header": "#",    "width": 35,  "getter": "get_line",        "alignment": "center", "margin_right": 0, "elide": False},
        {"header": "",     "width": 30,  "getter": "get_mode_emoji",  "alignment": "left", "margin_right": 0, "elide": False},
        {"header": "Mins", "width": 30,  "getter": "get_minutes",     "alignment": "right", "margin_right": 0, "elide": False},
        {"header": "Dest", "width": 140, "getter": "get_destination", "alignment": "left", "margin_right": 0, "elide": True},
    ],

    "is_full_screen": False,
    "is_touch": False,
    "touch_rotation": 270,


    "is_touch_calibrated": False,
    "touch_raw_x_min": 46,
    "touch_raw_x_max": 434,
    "touch_raw_y_min": 22,
    "touch_raw_y_max": 287,

    "css_file": "style.css",

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
    'IntercityBus': '🚍'
}



Column = namedtuple("Column", ["header", "width", "getter", "alignment", "margin_right", "elide"])

def get_line(departure):
    return departure.line

def get_mode_emoji(departure):
    try:
        e = mode_emoji[departure.mode]
    except Exception as e:
        print(f'unfound emoji for mode {departure.mode}')

    return e

def get_line_w_mode(departure):
    line = get_line(departure)
    e = get_mode_emoji(departure)

    return f'{line} {e}'
    

def get_destination(departure):
    return departure.direction

def get_minutes(departure):
    """
    compute the number of minutes, rounded down via integer arithmetic, to departure.

    problem: if the real_time is none, then this may fail.  so use a try/except around this.
    """

    if departure.real_time:
        minutes = int((departure.real_time - datetime.now(timezone.utc)).total_seconds() // 60 + 1 )
        # adding +1 to make match the iphone app
    else:
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
            raw_x = event.globalPos().x()
            raw_y = event.globalPos().y()
            new_x, new_y = self._transform(raw_x, raw_y)  # <- only change here

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


class StopDisplay(QWidget):
    """Replaces QTableWidget for one stop"""
    
    def __init__(self, columns, num_rows, num_cols_needed, row_height):
        super().__init__()
        self.columns = columns
        self.num_rows = num_rows
        self.num_cols_needed = num_cols_needed
        self.row_height = row_height
        self.labels = {}  # keyed by (row, col)
        
        self.grid = QGridLayout()
        self.grid.setSpacing(0)
        self.grid.setHorizontalSpacing(0)  # ADD THIS
        self.grid.setVerticalSpacing(0)    # ADD THIS
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.grid)
        
        self._build_grid()
    
    def _build_grid(self):

        num_cols_per_group = len(self.columns)
        total_w = sum(col.width + col.margin_right for col in self.columns) * self.num_cols_needed
        self.setFixedWidth(total_w)
        
        num_cols_per_group = len(self.columns)

        for col_group in range(self.num_cols_needed):
            for col_ind, col in enumerate(self.columns):
                grid_col = col_group * num_cols_per_group + col_ind

                # header
                header = ElidedLabel(col.header) if col.elide else QLabel(col.header)
                header.setAlignment(Qt.AlignCenter)
                header.setFixedSize(col.width, self.row_height)
                header.setProperty('class', 'grid_header')
                header.setContentsMargins(0, 0, col.margin_right, 0)
                self.grid.addWidget(header, 0, grid_col)

                # data rows
                for row in range(self.num_rows):
                    label = ElidedLabel('?') if col.elide else QLabel('?')
                    label.setAlignment(col.alignment)
                    label.setFixedSize(col.width, self.row_height)
                    label.setProperty('class', 'grid_cell')
                    label.setContentsMargins(0, 0, col.margin_right, 0)
                    self.grid.addWidget(label, row + 1, grid_col)
                    self.labels[(row, grid_col)] = label
    
    def set_cell(self, row, col, text):
        """Set text of a cell"""
        if (row, col) in self.labels:
            self.labels[(row, col)].setText(text)
    
    def clear(self):
        """Clear all cells"""
        for label in self.labels.values():
            label.setText('')
    
    def set_cell_style(self, row, col, style_class):
        """Change styling of a cell"""
        if (row, col) in self.labels:
            self.labels[(row, col)].setProperty('class', style_class)
            self.labels[(row, col)].style().unpolish(self.labels[(row, col)])
            self.labels[(row, col)].style().polish(self.labels[(row, col)])



# lets us truncate certain columns
class ElidedLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)

    def paintEvent(self, event):
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self.text(), Qt.ElideRight, self.width())
        painter.drawText(self.rect(), self.alignment(), elided)


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

        import math
        self.num_cols_needed = math.ceil(self.num_departures_to_monitor / self.num_rows_per_table)

        self.is_nav_needed = len(self.stops_to_monitor) > self.num_stops_per_page
        self.is_nav_needed_prev = len(self.stops_to_monitor) > self.num_stops_per_page
        self.is_nav_needed_next = len(self.stops_to_monitor) > self.num_stops_per_page + 1
        self.num_pages_needed = len(self.stops_to_monitor) // self.num_stops_per_page
        self.refresh_interval_ms = self.refresh_interval * 1000
        self.clear_interval_ms = self.clear_interval * 1000

    def setup_dvb_client(self):
        # the core of this display.  use this object to make queries into the DVB api.
        self.client = dvb.Client(user_agent=self.dvb_client_name)


    def setup_from_yaml(self, path):
        import yaml # pip install pyyaml

        def load_config(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    user_config =  yaml.safe_load(f)
            except Exception as e:
                print("didn't find config.yaml at the current location")
                print(e)
                sys.exit(-12039)

            if "dvb_client_name" not in user_config:
                raise RuntimeError("required entry `dvb_client_name` not found in your `config.yaml` file.  Add it.")
            return user_config

        user_config = load_config(path)

        # read from defaults
        config = DEFAULT_CONFIG.copy()

        # then overwrite with the items from the user's yaml file
        config.update(user_config)

        # finally, set the values of internal things from the YAML file.
        # i forbid myself to use `eval`.
        self.stops_to_monitor = config["stops_to_monitor"]
        self.row_height = config["row_height"]
        self.num_rows_per_table = config["num_rows_per_table"]
        self.num_stops_per_page = config["num_stops_per_page"]
        self.consecutive_autorefresh_timeout_threshold = config["consecutive_autorefresh_timeout_threshold"]
        self.refresh_interval = config["refresh_interval"]
        self.clear_interval = config["clear_interval"]

        self.width = config["window_width"]
        self.height = config["window_height"]
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
                width       = col["width"],
                getter      = GETTER_REGISTRY[getter_name],
                alignment   = ALIGNMENT_REGISTRY[alignment_name],
                margin_right= col.get("margin_right", 0),
                elide       = col.get("elide", False),  # default to False
            ))


        self.mock_update = config["mock_update"]
        self.title = config["window_title"]

        self.num_departures_to_monitor = config["num_departures_to_monitor"]

        self.verbosity = config["verbosity"]

        self.is_full_screen = config["is_full_screen"]
        self.is_touch = config["is_touch"]
        self.touch_rotation = config["touch_rotation"]

        self.is_touch_calibrated = config["is_touch_calibrated"]
        self.touch_raw_x_min     = config["touch_raw_x_min"]
        self.touch_raw_x_max     = config["touch_raw_x_max"]
        self.touch_raw_y_min     = config["touch_raw_y_min"]
        self.touch_raw_y_max     = config["touch_raw_y_max"]

        self.css_file = config["css_file"]

        self.dvb_client_name = config["dvb_client_name"]  # there should be no default for this, because the user is supposed to give contact into in this strong.
        if not self.dvb_client_name:
            raise RuntimeError('dvb_client_name must not be blank')

        if self.mock_update:
            print('ℹ️ `mock_update` is set to true, which is good for development, but bad for actual use.  set to false so it actually updates data')

    def validate_config(self):
        import math
        errors = []
        warnings = []

        # compute total column width
        col_width_per_group = sum(col.width + col.margin_right for col in self.columns)
        num_cols_needed = math.ceil(self.num_departures_to_monitor / self.num_rows_per_table)
        total_table_width = col_width_per_group * num_cols_needed

        # check table fits in window
        if total_table_width > self.width:
            errors.append(
                f"Table is too wide: {num_cols_needed} column groups x {col_width_per_group}px = "
                f"{total_table_width}px, but window is only {self.width}px wide.\n"
                f"  Possible fixes:\n"
                f"    - reduce num_departures_to_monitor (currently {self.num_departures_to_monitor})\n"
                f"    - increase num_rows_per_table (currently {self.num_rows_per_table})\n"
                f"    - reduce column widths in config\n"
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

        # check window size is sensible
        if self.width < 100 or self.height < 100:
            errors.append(f"window size {self.width}x{self.height} seems too small")

        # report warnings
        for w in warnings:
            print(f"⚠️  WARNING: {w}")

        # check css file exists
        if not os.path.exists(self.css_file):
            errors.append(f"css_file '{self.css_file}' not found")

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

        self.shortcut_prev = QShortcut(QKeySequence(Qt.Key_Left), self)
        self.shortcut_prev.activated.connect(lambda: self.change_page(-1))

        self.shortcut_next = QShortcut(QKeySequence(Qt.Key_Right), self)
        self.shortcut_next.activated.connect(lambda: self.change_page(+1))

        self.shortcut_refresh = QShortcut(QKeySequence(Qt.Key_Up), self)
        self.shortcut_refresh.activated.connect(self.manual_refresh)

        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)  # This removes padding around the layout
        self.setLayout(self.main_layout)
        
        self.init_tables()
        self.setup_bottom()
        self.setup_timers()
        
        if self.is_touch:
            self.setup_touch()

        self.auto_refresh() # kick it off!


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
            )

            self.header_widgets[ii] = QLabel()
            w = self.header_widgets[ii]
            w.setProperty('class', 'haltestelle_header')
            w.setAlignment(Qt.AlignCenter)

            this_layout.addWidget(w)
            this_layout.addWidget(self.tables[ii])
            self.tables_layout.addLayout(this_layout)

        self.main_layout.addLayout(self.tables_layout)



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

        self.setup_timeupdated()

        if self.is_nav_needed_prev:
            self.buttons['prev'] = QPushButton()
            self.buttons['prev'].clicked.connect(lambda x: self.change_page(-1))

        if self.is_nav_needed_next:
            self.buttons['next'] = QPushButton()
            self.buttons['next'].clicked.connect(lambda x: self.change_page(+1))

        self.buttons['refresh'] = QPushButton("🥀")
        self.buttons['refresh'].clicked.connect(self.manual_refresh)

        if self.is_nav_needed_prev:
            self.bottom_layout.addWidget(self.buttons['prev'])

        self.bottom_layout.addWidget(self.buttons['refresh'])

        if self.is_nav_needed_next:
            self.bottom_layout.addWidget(self.buttons['next'])

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
    



    def clear_stale_data(self):
        if self.verbosity >= 1:
            print('clearing stale data, waiting for refresh button push.')

        self.is_data_cleared = True
        self.departures = {}

        for ind, t in self.tables.items():
            t.clear()  # StopDisplay already has this method!

        self.time_updated_widget.setText(f'Stale data cleared. Refresh to start again.')


    def auto_refresh(self):
        if self.verbosity>=1:
            print('auto refreshing.')

        self.refresh()
        self.num_consecutive_autorefreshes += 1

        if self.num_consecutive_autorefreshes < self.consecutive_autorefresh_timeout_threshold:
            self.timer_refresh.start(self.refresh_interval_ms)
            self.timer_stale_data.stop()
        else:
            self.timer_stale_data.start(self.clear_interval_ms)

        





    def manual_refresh(self):
        if self.verbosity>=1:
            print('manually refreshing.')

        self.refresh()

        self.num_consecutive_autorefreshes = 0
        self.timer_refresh.start(self.refresh_interval_ms)
        self.timer_stale_data.start(self.clear_interval_ms)



    def refresh(self):
        self.is_data_cleared = False
        self._refresh_all_departures()
        self._refresh_time()

        self.rebuild()

    def _refresh_all_departures(self):

        for stop_name in self.stops_to_monitor:

            if not self.mock_update or stop_name not in self.departures:

                if self.verbosity>=1:
                    print(f'getting departures for {stop_name}')
                self.departures[stop_name] = self.client.monitor(stop=stop_name,limit=0)
                self.time_last_updated = datetime.now()
            else:
                if self.verbosity>=1:
                    print(f'mock getting departures for {stop_name}')

            # unpack
            departures = self.departures[stop_name]

            # sort the list of departures.  in-place sort.
            departures.sort(key = get_minutes)

    def _refresh_time(self):


        timestamp = self.time_last_updated.strftime("%Y-%m-%d %H:%M:%S")
        more_text = ''
        if self.mock_update:
            more_text = "`mock_update` is set to true. "

        self.time_updated_widget.setText(f'{more_text}{timestamp}')




    def change_page(self, increment):
        self.current_page = (increment + self.current_page + self.num_pages_needed) % self.num_pages_needed
        
        if self.is_data_cleared or (datetime.now()-self.time_last_updated) > timedelta(milliseconds=self.refresh_interval_ms):
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
        """
        re-paint the data into the table.  assumes the data is already refreshed.  
        this uses the existing data in the map self.departures
        """

        if stop_ind >= len(self.stops_to_monitor):
            return

        table_ind = stop_ind % self.num_stops_per_page
        table = self.tables[table_ind]  # now a StopDisplay
        
        stop_name = self.stops_to_monitor[stop_ind]
        self.header_widgets[table_ind].setText(stop_name)

        table.clear()  # clear old data

        departures = self.departures[stop_name]

        for ii, departure in enumerate(departures[:self.num_departures_to_monitor]):
            row = ii % self.num_rows_per_table
            col_group = ii // self.num_rows_per_table
            
            for shift, c in enumerate(self.columns):
                val = c.getter(departure)
                grid_col = col_group * len(self.columns) + shift
                table.set_cell(row, grid_col, f'{val}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DVB Monitor')
    parser.add_argument(
        '--config',
        default='config.yaml',
        help='path to config yaml file (default: config.yaml)'
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)

    ex = DVB_Monitor(app, config_path=args.config)
    sys.exit(app.exec_())
    
