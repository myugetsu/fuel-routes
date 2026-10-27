"""CSV station snapshots cached in process memory, with a database fallback."""
import csv
import logging
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from django.db import DatabaseError
from planner.constants import CANADIAN_PROVINCES, US_STATES

logger = logging.getLogger(__name__)


class StationDataUnavailable(Exception):
    pass


@dataclass(frozen=True)
class StationRecord:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: Decimal
    latitude: float | None
    longitude: float | None
    coordinate_source: str


@dataclass(frozen=True)
class StationCatalog:
    stations: tuple[StationRecord, ...]
    unlocated_count: int
    source: str


def fingerprint(path):
    try:
        info = path.stat()
        return info.st_mtime_ns, info.st_size, info.st_ino
    except FileNotFoundError:
        return None


def coordinates(row):
    lat, lon = row.get('latitude', '').strip(), row.get('longitude', '').strip()
    if not lat and not lon:
        return None, None
    latitude, longitude = float(lat), float(lon)
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('Invalid station coordinates')
    return latitude, longitude


@lru_cache(maxsize=4)
def csv_snapshot(path, signature, coordinate_path, coordinate_signature):
    """File signatures invalidate the immutable snapshot when either file changes."""
    sidecar = {}
    if coordinate_signature is not None:
        with Path(coordinate_path).open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            if not {'opis_id', 'latitude', 'longitude'} <= set(reader.fieldnames or ()):
                raise ValueError('Coordinate CSV requires opis_id, latitude, longitude')
            for row in reader:
                point = coordinates(row)
                if point[0] is None:
                    raise ValueError('Coordinate sidecar has an empty position')
                sidecar[int(row['opis_id'])] = point
    records = {}
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        required = {'OPIS Truckstop ID', 'Truckstop Name', 'Address', 'City', 'State', 'Retail Price'}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError('Station CSV is missing required columns')
        for row in reader:
            state = row['State'].strip().upper()
            if state in CANADIAN_PROVINCES:
                continue
            station_id, price = int(row['OPIS Truckstop ID']), Decimal(row['Retail Price'])
            if state not in US_STATES or station_id < 0 or not price.is_finite() or not 0 < price < 10000:
                raise ValueError('Station CSV contains an invalid station or price')
            name, address, city = (row[key].strip() for key in ('Truckstop Name', 'Address', 'City'))
            if not all((name, address, city)):
                raise ValueError('Station CSV contains missing station details')
            lat, lon = sidecar.get(station_id, (None, None))
            source = 'coordinate-sidecar' if lat is not None else row.get('coordinate_source', '').strip()
            if lat is None:
                lat, lon = coordinates(row)
                if lat is not None and not source:
                    source = 'enriched-csv'
            record = StationRecord(station_id, name, address, city, state, price, lat, lon, source)
            previous = records.get(station_id)
            if previous is None or price < previous.price:
                records[station_id] = record
    if not records:
        raise ValueError('Station CSV contains no US stations')
    located = tuple(record for record in records.values() if record.latitude is not None)
    return StationCatalog(located, len(records)-len(located), 'csv')


def load_stations():
    """Read CSV first; query station tables only when CSV cannot supply locations."""
    csv_catalog = None
    try:
        enriched = Path(settings.STATIONS_CSV)
        path = enriched if fingerprint(enriched) is not None else Path(settings.STATION_PRICES_CSV)
        sidecar = Path(settings.STATION_COORDINATES_CSV)
        csv_catalog = csv_snapshot(str(path), fingerprint(path), str(sidecar), fingerprint(sidecar))
        if csv_catalog.stations:
            return csv_catalog
        reason = 'CSV contains no located stations'
    except (OSError, ValueError, InvalidOperation, KeyError, TypeError, AttributeError, csv.Error) as exc:
        reason = str(exc)
    # DB prices and coordinates remain available if the primary file is missing,
    # malformed, or has not yet been enriched. Do not merge mismatched snapshots.
    from planner.models import Station
    try:
        records = list(Station.objects.all())
    except DatabaseError as exc:
        raise StationDataUnavailable('Station CSV is unavailable and the database backup cannot be read.') from exc
    located = tuple(StationRecord(s.opis_id, s.name, s.address, s.city, s.state, s.price,
                                  s.latitude, s.longitude, s.coordinate_source)
                    for s in records if s.latitude is not None and s.longitude is not None)
    if located:
        logger.info('Using station database backup: %s', reason)
        return StationCatalog(located, len(records)-len(located), 'database')
    return csv_catalog or StationCatalog((), len(records), 'database')
