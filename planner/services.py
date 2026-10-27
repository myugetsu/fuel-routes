from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from planner.geometry import METERS_PER_MILE, corridor_candidates, haversine
from planner.models import Place
from planner.station_data import StationDataUnavailable, load_stations
from planner.optimizer import FuelPoint, NoFeasiblePlan, optimize
from planner.providers import driving_route


class InvalidInput(ValueError):
    pass


class DataUnavailable(Exception):
    pass


def decimal_value(value, name, minimum, maximum):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or not minimum <= result <= maximum:
            raise ValueError
        return result
    except (ValueError, ArithmeticError) as exc:
        raise InvalidInput(f'{name} must be a finite number between {minimum} and {maximum}.') from exc


def resolve_place(value):
    if not isinstance(value, str) or len(value) > 150 or ',' not in value:
        raise InvalidInput('Locations must use US city and state, for example Dallas, TX.')
    city, state = value.rsplit(',', 1)
    key = f'{city.strip().casefold()}, {state.strip().upper()}'
    if not Place.objects.exists():
        raise DataUnavailable('Load US places first: python manage.py load_places.')
    try:
        return Place.objects.get(key=key)
    except Place.DoesNotExist as exc:
        raise InvalidInput(f'Unknown US city/state: {value}.') from exc


def money(value):
    return str(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def gallons(value):
    return str(value.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP))


def build_plan(payload):
    if not isinstance(payload, dict):
        raise InvalidInput('Expected a JSON object.')
    initial = decimal_value(payload.get('initial_gallons', 50), 'initial_gallons', 0, 50)
    initial_price = None
    if 'initial_fuel_price' in payload:
        initial_price = decimal_value(payload['initial_fuel_price'], 'initial_fuel_price', Decimal('.00000001'), 9999)
    start, finish = resolve_place(payload.get('start')), resolve_place(payload.get('finish'))
    if start.key == finish.key:
        raise InvalidInput('Start and finish must be different US cities.')
    try:
        catalog = load_stations()
    except StationDataUnavailable as exc:
        raise DataUnavailable(str(exc)) from exc
    stations = catalog.stations
    if not stations:
        raise DataUnavailable('No located stations. Import prices and geocode stations first.')
    origin = (start.longitude, start.latitude)
    destination = (finish.longitude, finish.latitude)
    reference = min(stations, key=lambda s: haversine(origin, (s.longitude, s.latitude)))
    if initial_price is None:
        initial_price = reference.price
    base = driving_route([origin, destination])
    candidates = corridor_candidates(base, stations, settings.CORRIDOR_MILES)
    base_plan = optimize(base['distance']/METERS_PER_MILE,
                         [FuelPoint(s.opis_id, mile, s.price) for s, mile, offset in candidates],
                         initial_gallons=initial)
    lookup = {s.opis_id: s for s in stations}
    selected = [lookup[stop.station_id] for stop in base_plan.stops]
    if len(selected) + 2 > settings.MAX_ROUTE_WAYPOINTS:
        raise NoFeasiblePlan('Too many fuel stops for the configured routing provider.')
    route = base
    plan = base_plan
    if selected:
        route = driving_route([origin] + [(s.longitude, s.latitude) for s in selected] + [destination])
        verified = []
        mile = 0.0
        for station, leg in zip(selected, route['legs']):
            mile += leg['distance']/METERS_PER_MILE
            verified.append(FuelPoint(station.opis_id, mile, station.price))
        plan = optimize(route['distance']/METERS_PER_MILE, verified, initial_gallons=initial)
    used_initial = min(initial, plan.gallons_used)
    initial_cost = used_initial * initial_price
    purchases = []
    for stop in plan.stops:
        station = lookup[stop.station_id]
        purchases.append({'opis_id': station.opis_id, 'name': station.name, 'address': station.address,
                          'city': station.city, 'state': station.state, 'latitude': station.latitude,
                          'longitude': station.longitude, 'coordinate_source': station.coordinate_source,
                          'mile': round(float(stop.mile), 3), 'price_per_gallon': str(stop.price),
                          'gallons': gallons(stop.gallons), 'cost_usd': money(stop.cost)})
    return {'route': {'distance_miles': round(route['distance']/METERS_PER_MILE, 3),
                       'duration_seconds': round(route['duration']), 'geometry': route['geometry']},
            'fuel_stops': purchases,
            'fuel': {'mpg': 10, 'tank_capacity_gallons': 50, 'maximum_range_miles': 500,
                     'initial_gallons': gallons(initial), 'gallons_consumed': gallons(plan.gallons_used),
                     'arrival_gallons': gallons(plan.arrival_gallons),
                     'initial_fuel_price_per_gallon': str(initial_price),
                     'initial_fuel_reference_opis_id': reference.opis_id if 'initial_fuel_price' not in payload else None,
                     'initial_fuel_consumed_cost_usd': money(initial_cost),
                     'money_spent_en_route_usd': money(plan.cost),
                     'total_cost_usd': money(initial_cost + plan.cost), 'currency': 'USD'},
            'coverage': {'located_stations': len(stations),
                         'unlocated_stations': catalog.unlocated_count,
                         'station_source': catalog.source,
                         'corridor_candidates': len(candidates)},
            'assumptions': [
                'City/state inputs resolve to GeoNames city centroids.',
                'Initial tank defaults to full; consumed initial fuel is included in total cost.',
                'If initial_fuel_price is omitted, the nearest located CSV station supplies its reference price.',
                'Minimum fuel cost on the fixed candidate route; global route choice and stop count are not optimized.',
                'Candidates lie within one mile of the initial route; final distance includes driving to selected stations.',
                'Station address-range geocodes and OSRM snapping may differ from actual forecourt entrances.',
                'Unlocated stations are excluded; CSV prices are a historical snapshot.',
            ],
            'attribution': '© OpenStreetMap contributors; routing by OSRM/FOSSGIS; cities: GeoNames CC BY 4.0.'}
