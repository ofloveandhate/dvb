import sys
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QGroupBox, QDialog, QVBoxLayout, QGridLayout, QMainWindow, QTableWidget, QTableWidgetItem
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import pyqtSlot, QTimer
from PyQt5.QtCore import Qt
import dvb
from datetime import datetime, timezone


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

    minutes = (departure.real_time - datetime.now(timezone.utc)).total_seconds() // 60 + 1 # adding +1 to make match the iphone app
    return minutes


class App(QMainWindow):

    def __init__(self):
        super().__init__()

        self.title = "DVB Local Stop Monitor"
        self.left = 10
        self.top = 10
        self.width = 500
        self.height = 300

        self.num_departures_to_monitor = 12

        self.horizontalGroupBox = None
        self.departures = {}

        self.never_update = True # set to true to only ever get the departures once.  keeps from requesting for no reason.

        self.main_layout = None # will hold all the other layouts

        self.tables_layout = None # holds the layouts per haltestelle

        self.layout_per_haltestelle = None
        self.header_widgets = None

        self.time_updated_widget = None




        


        self.num_rows_per_table = 6
        self.num_cols_per_col = 3  # because each departure gets this many, and we use multiple cols of departures

        self.num_cols_needed = self.num_departures_to_monitor // self.num_rows_per_table

        self.stops_to_monitor = [
                                '33000003', #pragerstr
                                'Albertplatz'
                                ]


        self.client = dvb.Client(user_agent="dvb_testing/2026.04.25 silviana amethyst (amethyst@mpi-cbg.de)")

        self.initUI()
        


    def init_tables(self):
        
        # make a new layout to hold this
        self.tables_layout = QHBoxLayout()

        # make the tables 
        self.tables = {s:QTableWidget() for s in self.stops_to_monitor}

        # m
        self.layout_per_haltestelle = {}
        self.header_widgets = {}

        for s,t in self.tables.items():

            self.setup_table(t, s)

            self.layout_per_haltestelle[s] = QVBoxLayout()

            # unpack
            this_layout = self.layout_per_haltestelle[s]

            self.header_widgets[s] = QLabel(s)
            w = self.header_widgets[s]
            w.setProperty('class', 'haltestelle_header')
            w.setAlignment(Qt.AlignCenter)

            this_layout.addWidget(w)
            this_layout.addWidget(self.tables[s])

            self.tables_layout.addLayout(this_layout)

        self.main_layout.addLayout(self.tables_layout)

    def setup_table(self, table, title):

        table.setRowCount(self.num_rows_per_table)
        table.setColumnCount(self.num_cols_per_col * self.num_cols_needed)


        self.set_column_labels(table)


        # Make table non-editable
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        # Hide row labels (vertical header)
        table.verticalHeader().setVisible(False)

        # Set table properties
        # table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_column_labels(self,table):
        for ii in range(self.num_cols_needed):
            table.setHorizontalHeaderItem(0+ii*3, QTableWidgetItem(f"#"))
            table.setColumnWidth(0+ii*3, 50)
            table.setHorizontalHeaderItem(1+ii*3, QTableWidgetItem(f"Dest"))
            table.setColumnWidth(1+ii*3, 120)
            table.setHorizontalHeaderItem(2+ii*3, QTableWidgetItem(f"Mins"))
            table.setColumnWidth(2+ii*3, 40)


    def setup_timeupdated(self):
        self.time_updated_widget = QLabel()

        w = self.time_updated_widget
        w.setProperty('class', 'footer')
        w.setAlignment(Qt.AlignRight)

        self.main_layout.addWidget(self.time_updated_widget)  # Add text widget here


    def initUI(self):
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)
        
        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)


        self.main_layout = QVBoxLayout(central_widget)
        self.setLayout(self.main_layout)
        

        self.init_tables()
        self.setup_timeupdated()
        


        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rebuild)
        self.timer.start(120 * 1000) # writing this way to make easier to reason about number of seconds

        self.rebuild()

        self.show()#FullScreen()


    def rebuild(self):

        for s in self.stops_to_monitor:
            self.repop_table(s)
        
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.time_updated_widget.setText(timestamp)

        self.update()
        
    def repop_table(self, stop_name):

        # unpack to make shorter
        table = self.tables[stop_name]

        self.set_column_labels(table)


        if not self.never_update or stop_name not in self.departures:
            print(f'getting departures for {stop_name}')
            self.departures[stop_name] = self.client.monitor(stop=stop_name,limit=self.num_departures_to_monitor)


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
    QLabel.title{font-size: 30pt; color: white;}
    QLabel.body{font-size: 23pt; color: white;}
    QLabel.footer{font-size: 8pt; color: white;}
    ''')

    #app.setStyleSheet("background-color: blue")
    ex = App()
    sys.exit(app.exec_())
    
#while True:
#    print('hello')
#    time.sleep(60)