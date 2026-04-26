import sys
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QGroupBox, QDialog, QVBoxLayout, QGridLayout, QMainWindow, QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QShortcut
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtCore import pyqtSlot, QTimer
from PyQt5.QtCore import Qt
import dvb
from datetime import datetime, timezone, timedelta


from collections import namedtuple

import numpy as np # for infinity

occupancy_emoji = {
    'StandingOnly': '🕴️',
    'ManySeats': '💺',
    'Unknown': ''
}

mode_emoji = {
    'Tram': '🚋',
    'CityBus': '🚌',
    'PlusBus': '🚎'
}


display = {
    'PiTFT Plus': [0, 0, 480, 320] #https://www.adafruit.com/product/2441
}


DEFAULT_CONFIG = {
    "stops_to_monitor": ["Altmarkt"],
    "row_height": 30,
    "num_rows_per_table": 6,
    "num_stops_per_page": 1,
    "consecutive_autorefresh_timeout_threshold": 10,
    "refresh_interval": 60,
    "clear_interval": 120,
    # there is no default dvb client name, i want my user to have to make the entry themselves, so they don't use my email address.
}









Column = namedtuple("Column", ["header","width","getter", "alignment"])

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



class DVB_Monitor(QMainWindow):

    def __init__(self):
        super().__init__()

        self.title = "DVB Local Stop Monitor"

        self.num_departures_to_monitor = 12 
        self.never_update = True # set to true to only ever get the departures once.  keeps from requesting repeatedly while in development





        self.setup_user_config()

        self.columns = [
                        Column('#'   ,35,get_line,         Qt.AlignHCenter | Qt.AlignBottom),
                        Column(''    ,30,get_mode_emoji,   Qt.AlignLeft | Qt.AlignBottom),
                        Column('Mins',30,get_minutes,      Qt.AlignRight | Qt.AlignBottom),
                        Column('Dest',140,get_destination, Qt.AlignLeft | Qt.AlignBottom),
                        ]


        # holds some state through the loop
        self.time_last_updated = None
        self.current_page      = 0
        self.departures        = {} # holds the departures, per-stop.
        self.num_consecutive_autorefreshes = 0
        self.is_data_cleared = True

        self.left = self.right = self.width = self.height = None

        #
        #  internal variables for holding Qt objects
        #  
        self.main_layout            = None # will hold all the other layouts
        self.tables_layout          = None # holds the layouts per table on page.
        self.buttons                = None
        self.layout_per_haltestelle = None
        self.header_widgets         = None
        self.time_updated_widget    = None
        self.horizontalGroupBox     = None

        # some helper variables so don't need to keep recomputing them

        self.num_cols_per_col = len(self.columns)  # because each departure gets this many, and we use multiple cols of departures

        self.num_cols_needed = self.num_departures_to_monitor // self.num_rows_per_table
        self.is_nav_needed = len(self.stops_to_monitor) > self.num_stops_per_page
        self.is_nav_needed_prev = len(self.stops_to_monitor) > self.num_stops_per_page
        self.is_nav_needed_next = len(self.stops_to_monitor) > self.num_stops_per_page + 1
        self.num_pages_needed = len(self.stops_to_monitor) // self.num_stops_per_page
        self.refresh_interval_ms = self.refresh_interval * 1000
        self.clear_interval_ms = self.clear_interval * 1000


        # the core of this display.  use this object to make queries into the DVB api.
        self.client = dvb.Client(user_agent=self.dvb_client_name)
        self.initUI()
    
    def setup_user_config(self, path="config.yaml"):
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

        

        config = DEFAULT_CONFIG.copy()
        config.update(user_config)


        self.stops_to_monitor = config["stops_to_monitor"]
        self.row_height = config["row_height"]
        self.num_rows_per_table = config["num_rows_per_table"]
        self.num_stops_per_page = config["num_stops_per_page"]
        self.consecutive_autorefresh_timeout_threshold = config["consecutive_autorefresh_timeout_threshold"]
        self.refresh_interval = config["refresh_interval"]
        self.clear_interval = config["clear_interval"]

        self.dvb_client_name = config["dvb_client_name"]  # there should be no default for this, because the user is supposed to give contact into in this strong.


    def setup_window_size(self):

        # get from the dict at the top
        params = display['PiTFT Plus']

        self.left   = params[0]
        self.top    = params[1]
        self.width  = params[2]
        self.height = params[3]

    def init_tables(self):
        
        # make a new layout to hold this
        self.tables_layout = QHBoxLayout()

        # make the tables 
        self.tables = {}

        # m
        self.layout_per_haltestelle = {}
        self.header_widgets = {}

        for ii in range( min(self.num_stops_per_page, len(self.stops_to_monitor))):

            self.layout_per_haltestelle[ii] = QVBoxLayout() # make and store
            this_layout = self.layout_per_haltestelle[ii] # unpack

            self.setup_table(ii)

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

        table.setRowCount(self.num_rows_per_table)
        table.setColumnCount(self.num_cols_per_col * self.num_cols_needed)

        

        for jj in range(self.num_rows_per_table):
            table.setRowHeight(jj, self.row_height) # magic constant

        self.set_column_labels(table)


        # Make table non-editable
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        # Hide row labels (vertical header)
        table.verticalHeader().setVisible(False)

        table.horizontalHeader().setStretchLastSection(False)
        table.verticalHeader().setStretchLastSection(False)

        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)

        

        return table
        # Set table properties
        # table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_column_labels(self,table):

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

    def setup_timeupdated(self):
        self.time_updated_widget = QLabel()

        w = self.time_updated_widget
        w.setProperty('class', 'footer')
        w.setAlignment(Qt.AlignCenter)

        self.main_layout.addWidget(self.time_updated_widget)  # Add text widget here

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



    def initUI(self):

        self.setup_window_size()

        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)
        


        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)


        self.escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.escape_shortcut.activated.connect(QApplication.quit)

        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)  # This removes padding around the layout
        self.setLayout(self.main_layout)
        

        self.init_tables()
        self.setup_bottom()
        


        

        self.timer_refresh = QTimer(self)
        self.timer_refresh.setSingleShot(True)
        self.timer_refresh.timeout.connect(self.auto_refresh)

        self.timer_stale_data = QTimer(self)
        self.timer_stale_data.setSingleShot(True)
        self.timer_stale_data.timeout.connect(self.clear_stale_data)
        

        self.auto_refresh() # kick it off!
        


    



    def clear_stale_data(self):
        self.is_data_cleared = True
        self.departures = {}

        for ind,t in self.tables.items():
            for row in range(t.rowCount()):
                for col in range(t.columnCount()):
                    item = t.item(row, col)
                    if item is not None:
                        item.setText("")


    def auto_refresh(self):
        print('auto_refresh', self.num_consecutive_autorefreshes)

        self.refresh()
        self.num_consecutive_autorefreshes += 1

        if self.num_consecutive_autorefreshes < self.consecutive_autorefresh_timeout_threshold:
            self.timer_refresh.start(self.refresh_interval_ms)
            self.timer_stale_data.stop()
        else:
            print('ceasing to auto_refresh')
            self.timer_stale_data.start(self.clear_interval_ms)

        





    def manual_refresh(self):
        print('manual_refresh')

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

            if not self.never_update or stop_name not in self.departures:
                print(f'getting departures for {stop_name}')
                self.departures[stop_name] = self.client.monitor(stop=stop_name,limit=0)
            else:
                print(f'mock getting departures for {stop_name}')

            # unpack
            departures = self.departures[stop_name]

            # sort the list of departures.  in-place sort.
            departures.sort(key = get_minutes)

    def _refresh_time(self):
        self.time_last_updated = datetime.now()
        timestamp = self.time_last_updated.strftime("%Y-%m-%d %H:%M:%S")
        self.time_updated_widget.setText(timestamp)




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

        # unpack to make shorter
        table = self.tables[table_ind]

        stop_name = self.stops_to_monitor[stop_ind]

        w = self.header_widgets[table_ind]
        w.setText(stop_name)


        departures = self.departures[stop_name] # unpack to make shorter

        # now we set the data in the tables from the departure list
        for ii,departure in enumerate(departures[:self.num_departures_to_monitor]):

            row = ii%self.num_rows_per_table
            col = ii//self.num_rows_per_table * self.num_cols_per_col

            for shift,c in enumerate(self.columns):
                val = c.getter(departure) # get the value using the getter function

                item = table.item(row, col+shift) # get the item from the table.  assumes it was created above in the init routines.

                #finally, set the value.
                item.setText(f'{val}')


if __name__ == '__main__':
    app = QApplication(sys.argv)


    with open("style.css",'r') as f:
        app.setStyleSheet(f.read())

    ex = DVB_Monitor()
    sys.exit(app.exec_())
    
