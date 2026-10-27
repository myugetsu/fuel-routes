"""Prepare station coordinates in visible, checkpointed Census batches."""
import csv
import io
import math
import uuid
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from planner.models import Station
from planner.providers import ProviderUnavailable, download


class Command(BaseCommand):
    help = 'Geocode stations in resumable Census batches; report addresses requiring verified coordinates.'

    def add_arguments(self, parser):
        parser.add_argument('--unresolved', default='data/unresolved-stations.csv')
        parser.add_argument('--limit', type=int, default=10000, help='Maximum stations to process in this run.')
        parser.add_argument('--batch-size', type=int, default=100, help='Stations per Census request (default: 100).')
        parser.add_argument('--timeout', type=int, default=30, help='HTTP socket timeout in seconds (default: 30).')
        parser.add_argument('--retry-unmatched', action='store_true', help='Also retry previously processed unmatched addresses.')

    def progress(self, message):
        self.stdout.write(message)
        self.stdout.flush()

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 10000:
            raise CommandError('--limit must be between 1 and 10000')
        if not 1 <= options['batch_size'] <= 10000:
            raise CommandError('--batch-size must be between 1 and 10000')
        if not 1 <= options['timeout'] <= 600:
            raise CommandError('--timeout must be between 1 and 600 seconds')
        unresolved = Station.objects.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True))
        pending = unresolved
        if not options['retry_unmatched']:
            pending = pending.filter(geocoding_attempted_at__isnull=True)
        stations = list(pending.order_by('opis_id')[:options['limit']])
        output = Path(options['unresolved'])
        if not stations:
            self.write_report(output)
            if unresolved.exists():
                self.progress(f'No unattempted stations. {unresolved.count()} remain unresolved; see {output}. '
                              'Use verified --coordinates with import_stations, or --retry-unmatched to retry Census.')
            else:
                self.progress('No unresolved stations.')
            return
        batches = math.ceil(len(stations) / options['batch_size'])
        self.progress(f'Processing {len(stations)} stations in {batches} batch(es); '
                      f'socket timeout {options["timeout"]}s per request. '
                      'Completed batches are saved; rerun to resume after an interruption.')
        matched = 0
        number = 0
        try:
            for start in range(0, len(stations), options['batch_size']):
                number += 1
                batch = stations[start:start + options['batch_size']]
                self.progress(f'Batch {number}/{batches}: sending {len(batch)} station(s) to Census; waiting for response…')
                raw = self.request_batch(batch, options['timeout'])
                updates = self.parse_response(raw, batch)
                now = timezone.now()
                for station in batch:
                    station.geocoding_attempted_at = now
                    if station.opis_id in updates:
                        station.longitude, station.latitude = updates[station.opis_id]
                        station.coordinate_source = 'US Census exact address-range match'
                with transaction.atomic():
                    Station.objects.bulk_update(batch, ['longitude', 'latitude', 'coordinate_source', 'geocoding_attempted_at'])
                matched += len(updates)
                self.progress(f'Batch {number}/{batches} complete: {len(updates)} matched, '
                              f'{len(batch)-len(updates)} unresolved; saved {min(start+len(batch), len(stations))}/{len(stations)}.')
        except (OSError, ValueError, csv.Error, ProviderUnavailable, KeyboardInterrupt) as exc:
            self.write_report(output)
            reason = 'Interrupted' if isinstance(exc, KeyboardInterrupt) else str(exc)
            if exc.__cause__:
                reason += f' ({exc.__cause__})'
            raise CommandError(f'Geocoding stopped at batch {number}/{batches}: {reason}. '
                               f'Completed batches were saved. Rerun to resume. Unresolved report: {output}') from exc
        self.write_report(output)
        self.progress(f'Matched {matched}/{len(stations)}; unresolved report: {output}')
        if not Station.objects.filter(latitude__isnull=False, longitude__isnull=False).exists():
            self.progress(self.style.WARNING(
                'No stations were located. Census requires numbered street addresses; highway exits may not match. '
                'The API still needs verified coordinates. Import them using import_stations --coordinates.'))

    def request_batch(self, stations, timeout):
        stream = io.StringIO()
        writer = csv.writer(stream)
        for station in stations:
            writer.writerow([station.opis_id, station.address, station.city, station.state, ''])
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="benchmark"\r\n\r\nPublic_AR_Current\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="addressFile"; filename="stations.csv"\r\n'
                f'Content-Type: text/csv\r\n\r\n{stream.getvalue()}\r\n--{boundary}--\r\n').encode()
        return download('https://geocoding.geo.census.gov/geocoder/locations/addressbatch',
                        data=body, headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
                        timeout=timeout).decode('utf-8-sig')

    def parse_response(self, raw, stations):
        expected = {station.opis_id for station in stations}
        seen, matches = set(), {}
        try:
            for row in csv.reader(io.StringIO(raw), strict=True):
                if not row:
                    continue
                station_id = int(row[0])
                if (len(row) < 3 or station_id not in expected or station_id in seen
                        or row[2] not in {'Match', 'No_Match', 'Tie'}):
                    raise ValueError('Unexpected record')
                seen.add(station_id)
                if row[2] == 'Match':
                    if len(row) < 6 or row[3] not in {'Exact', 'Non_Exact'}:
                        raise ValueError('Invalid match record')
                    lon, lat = map(float, row[5].split(','))
                    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        raise ValueError('Invalid coordinates')
                    if row[3] == 'Exact':
                        matches[station_id] = (lon, lat)
            if seen != expected:
                raise ValueError('Missing records')
        except (ValueError, IndexError, csv.Error) as exc:
            raise ValueError('Invalid Census response; batch was not saved') from exc
        return matches

    def write_report(self, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('w', newline='') as target:
            writer = csv.writer(target)
            writer.writerow(['opis_id', 'name', 'address', 'city', 'state'])
            writer.writerows(Station.objects.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True)).values_list(
                'opis_id', 'name', 'address', 'city', 'state'))
