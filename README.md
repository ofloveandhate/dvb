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

On a touchscreen with `use_backlight_control`, the display sleeps once its data has gone stale.
Tapping to wake it also refreshes, so you get current departures rather than an empty table and a
button to press. That first tap only wakes; it does not fall through to whatever was underneath
it.

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

## Arrivals with no time

The API does not always give a real time for a departure. Those get an infinite number of
minutes, which sorts them to the bottom of the table and shows as `inf`.

| setting | default | meaning |
| --- | --- | --- |
| `show_infinite_arrivals` | true | show departures the API gave no time for |

Set it to `false` to leave them out. They sort last either way, so hiding them never displaces a
departure that does have a time -- it just frees up the rows at the bottom they were occupying.

## Example configurations

Two config/stylesheet pairs to copy and cut about, both using every column the app has --
line number, mode emoji, direction arrow, destination and minutes -- and both with more stops
than fit on a page, so the prev/next buttons are in play.

```
python DVB_Monitor.py --config config_showcase.yaml
python DVB_Monitor.py --config config_tutorial.yaml
```

Add `--fake-client ok` to either to try it without touching the network.

| pair | for |
| --- | --- |
| `config_showcase.yaml` + `style_showcase.css` | a wide departure-board look, four stops two at a time. Start here if you want something good-looking to adapt. |
| `config_tutorial.yaml` + `style_tutorial.css` | the same features with the reasoning left in: seven numbered experiments in the config, four more in the stylesheet, each one a single edit. Start here if you want to understand why. |

The tutorial pair ships with its emoji and arrow columns commented out, so uncommenting them is
one of the experiments. Every experiment in both files has been run -- changing `num_stops_per_page`
really does make the nav buttons disappear, 18pt really does grow the rows and then warn you that
they no longer fit.

One limitation worth knowing before you spend long on a stylesheet: **every departure cell shares
a single style class**, so you cannot make the minutes bold and the destination plain. Per column,
the stylesheet cannot tell them apart. Width, alignment and eliding are per column, in the yaml.

The showcase raises `direction_lookups_per_refresh` well above the default, so its arrows fill in
on the second refresh rather than trickling in over ten of them. That is right for a board you are
looking at and wrong for one you leave running -- the default is deliberately gentler.

## Direction arrows

An optional column showing which way each service leaves the stop, as a little arrow:

```yaml
  - header: ""
    width: 24
    getter: get_direction_arrow
    alignment: center
```

`config_pitft.yaml` has it switched on. Add the column to any other config to get it there too --
it is off unless a column asks for it, and costs nothing at all if you leave it out.

### How it works, and what it costs

The API does not say which way a departure is heading, so the app works it out: it asks for the
stops along the trip, takes the one after yours, and turns the compass bearing between the two
into one of eight arrows. Your stop's coordinates arrive with the stop-id lookup the app already
does, so they are free.

The trip lookup is not free, so it happens **once per route per stop**, not once per departure.
Every upcoming service of the same line and direction reuses the answer. At Altmarkt that turns
fifteen departures into six lookups; at Pirnaischerplatz, into eleven.

| setting | default | meaning |
| --- | --- | --- |
| `direction_recompute_interval` | 3600 | seconds before a cached arrow is worked out again; `0` never recomputes |
| `direction_lookups_per_refresh` | 4 | most lookups any one refresh may make |

Once the arrows are known, a refresh costs exactly what it always did: **one API call per stop**.
The lookups only happen when an arrow is new or has gone stale.

`direction_lookups_per_refresh` spreads the cold start out rather than firing everything at once.
Three stops with 29 routes between them fill in over about eight refreshes, drawing blank arrows
until each one resolves.

`direction_recompute_interval` exists because routes do change -- a line can take a different path
once the night schedule starts, without changing its number or its destination. Recomputing every
hour bounds how long an arrow can be wrong. Set it to `0` if you would rather never spend the
calls, and restart the app when the timetable changes.

### What it does not do

Arrows are drawn for trams and buses only. Trains run express and stopping services under the
same line and destination, so a single cached arrow would be wrong for some of them and there is
no cheap way to tell which.

The arrow is the direction the service leaves *this* stop, which is not always the direction of
its destination -- a route that loops or doubles back will point somewhere that looks surprising
until you follow it on a map.

Anything the app cannot work out is simply left blank: a terminus, a stop missing from the trip
data, or a lookup that failed.

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

### Room around the buttons

| setting | default | meaning |
| --- | --- | --- |
| `button_margin` | 8 | pixels of room around the row of buttons along the bottom |
| `button_spacing` | 6 | pixels between one button and the next |
| `refresh_button_width` | 64 | width of the refresh button |

The refresh button holds a single glyph, so it keeps a fixed width and the prev/next buttons --
which carry stop names -- take whatever room is left. With only one stop group to show there are
no prev/next buttons at all, and the refresh button stays its own size in the middle rather than
stretching the width of the window. It never shrinks below what the glyph needs, whatever you
set.

These are config settings rather than stylesheet rules so that they scale with the screen like
every other pixel size. In CSS they would be stuck at one size on a high-DPI display while
everything around them grew.

Height at the bottom is the scarcest thing on a small screen. `config_pitft.yaml` uses a 6px
margin and a 12pt departure font, which is what fits six rows, a heading, the timestamp and a row
of touch-sized buttons into 320px. If the contents outgrow the window and it cannot grow -- which
is the case in full screen -- the app says so on startup.

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
