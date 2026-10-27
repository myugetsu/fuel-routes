"""Local spatial grid keeps corridor matching independent of external API calls."""
import math
from collections import defaultdict

METERS_PER_MILE = 1609.344
CELL = 0.05


def haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 3958.7613 * 2 * math.asin(min(1, math.sqrt(h)))


def corridor_candidates(route, stations, width):
    points = route['geometry']['coordinates']
    segments = []
    grid = defaultdict(list)
    cumulative = 0.0
    for a, b in zip(points, points[1:]):
        length = haversine(a, b)
        segment = (a, b, cumulative, length)
        segments.append(segment)
        index = len(segments)-1
        # Only the local grid cells occupied by a segment are indexed.
        for x in range(math.floor(min(a[0], b[0])/CELL), math.floor(max(a[0], b[0])/CELL)+1):
            for y in range(math.floor(min(a[1], b[1])/CELL), math.floor(max(a[1], b[1])/CELL)+1):
                grid[x, y].append(index)
        cumulative += length
    if not cumulative:
        return []
    scale = route['distance'] / METERS_PER_MILE / cumulative
    candidates = []
    for station in stations:
        lon, lat = station.longitude, station.latitude
        cos_lat = max(0.1, math.cos(math.radians(lat)))
        radius_x = math.ceil(width / (69 * cos_lat) / CELL) + 1
        radius_y = math.ceil(width / 69 / CELL) + 1
        cx, cy = math.floor(lon/CELL), math.floor(lat/CELL)
        indexes = set()
        for x in range(cx-radius_x, cx+radius_x+1):
            for y in range(cy-radius_y, cy+radius_y+1):
                indexes.update(grid.get((x,y), ()))
        best = None
        for index in indexes:
            a, b, start, length = segments[index]
            ax, ay = (a[0]-lon)*69*cos_lat, (a[1]-lat)*69
            bx, by = (b[0]-lon)*69*cos_lat, (b[1]-lat)*69
            dx, dy = bx-ax, by-ay
            fraction = max(0, min(1, -(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0
            offset = math.hypot(ax+fraction*dx, ay+fraction*dy)
            if best is None or offset < best[0]:
                best = offset, (start + length*fraction)*scale
        if best and best[0] <= width:
            candidates.append((station, best[1], best[0]))
    return candidates
