import csv
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from planner.models import Station
from planner.constants import US_STATES, CANADIAN_PROVINCES
from planner.station_data import coordinates as csv_coordinates


class Command(BaseCommand):
    help = 'Import the supplied fuel CSV, optionally with an opis_id,latitude,longitude sidecar.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--coordinates')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            coordinates = {}
            if options['coordinates']:
                with Path(options['coordinates']).open(newline='', encoding='utf-8-sig') as stream:
                    for row in csv.DictReader(stream):
                        lat, lon = float(row['latitude']), float(row['longitude'])
                        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                            raise ValueError('Invalid coordinates')
                        coordinates[int(row['opis_id'])] = (lat, lon)
            records = {}
            with Path(options['path']).open(newline='', encoding='utf-8-sig') as stream:
                for line, row in enumerate(csv.DictReader(stream), 2):
                    price = Decimal(row['Retail Price'])
                    opis_id = int(row['OPIS Truckstop ID'])
                    state = row['State'].strip().upper()
                    if state in CANADIAN_PROVINCES:
                        continue
                    if (not price.is_finite() or not 0 < price < 10000 or opis_id < 0
                            or state not in US_STATES or price.as_tuple().exponent < -8):
                        raise ValueError(f'Invalid station or price on line {line}')
                    if opis_id in records and records[opis_id]['price'] <= price:
                        continue
                    values = {'name': row['Truckstop Name'].strip(), 'address': row['Address'].strip(),
                              'city': row['City'].strip(), 'state': state, 'price': price}
                    if not all(values[field] for field in ('name', 'address', 'city')):
                        raise ValueError(f'Missing station details on line {line}')
                    if opis_id in coordinates:
                        values.update(latitude=coordinates[opis_id][0], longitude=coordinates[opis_id][1],
                                      coordinate_source='coordinate-sidecar')
                    else:
                        lat, lon = csv_coordinates(row)
                        if lat is not None:
                            values.update(latitude=lat, longitude=lon,
                                          coordinate_source=row.get('coordinate_source', '').strip() or 'enriched-csv')
                    records[opis_id] = values
            for opis_id, values in records.items():
                existing = Station.objects.filter(opis_id=opis_id).first()
                if existing and any(getattr(existing, key) != values[key] for key in ('address', 'city', 'state')):
                    if 'latitude' not in values:
                        values.update(latitude=None, longitude=None, coordinate_source='', geocoding_attempted_at=None)
                Station.objects.update_or_create(opis_id=opis_id, defaults=values)
        except (OSError, KeyError, ValueError, InvalidOperation, csv.Error) as exc:
            raise CommandError(f'Import failed: {exc}') from exc
        self.stdout.write(self.style.SUCCESS(f'Imported {len(records)} unique stations.'))
