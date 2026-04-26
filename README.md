# A DVB Haltestelle Departures display application through PyQt5.  

## How to use

Simply run the Python code. 

- Make sure that `config.yaml` is at the current path; suggested to derive from the one in this repo.
- You'll also need to add a string value to `config.yaml`.  They setting name is `dvb_client_name`, and the value is the name of your app and your contact information.  This is required by DVB.  See [the dvb package documentation](https://pypi.org/project/dvb/)

## Installation

Requires the following packages, avail through pip or perhaps mamba/etc:

- `dvb`.  Currently using version 3.
- `pyyaml`
- `numpy`

and also def installed in a mamba env:
- `pyqt5`


## Credits

Derived from a version graciously provided by William Nash.
