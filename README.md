# Fuel Station Route API

A Django API that plans US driving trips, chooses cost-effective fuel stops using
`fuel-prices-for-be-assessment.csv`, returns a mapped route, and calculates fuel cost.
The vehicle has a **500-mile range**, **50-gallon tank**, and **10 mpg** efficiency.

The project pins **Django 6.1.2**, the latest stable release listed on the
[official download page](https://www.djangoproject.com/download/) when this project
was prepared. Python 3.12 or newer is required.

## Quick start

From this directory, using the existing `.venv`:

```bash
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
python manage.py migrate
python manage.py createcachetable
python manage.py load_places
python manage.py runserver
```

If creating an environment from scratch, run `uv venv --python 3.12 .venv` first.
Alternatively, use `python -m venv .venv` and install requirements with pip.

Visit **http://127.0.0.1:8000/** and choose the start and finish using state and city
dropdowns. City choices load for the selected state, and the form opens the resulting route map.
No routing API key is required.

### CSV first, database backup

Station prices and coordinates are read from CSV into process memory on first use.
The file is parsed once while unchanged; a file replacement or modification reloads
it on the next request. Normal route requests do **not** query the station table.

The source order is:

1. `data/stations.csv`: an enriched CSV containing prices and coordinates.
2. If that file is absent, the original `fuel-prices-for-be-assessment.csv`, with
   coordinates supplied by `data/station-coordinates.csv`.
3. If CSV data is missing, invalid, or contains no located stations, use the
   existing station database as a backup.

A valid but partially located CSV is used as-is: missing station coordinates are
reported in coverage rather than silently mixing two different data snapshots.
`coverage.station_source` reports `csv` or `database` in each response.

**Switch an existing database to CSV without repeating geocoding:**

```bash
python manage.py export_stations
```

This atomically writes `data/stations.csv`, including all known coordinates, and
leaves the database intact. Run it again after importing new prices or geocoding
additional database stations. It refuses to overwrite a CSV from an empty database.

**Use the original CSV directly:** put a verified `opis_id,latitude,longitude`
sidecar at `data/station-coordinates.csv`, and ensure no enriched snapshot takes
precedence (or set `STATIONS_CSV` to a nonexistent path). Station database import
is optional for this path. The original CSV supplies names and prices; the sidecar
supplies positions.

**Keep the backup current after editing an enriched CSV:**

```bash
python manage.py import_stations data/stations.csv
```

Or, when using original prices plus a coordinate sidecar:

```bash
python manage.py import_stations fuel-prices-for-be-assessment.csv --coordinates data/station-coordinates.csv
```

Backup synchronization is explicit; route requests only read station data. A backup
can have older prices if it has not been synchronized. City lookup and route/map
caching still use the database, so this fallback handles station CSV problems;
it does not make the whole application independent of database availability.

On a fresh installation with no coordinates, use a verified enriched CSV or
coordinate sidecar. The optional Census preparation workflow remains available:

```bash
python manage.py import_stations fuel-prices-for-be-assessment.csv
python manage.py geocode_stations --batch-size 50 --timeout 30
python manage.py export_stations
```

### Why there is a preparation step

The supplied CSV has **8,151 rows**, including duplicates and Canadian stations,
and **does not contain latitude or longitude**. Coordinates cannot be inferred
reliably from interstate exit descriptions.

* `import_stations` imports **6,626 unique US stations**, excludes Canadian
  provinces, and keeps the lowest listed price for a duplicate OPIS station ID.
  Import is atomic and repeatable. An address change clears old coordinates.
* `load_places` downloads [GeoNames US postal data](https://download.geonames.org/export/zip/)
  once and builds a local city/state lookup. City coordinates are averages of the
  available postal-place coordinates. GeoNames attribution: CC BY 4.0.
* `geocode_stations` processes up to 10,000 unresolved station addresses per run
  in batches of 100, with a progress message before and after each request to the free [US Census Geocoder](https://geocoding.geo.census.gov/geocoder/).
  Only exact address matches are retained. Results and completed-attempt checkpoints
  are stored in SQLite after each batch.
* Unmatched addresses are written to `data/unresolved-stations.csv`. Highway exits
  and intersections often need manual verification. Unlocated stations are excluded
  from planning and their count is included in API responses. National coverage is
  therefore not guaranteed after the automated geocoding step.

**Do not replace unknown station locations with city centers.** That could produce
fuel stops miles away from the station. Provide verified coordinates for unresolved
stations using a sidecar CSV:

```csv
opis_id,latitude,longitude
7,36.5682,-95.2210
```

The row above illustrates the format; verify coordinates before using them.

```bash
python manage.py import_stations fuel-prices-for-be-assessment.csv --coordinates data/coordinates.csv
```

### If station geocoding appears stuck

The original command submitted all stations in one silent request with a 180-second
socket timeout. The command now reports progress and uses smaller batches. If an
older command is still running, stop it with **Ctrl+C**, then run:

```bash
python manage.py migrate
python manage.py geocode_stations --batch-size 50 --timeout 30
```

`--timeout` sets the socket timeout, not a total wall-clock deadline. Each batch
prints a message before waiting for the network and prints its match count after
saving. Interrupted or failed runs keep completed batches; rerunning resumes with
unattempted stations. Unmatched addresses from completed batches are skipped unless
you explicitly pass `--retry-unmatched`. `--limit 50` can be used for a short trial.

A timeout or connection error identifies the failed batch and writes the unresolved
report. If zero stations match, the command warns that the API remains unready and
points to the verified-coordinate import option. Census requires numbered street
addresses; almost all supplied addresses are highway or exit descriptions, so
Census alone is unlikely to provide adequate nationwide station coverage.

A previously downloaded GeoNames archive can be loaded offline:

```bash
python manage.py load_places --zip data/US.zip
```

Inspect preparation coverage:

```bash
python manage.py shell -c "from planner.models import Station; print('Located:', Station.objects.filter(latitude__isnull=False).count(), 'Total:', Station.objects.count())"
```

The API returns a clear `503` until US places and at least one located station
are available. Loading prices alone does not create station coordinates.

## API

### `GET /api/places/?state=TX`

Returns alphabetically sorted city choices from the loaded local place data:

```json
{"cities": [{"value": "austin, TX", "label": "Austin, TX"}, {"value": "dallas, TX", "label": "Dallas, TX"}]}
```

This endpoint powers the city dropdowns and makes no external API calls. Unknown
state codes return `400`; a state with no loaded cities returns an empty list.

### `POST /api/routes/`

```bash
curl -X POST http://127.0.0.1:8000/api/routes/ \
  -H 'Content-Type: application/json' \
  -d '{"start":"Dallas, TX","finish":"Los Angeles, CA"}'
```

Inputs:

| Field | Required | Meaning |
| --- | --- | --- |
| `start` | Yes | US city and two-letter state, e.g. `Dallas, TX` |
| `finish` | Yes | US city and two-letter state |
| `initial_gallons` | No | Fuel already in the tank, from 0 to 50; defaults to 50 |
| `initial_fuel_price` | No | USD per gallon used to value consumed initial fuel |

City matching ignores case and surrounding whitespace. Unknown city/state pairs
and Canadian locations are rejected. This version accepts city/state locations,
not arbitrary street addresses or raw coordinates. The driving route itself may
cross an international border; only the endpoints and selected stations must be US locations.

The response includes:

* `route`: final road distance in miles, duration in seconds, and GeoJSON geometry
  in `[longitude, latitude]` order.
* `fuel_stops`: station names, addresses, coordinates, OPIS IDs, road mile markers,
  prices, gallons purchased, and purchase costs.
* `fuel`: consumed gallons, arrival fuel, initial fuel valuation, purchases en
  route, and `total_cost_usd`.
* `map_url`: relative URL to an interactive Leaflet map, valid for 24 hours while
  cached. The map plots the driving route and fuel stops with purchase details.
* `coverage` (including `station_source`), `assumptions`, and `attribution`.

Money and gallon quantities are decimal strings. Prices retain CSV precision;
costs are calculated before rounding to cents. Rounded individual purchases can
differ slightly from the independently rounded total when added together.

### Fuel accounting

The default initial tank is full. This makes departure possible even when no
station is directly at the starting point. The response distinguishes:

```text
total_cost_usd = initial_fuel_consumed_cost_usd + money_spent_en_route_usd
```

Only the **consumed portion** of the initial tank is charged to the trip. Its
reference price is the nearest located station's CSV price unless the caller
supplies `initial_fuel_price`. This is a valuation assumption, not a claim that
the vehicle purchased fuel there. The reference station ID is returned.

For example, a 200-mile trip consumes 20 gallons. At a $3 initial reference price,
total fuel cost is $60, and purchases en route are $0. Unused initial fuel is
not charged. An empty initial tank needs a reachable station at the origin.

### Errors

| Status | Meaning |
| --- | --- |
| `400` | Invalid JSON, input type, location, or fuel value |
| `405` | Unsupported method |
| `422` | No driving route or no verified fuel plan within range |
| `503` | Missing preparation data, upstream failure, or busy routing provider |

Locations must have a continuous mapped driving connection. For example, Adak,
Alaska is on an island and cannot be reached by road from Arkansas. A location
being in the USA does not guarantee a road connection to every other US location.
The dropdowns still include Alaska and Hawaii to permit routes on their local
road networks.

OSRM `NoRoute` and `NoSegment` responses return `422`, including when OSRM sends
its documented HTTP 400 error response. `NoSegment` means a coordinate could not
snap to a driving road within the configured 200-meter radius; city coordinates
are approximate. These errors are distinct from provider outages, rate limits,
and connection failures, which remain `503`. Connection failure details are logged
on the server for diagnosis.

Errors use `{"error":{"code":"...","message":"..."}}`.
A map URL that has expired returns `404`.

## Routing, performance, and API budget

Routing uses the free [FOSSGIS OSRM service](https://routing.openstreetmap.de/about.html)
with OpenStreetMap data. Its [API](https://project-osrm.org/docs/v5.24.0/api/)
returns full GeoJSON and road distances.

For a cold request:

1. **One route call** gets the initial driving route between city centers.
2. Local spatial-grid matching finds located CSV stations within one mile of that
   geometry. Cumulative geometry length estimates their initial route positions.
3. The fuel optimizer chooses purchases locally.
4. If purchases are needed, **one additional route call** visits the chosen stations.
   Its actual road-leg distances replace the estimates. The fuel plan is recalculated,
   and any leg the vehicle cannot reach is rejected.

Thus a successful request uses **one or two routing calls**, never one per fuel
station. There are no online endpoint or station geocoding calls during a request.
The second route includes station detours; a station purchase is never accepted
solely on the basis of straight-line distance.

OSRM routes are cached for 24 hours by coordinates and provider URL. Repeating the
same request makes **zero routing calls** while those entries remain cached. Station CSV changes invalidate the in-memory snapshot, so updated CSV prices are
used even when road geometry remains cached. Database backup prices are read afresh
when the fallback is active. After database imports, regenerate an existing enriched
CSV with `export_stations` to publish those changes to the primary source.
The database cache shares routes, in-flight locks, and a conservative rate gate
across local workers. The public service allows at most one request per second;
this app spaces requests by two seconds. Concurrent duplicate requests receive
`503` with `Retry-After` rather than duplicating upstream work.

Map tiles and Leaflet assets load in the browser separately from the route API.
They do not increase the backend routing-call count. Attribution is displayed;
follow the [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/).
The public routing service has no availability guarantee and prohibits heavy use.
For sustained traffic, point `OSRM_URL` at your own OSRM instance and configure its
rate interval appropriately.

No latency claim has been made from mocked tests: actual response time depends on
the provider, cache warmth, route length, and station coverage.

## Optimization scope

For a fixed route with unlimited fuel availability and no stop fees, the optimizer
buys enough to reach the first cheaper station within range. If none exists, it
fills the tank or buys just enough to finish. The destination acts as a zero-price
terminal point, preventing unnecessary final purchases. Decimal arithmetic keeps
fuel and price calculations precise.

This minimizes purchased-fuel cost along the supplied station sequence, given the
initial fuel. It **does not guarantee a global minimum across every possible
highway route, detour, or station outside the one-mile corridor**. The final road
verification can change distances and costs; the second optimization is restricted
to the selected station sequence. If that route is infeasible, the API returns `422`
without spending additional API calls searching alternatives. Other feasible plans
may exist. It also does not minimize the number of stops or account for traffic,
vehicle-specific road restrictions, grades, idle fuel, reserve fuel, or opening hours.

Census matches are interpolated address-range coordinates, not surveyed forecourt
entrances. OSRM snapping is limited to 200 meters. Fuel prices are a historical
snapshot from the assignment and are not live prices. The assignment does not
identify fuel type; all CSV prices are treated as applicable USD per US gallon.

## Demonstrate with curl

With the Django server running, send this request from another terminal:

```bash
curl -i --max-time 40 -X POST http://127.0.0.1:8000/api/routes/ \
  -H 'Content-Type: application/json' \
  --data '{"start":"agoura hills, CA","finish":"acton, CA"}'
```

During preparation, the environment could not connect to the user's localhost and
could not bind a TCP port or Unix socket. Consequently, no successful curl execution
is claimed. A Django in-process HTTP check used the actual CSV and an existing
cached OSRM road route without mocked route data. It returned 200, 57.282 miles,
5.728166 gallons consumed, and $18.67 total fuel cost. This short trip needs no
fuel purchases because the initial tank is full. Map retrieval returned 200;
invalid location/fuel returned 400 and the unsupported route method returned 405.
These results do not establish live OSRM availability. The current 22 located
stations are insufficient to guarantee long-distance fuel coverage.

### Quick code walkthrough

* `planner/views.py` exposes city choices, the route endpoint, and cached map pages.
* `planner/services.py` resolves city coordinates, finds route candidates, verifies
  actual road legs through fuel stops, and assembles distances and fuel costs.
* `planner/optimizer.py` chooses cost-effective purchases using a 50-gallon tank
  and 10 mpg; it buys to the next cheaper reachable station or fills as needed.
* `planner/station_data.py` caches CSV stations in memory, detects file changes,
  and uses the database backup when the primary station data is unusable.
* `planner/providers.py` limits routing to one or two OSRM calls on a cold request,
  caches geometry, and distinguishes unreachable routes from service failures.

## Tests and TDD

```bash
source .venv/bin/activate
python manage.py test --verbosity 2
python manage.py check
python manage.py makemigrations --check --dry-run
```

The standalone optimizer tests can run without Django:

```bash
.venv/bin/python -m unittest planner.tests.test_optimizer
```

The agreed test boundaries are the public fuel optimizer, HTTP API, and CSV import
command. Tests cover partial refueling, cheaper stations ahead, exact 500-mile legs,
unreachable gaps, physical input validation, import rollback, duplicate handling,
Canadian exclusions, GeoJSON/map delivery, road-detour verification, cache reuse,
updated prices, and upstream failures. CSV-first tests additionally verify no station
queries on the primary path, one file read across unchanged requests, source reloads,
coordinate-sidecar support, database fallback, and database-to-CSV round trips. An independent inventory-search oracle checks
100 seeded fuel-price scenarios against the optimizer. External HTTP is mocked at
`urllib.request.urlopen`; application services and optimization run normally.

All 42 tests pass on Django 6.1.2. Tests were developed through failing behavior
cases followed by implementation, including the HTTP 400 routing-error regression.

## Configuration and deployment

| Environment variable | Default |
| --- | --- |
| `DJANGO_SECRET_KEY` | Local development placeholder; replace for deployment |
| `DJANGO_DEBUG` | `0` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` |
| `OSRM_URL` | `https://routing.openstreetmap.de/routed-car` |
| `OSRM_MIN_INTERVAL` | `2` seconds; use `0` only with an endpoint you control |
| `STATIONS_CSV` | `data/stations.csv` (enriched primary snapshot) |
| `STATION_PRICES_CSV` | `fuel-prices-for-be-assessment.csv` (original prices) |
| `STATION_COORDINATES_CSV` | `data/station-coordinates.csv` (optional verified coordinates) |
| `PROVIDER_USER_AGENT` | `FuelStationRouteAssessment/1.0`; add your contact information |

The application is a stateless, unauthenticated exercise API; it performs no
account or payment operations. SQLite and its database cache are suitable for a
local assessment. For production, use a production WSGI server, HTTPS, deployment
settings, an application rate limit, a shared cache/database, and a routing service
sized for the workload. Changing the cache backend must preserve atomic `add`
semantics for the provider gate. Maps contain user-supplied trip locations and are
accessible to anyone holding their unguessable URLs until expiration.

## Layout

```text
config/                         Django settings, URLs, WSGI
planner/models.py               Station database backup and US city lookup
planner/station_data.py         Cached CSV station source and database fallback
planner/optimizer.py            Dependency-free fixed-route fuel optimizer
planner/geometry.py             Local station corridor matching
planner/providers.py            Cached, rate-limited OSRM adapter
planner/services.py             Route planning and fuel accounting
planner/views.py                JSON endpoint and map views
planner/management/commands/    CSV import and one-time data preparation
planner/tests/                  Optimizer, API, and import tests
```
