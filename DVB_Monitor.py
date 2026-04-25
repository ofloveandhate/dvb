import sys
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QHBoxLayout, QGroupBox, QDialog, QVBoxLayout, QGridLayout
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import pyqtSlot, QTimer
import dvb
from datetime import datetime, timezone


occupancy_emoji = {
    'StandingOnly': '🕴️',
    'ManySeats': '💺',
    'Unknown': ''
}

mode_emoji = {
    'Tram': '🚋',
    'Bus': '🚌'
}

def time_to_depart(departure):
    """
    compute the number of minutes, rounded down via integer arithmetic, to departure.

    problem: if the real_time is none, then this may fail.  so use a try/except around this.
    """

    minutes = (departure.real_time - datetime.now(timezone.utc)).total_seconds() // 60 + 1 # adding +1 to make match the iphone app
    return minutes


class App(QDialog):

    def __init__(self):
        super().__init__()

        self.title = 'PyQt5 layout - pythonspot.com'
        self.left = 10
        self.top = 10
        self.width = 420
        self.height = 120
        self.horizontalGroupBox = None
        self.departures = {}

        self.never_update = True


        self.stops_to_monitor = [
                                '33000003', #pragerstr
                                'Albertplatz'
                                ]
        self.client = dvb.Client(user_agent="dvb_testing/2026.04.25 silviana amethyst (amethyst@mpi-cbg.de)")

        self.initUI()
        


        
    def initUI(self):
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)
        
        self.windowLayout = QVBoxLayout()
        self.setLayout(self.windowLayout)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rebuild)
        self.timer.start(120 * 1000) # writing this way to make easier to reason about number of seconds

        self.rebuild()
        self.show()#FullScreen()




    def rebuild(self):
        print('swans')
        
        if self.horizontalGroupBox is not None:
            self.horizontalGroupBox.deleteLater()
        self.createLargeGridLayout()
        self.windowLayout.addWidget(self.horizontalGroupBox)
        self.update()
        

    
    def createLargeGridLayout(self):
        self.horizontalGroupBox = QWidget()
        layout = QGridLayout()
        

        left = self.createStopWidget(self.stops_to_monitor[0], 10)
        right = self.createStopWidget(self.stops_to_monitor[1], 10)#Bautzner Straße/Rothenberger Straße

        bottom = self.createFooter()
        layout.addWidget(left,0,0)
        layout.addWidget(right,0,1)
        layout.addWidget(bottom,1,1)
        
        self.horizontalGroupBox.setLayout(layout)

    def createStopWidget(self, stop_name, num_results):
        box = QGroupBox()
        layout = QVBoxLayout()
        layout.addWidget(self.createLabel(stop_name, 'title'))
        
        layout.addWidget(self.createDepartureHeader())
        
        if not self.never_update:
            print(f'getting departures for {stop_name}')
        self.departures[stop_name] = self.client.monitor(stop=stop_name,limit=4)



        departures = self.departures[stop_name]

        departures.sort(key = time_to_depart)

        for departure in departures:
            print(departure)
            # if departure['arrival'] == 0:
            #     departure['arrival'] = 'Due'
            temp_name = departure.direction
            layout.addWidget(self.createDepartureWidget(departure)) 
                                
        box.setLayout(layout)
        return box
        
        
    def createDepartureWidget(self, departure):
        box = QGroupBox()
        layout = QHBoxLayout()
        
        from datetime import datetime, timezone

        


        layout.addWidget(self.createLabel(departure.line, 'body'))
        layout.addWidget(self.createLabel(departure.direction, 'body'))

        try:
            minutes = time_to_depart(departure)
            layout.addWidget(self.createLabel(f'{minutes:0.0f}', 'body'))
        except Exception as e:
            layout.addWidget(self.createLabel(f'{departure.state}', 'body'))

        # minutes_diff = (datetime_end - datetime_start).total_seconds() / 60.0
        # layout.addWidget(self.createLabel(str(departure['arrival']), 'body'))

                
        box.setLayout(layout)
        return box


    def createDepartureHeader(self):
        box = QGroupBox()
        layout = QHBoxLayout()  
        layout.addWidget(self.createLabel('Route', 'header'))
        layout.addWidget(self.createLabel('Destination', 'header'))
        #layout.addWidget(self.createLabel('Scheduled', 'header'))
        layout.addWidget(self.createLabel('Expected', 'header'))
        
        box.setLayout(layout)
        return box

    def createLabel(self, text, style_class):
        label = QLabel(text)
        label.setProperty('class', style_class)
        return label

    def createFooter(self):
        box = QGroupBox()
        layout = QHBoxLayout()
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        layout.addWidget(self.createLabel(timestamp, 'footer'))
        
        box.setLayout(layout)
        return box

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