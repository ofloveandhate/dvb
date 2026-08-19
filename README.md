# A DVB Haltestelle Departures display application through PyQt5.  

## How to use

Two steps:

**1. Tell DVB who you are.** They require every client to identify itself with a name and a
contact address. Put yours in `dvb_client_name.txt`, next to `DVB_Monitor.py`:

```
echo 'DVB Monitor - your name <you@example.com>' > dvb_client_name.txt
```

That file is gitignored, so your contact details stay out of the repo no matter what you commit.
See [the dvb package documentation](https://pypi.org/project/dvb/) for what they expect.

**2. Run it.** `config.yaml` is read from the current directory by default; point somewhere else
with `--config myconfig.yaml`, or write a fresh one with `--generate-config myconfig.yaml`.

Because your contact details are not in it, every `config.yaml` in this repo is safe to commit
and to share.

### Where the client name is looked up

In order, first hit wins:

1. `dvb_client_name.txt` beside the config file you are using
2. `dvb_client_name.txt` beside `DVB_Monitor.py` -- the usual case, one file shared by every
   config in your checkout
3. a `dvb_client_name:` entry in the config file itself

The third is only kept so that setups predating the separate file keep working. Prefer the file;
a config with your email in it is a config you cannot share.

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
⚠️  the table is 520px wide but the window is only 480px.  Fixes: narrow the columns in your
    config, reduce column_group_spacing (currently 20), or raise window_width.
```

The shipped `config*.yaml` files and the `--generate-config` defaults are all sized to fit their
own windows, so a fresh setup starts silently. If you widen a column or raise a font, these
warnings tell you what no longer fits.

### High-DPI screens

Font sizes in the stylesheets are in `pt`, which Qt converts to pixels using your screen's DPI.
Every size in a config file is in pixels, which it does not. On a 192dpi laptop that means the
text comes out twice the size while the columns stay put -- rows overflow, and destinations get
cut off.

So config pixel sizes are treated as being written for a 96dpi screen, and scaled to whatever you
actually have. On an ordinary display nothing changes. On a 192dpi one, a `window_width: 550`
window is built 1100px wide, and every column, row and gap grows to match, so the layout keeps
its proportions and its physical size.

| setting | default | meaning |
| --- | --- | --- |
| `scale_with_screen_dpi` | true | scale config pixel sizes to the screen |
| `reference_dpi` | 96 | the DPI those pixel sizes were written for |

Set `scale_with_screen_dpi: false` when the numbers *are* the hardware and must be taken
literally -- `config_pitft.yaml` does this, because the PiTFT is exactly 480x320 physical pixels.

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
