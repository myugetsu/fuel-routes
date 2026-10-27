"""Export the persisted database backup into the primary in-memory CSV source."""
import csv
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from planner.models import Station


class Command(BaseCommand):
    help = 'Atomically export database stations, prices, and coordinates to the primary enriched CSV.'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Output CSV; defaults to STATIONS_CSV (data/stations.csv).')

    def handle(self, *args, **options):
        output = Path(options['output'] or settings.STATIONS_CSV)
        temporary = None
        try:
            stations = list(Station.objects.order_by('opis_id'))
            if not stations:
                raise CommandError('Database contains no stations; refusing to replace the primary CSV.')
            output.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode='w', newline='', encoding='utf-8', dir=output.parent,
                                    prefix=f'.{output.name}.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                writer = csv.writer(stream)
                writer.writerow(['OPIS Truckstop ID', 'Truckstop Name', 'Address', 'City', 'State',
                                 'Rack ID', 'Retail Price', 'latitude', 'longitude', 'coordinate_source'])
                for station in stations:
                    writer.writerow([station.opis_id, station.name, station.address, station.city, station.state,
                                     '', station.price, station.latitude, station.longitude, station.coordinate_source])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        except (OSError, DatabaseError) as exc:
            raise CommandError(f'Could not export stations: {exc}') from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        located = sum(s.latitude is not None and s.longitude is not None for s in stations)
        self.stdout.write(f'Exported {len(stations)} stations ({located} located) to {output}. Database backup retained.')
        if not located:
            self.stdout.write(self.style.WARNING(
                'No coordinates are available yet. Export does not geocode addresses; supply verified coordinates before routing.'))
