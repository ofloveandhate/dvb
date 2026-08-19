# A DVB Haltestelle Departures display application through PyQt5.  

## How to use

Simply run the Python code. 

- Make sure that `config.yaml` is at the current path; suggested to derive from the one in this repo.
- You'll also need to add a string value to `config.yaml`.  They setting name is `dvb_client_name`, and the value is the name of your app and your contact information.  This is required by DVB.  See [the dvb package documentation](https://pypi.org/project/dvb/)

Point it at a different config with `--config myconfig.yaml`, or write a fresh one with
`--generate-config myconfig.yaml`.

## Installation

Requires the following packages, avail through pip or perhaps mamba/etc:

- `dvb`.  Currently using version 3.
- `pyyaml`
- `numpy`
- `requests` and `pyproj`, both required by `dvb` itself.  `pyproj` needs the PROJ binaries, so
  install it from conda-forge rather than letting pip build it -- especially on a Raspberry Pi.

and also def installed in a mamba env:
- `pyqt5`

In a mamba/micromamba env, that is:

```
micromamba create -n dvb -c conda-forge python=3.12 pyqt pyyaml numpy requests pyproj
micromamba run -n dvb pip install --no-deps dvb
```

(the conda-forge package for PyQt5 is called `pyqt`, not `pyqt5`.)

## When the DVB API misbehaves

The API goes down, rate limits, and occasionally returns something that isn't JSON.  None of that
stops the display any more:

- a failing stop no longer prevents the other stops from updating
- the last known departures stay on screen, with the stop name marked ⚠️, and the footer showing
  when the data was last good
- a stop with nothing to show at all is marked ❌ with the reason underneath
- refreshes back off exponentially while everything is failing, and return to the normal interval
  as soon as anything succeeds
- fetching happens on a background thread, so the window stays responsive even when the API hangs

Relevant `config.yaml` settings, all optional:

| setting | default | meaning |
| --- | --- | --- |
| `request_timeout` | 8 | seconds to wait for a response body (the `dvb` package's own default is 15) |
| `request_connect_timeout` | 4 | seconds to wait for the connection |
| `cache_stop_ids` | true | look up each stop's id once instead of on every request |
| `retry_backoff_factor` | 2.0 | multiplier applied to the interval while every stop is failing |
| `retry_backoff_max` | 600 | seconds; ceiling on that backoff |
| `retry_when_stale` | false | keep retrying after stale data has been cleared |
| `fetch_in_background` | true | fetch off the GUI thread; set false to go back to blocking fetches |
| `stale_data_threshold` | 300 | seconds before displayed data is marked stale |
| `show_stale_data_on_error` | true | keep the last good departures visible when a fetch fails |
| `error_placeholder` | `—` | drawn in a cell whose value could not be computed |
| `unknown_mode_emoji` | `` | drawn for a mode of transit with no emoji |

## Changing how it looks

`style.css` is the default; `style_pitft.css` is tuned for the 480x320 Adafruit PiTFT. Point at
one with `css_file:` in your config.

It is Qt's stylesheet language (QSS), which looks like CSS but has no variables, so colours are
written out in full each time. Each file opens with the four rules worth changing first --
departure rows, column headings, the stop name, and the timestamp -- and everything after that is
chrome.

Rows grow to fit whatever `font-size` you set, so making the departure text bigger just works;
`row_height` in your config is treated as a minimum rather than a fixed size. Column *widths* are
a different matter: they come from the `columns:` section of your config and cannot grow on their
own, because the window width is fixed. If the text outgrows them, the app says so on startup:

```
⚠️  column "Dest" is 90px wide but its heading needs 140px at the current font; text will be cut
    off.  Widen it in your config, or reduce font-size in style.css.
⚠️  the table is 512px tall but the window is only 400px.  Fixes: reduce font-size in style.css,
    reduce num_rows_per_table (currently 12), or raise window_height.
```

Only these style classes exist; anything else in a stylesheet is ignored:

| class | what it is |
| --- | --- |
| `grid_cell` | the departure rows |
| `grid_header` | the `#` / `Dest` / `t` headings |
| `haltestelle_header` | the stop name |
| `haltestelle_header_stale` | stop name, showing old data after a failed fetch |
| `haltestelle_header_error` | stop name, failed with nothing left to show |
| `footer` | the timestamp line |

## Development

`--fake-client {ok,fail,mixed,empty,slow}` runs the whole UI against a fake API, with no network
at all, which is the easiest way to see each of the states above:

```
python DVB_Monitor.py --fake-client mixed
```

Tests:

```
QT_QPA_PLATFORM=offscreen pytest tests/
```

## Credits

Derived from a version graciously provided by William Nash.
