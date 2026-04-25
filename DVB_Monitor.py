import sys
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QGroupBox, QDialog, QVBoxLayout, QGridLayout, QMainWindow, QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QShortcut
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtCore import pyqtSlot, QTimer
from PyQt5.QtCore import Qt
import dvb
from datetime import datetime, timezone


import numpy as np

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



def get_line_w_mode(departure):
    try:
        e = mode_emoji[departure.mode]
    except Exception as e:
        print(f'unfound emoji for mode {departure.mode}')

    return f'{departure.line} {e}'
    

def time_to_depart(departure):
    """
    compute the number of minutes, rounded down via integer arithmetic, to departure.

    problem: if the real_time is none, then this may fail.  so use a try/except around this.
    """

    if departure.real_time:
        minutes = (departure.real_time - datetime.now(timezone.utc)).total_seconds() // 60 + 1 
        # adding +1 to make match the iphone app
    else:
        return np.inf

    
    return minutes


class App(QMainWindow):

    def __init__(self):
        super().__init__()

        self.title = "DVB Local Stop Monitor"
        self.left = self.right = self.width = self.height = None

        self.num_departures_to_monitor = 12

        self.horizontalGroupBox = None
        self.departures = {}

        self.never_update = True # set to true to only ever get the departures once.  keeps from requesting for no reason.

        self.main_layout = None # will hold all the other layouts

        self.tables_layout = None # holds the layouts per table on page.
        self.buttons = None
        self.layout_per_haltestelle = None
        self.header_widgets = None

        self.time_updated_widget = None

        self.css = {}
        with open("table.css",'r') as f:
            self.css['table'] = f.read()

        


        self.row_height = 30
        self.num_rows_per_table = 6
        self.num_cols_per_col = 3  # because each departure gets this many, and we use multiple cols of departures

        self.num_cols_needed = self.num_departures_to_monitor // self.num_rows_per_table

        self.stops_to_monitor = [
                                'Pirnaischerplatz',
                                'Pragerstr', #pragerstr
                                'Altmarkt',                              
                                # 'Albertplatz',
                                # 'Bautzner Straße/Rothenberger Straße'
                                ]

        self.current_page = 0
        self.num_stops_per_page = 1

        self.num_pages_needed = len(self.stops_to_monitor) // self.num_stops_per_page

        self.client = dvb.Client(user_agent="dvb_testing/2026.04.25 silviana amethyst (amethyst@mpi-cbg.de)")
        self.initUI()
        
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

        for ii in range(self.num_stops_per_page):

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

        table.setStyleSheet(self.css['table'])

        table.horizontalHeader().setStretchLastSection(False)
        table.verticalHeader().setStretchLastSection(False)

        table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)

        

        return table
        # Set table properties
        # table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_column_labels(self,table):

        
        for ii in range(self.num_cols_needed):

            table.setHorizontalHeaderItem(0+ii*3, QTableWidgetItem(f"#"))
            table.setColumnWidth(0+ii*3, 70)
            table.setHorizontalHeaderItem(1+ii*3, QTableWidgetItem(f"Dest"))
            table.setColumnWidth(1+ii*3, 120)
            table.setHorizontalHeaderItem(2+ii*3, QTableWidgetItem(f"Mins"))
            table.setColumnWidth(2+ii*3, 40)


        width = sum( table.columnWidth(col) for col in range(table.columnCount()))
            

        table.setFixedSize(width + 20, self.row_height * (self.num_rows_per_table+1))

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

        
        self.buttons['prev'] = QPushButton()
        self.buttons['prev'].clicked.connect(lambda x: self.change_page(-1))

        self.buttons['next'] = QPushButton()
        self.buttons['next'].clicked.connect(lambda x: self.change_page(+1))

        self.buttons['refresh'] = QPushButton("🥀")
        self.buttons['refresh'].clicked.connect(self.rebuild)

        self.bottom_layout.addWidget(self.buttons['prev'])
        self.bottom_layout.addWidget(self.buttons['refresh'])
        self.bottom_layout.addWidget(self.buttons['next'])

        self.main_layout.addLayout(self.bottom_layout)

    def change_page(self, increment):
        self.current_page = (increment + self.current_page + self.num_pages_needed) % self.num_pages_needed
        
        self.rebuild()

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
        


        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rebuild)
        self.timer.start(120 * 1000) # writing this way to make easier to reason about number of seconds

        self.rebuild()

        self.show()#FullScreen()


    def rebuild(self):

        for table_ind in range(self.num_stops_per_page):
            stop_ind = self.current_page*self.num_stops_per_page + table_ind

            self.repop_table(stop_ind)
        

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_updated_widget.setText(timestamp)

        self.buttons['prev'].setText(self.stops_to_monitor[(self.current_page*self.num_stops_per_page-1) % len(self.stops_to_monitor)])
        self.buttons['next'].setText(self.stops_to_monitor[(self.current_page*self.num_stops_per_page+self.num_stops_per_page) % len(self.stops_to_monitor)])

        self.update()
        
    def repop_table(self, stop_ind):

        table_ind = stop_ind % self.num_stops_per_page

        # unpack to make shorter
        table = self.tables[table_ind]

        self.set_column_labels(table)

        stop_name = self.stops_to_monitor[stop_ind]

        w = self.header_widgets[table_ind]
        w.setText(stop_name)

        if not self.never_update or stop_name not in self.departures:
            print(f'getting departures for {stop_name}')
            self.departures[stop_name] = self.client.monitor(stop=stop_name,limit=self.num_departures_to_monitor)
        else:
            print(f'mock getting departures for {stop_name}')

        # unpack
        departures = self.departures[stop_name]

        # sort the list of departures
        departures.sort(key = time_to_depart)

        # now we set the data in the tables from the departure list

        for ii,departure in enumerate(departures[:self.num_departures_to_monitor]):


            row = ii%self.num_rows_per_table

            
            col = ii//self.num_rows_per_table * self.num_cols_per_col
            table.setItem(row, col+0, QTableWidgetItem(get_line_w_mode(departure)))
            table.setItem(row, col+1, QTableWidgetItem(departure.direction))
            try:
                minutes = time_to_depart(departure)
                mins_text = f'{minutes:0.0f}'

            except Exception as e:
                mins_text = f'{departure.state}'

            table.setItem(row, col+2, QTableWidgetItem(mins_text))
    

        
    # def createLargeGridLayout(self):
    #     self.horizontalGroupBox = QWidget()
    #     layout = QGridLayout()
        

    #     left = self.createStopWidget(self.stops_to_monitor[0], 10)
    #     right = self.createStopWidget(self.stops_to_monitor[1], 10)#Bautzner Straße/Rothenberger Straße

    #     bottom = self.createFooter()
    #     layout.addWidget(left,0,0)
    #     layout.addWidget(right,0,1)
    #     layout.addWidget(bottom,1,1)
        
    #     self.horizontalGroupBox.setLayout(layout)

    # def createStopWidget(self, stop_name, num_results):
    #     box = QGroupBox()
    #     layout = QVBoxLayout()
    #     layout.addWidget(self.createLabel(stop_name, 'title'))
        
    #     layout.addWidget(self.createDepartureHeader())
        
    #     if not self.never_update:
    #         print(f'getting departures for {stop_name}')
    #     self.departures[stop_name] = self.client.monitor(stop=stop_name,limit=4)



    #     departures = self.departures[stop_name]

    #     departures.sort(key = time_to_depart)

    #     for departure in departures:
    #         print(departure)
    #         # if departure['arrival'] == 0:
    #         #     departure['arrival'] = 'Due'
    #         temp_name = departure.direction
    #         layout.addWidget(self.createDepartureWidget(departure)) 
                                
    #     box.setLayout(layout)
    #     return box
        
        
    # def createDepartureWidget(self, departure):
    #     box = QGroupBox()
    #     layout = QHBoxLayout()
        

    #     layout.addWidget(self.createLabel(departure.line, 'body'))
    #     layout.addWidget(self.createLabel(departure.direction, 'body'))

    #     try:
    #         minutes = time_to_depart(departure)
    #         layout.addWidget(self.createLabel(f'{minutes:0.0f}', 'body'))
    #     except Exception as e:
    #         layout.addWidget(self.createLabel(f'{departure.state}', 'body'))

    #     # minutes_diff = (datetime_end - datetime_start).total_seconds() / 60.0
    #     # layout.addWidget(self.createLabel(str(departure['arrival']), 'body'))

                
    #     box.setLayout(layout)
    #     return box


    # def createDepartureHeader(self):
    #     box = QGroupBox()
    #     layout = QHBoxLayout()  
    #     layout.addWidget(self.createLabel('Route', 'header'))
    #     layout.addWidget(self.createLabel('Destination', 'header'))
    #     #layout.addWidget(self.createLabel('Scheduled', 'header'))
    #     layout.addWidget(self.createLabel('Expected', 'header'))
        
    #     box.setLayout(layout)
    #     return box

    # def createLabel(self, text, style_class):
    #     label = QLabel(text)
    #     label.setProperty('class', style_class)
    #     return label

    # def createFooter(self):
    #     box = QGroupBox()
    #     layout = QHBoxLayout()
    #     now = datetime.now()
    #     timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    #     layout.addWidget(self.createLabel(timestamp, 'footer'))
        
    #     box.setLayout(layout)
    #     return box

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet(
    '''
    QGroupBox{background-color: black;}
    QLabel.header{font-size: 27pt; color: white; background-color: blue;}
    QLabel.title{font-size: 24pt; color: white;}
    QLabel.haltestelle_header{font-size: 18pt; color: white;}

    QLabel.body{font-size: 23pt; color: white;}
    QLabel.footer{font-size: 8pt; color: white;}
    ''')


    ex = App()
    sys.exit(app.exec_())
    
