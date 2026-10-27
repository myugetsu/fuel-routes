import csv
import io
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from planner.models import Place
from planner.providers import ProviderUnavailable, download
from planner.constants import US_STATES


class Command(BaseCommand):
    help = 'Load US city/state locations from GeoNames US.zip (download once or supply --zip).'

    def add_arguments(self, parser):
        parser.add_argument('--zip', dest='zip_path')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            blob = (Path(options['zip_path']).read_bytes() if options['zip_path'] else
                    download('https://download.geonames.org/export/zip/US.zip', timeout=60))
            cities = {}
            with ZipFile(io.BytesIO(blob)) as archive:
                with io.TextIOWrapper(archive.open('US.txt'), encoding='utf-8') as stream:
                    for row in csv.reader(stream, delimiter='\t'):
                        if row[0] != 'US' or row[4] not in US_STATES:
                            continue
                        key = f'{row[2].strip().casefold()}, {row[4]}'
                        lat, lon = float(row[9]), float(row[10])
                        total_lat, total_lon, count = cities.get(key, (0, 0, 0))
                        cities[key] = (total_lat + lat, total_lon + lon, count + 1)
            for key, (lat, lon, count) in cities.items():
                Place.objects.update_or_create(key=key, defaults={'latitude': lat/count, 'longitude': lon/count})
        except (OSError, ValueError, IndexError, KeyError, BadZipFile, ProviderUnavailable) as exc:
            raise CommandError(f'Could not load places: {exc}') from exc
        self.stdout.write(f'Loaded {len(cities)} US cities. GeoNames CC BY 4.0.')
