"""Minimum-cost refueling on an ordered, fixed route (miles, US gallons, USD)."""
from dataclasses import dataclass
from decimal import Decimal


class NoFeasiblePlan(ValueError):
    pass


@dataclass(frozen=True)
class FuelPoint:
    station_id: int
    mile: float
    price: Decimal


@dataclass(frozen=True)
class Purchase:
    station_id: int
    mile: Decimal
    gallons: Decimal
    price: Decimal

    @property
    def cost(self):
        return self.gallons * self.price


@dataclass(frozen=True)
class Plan:
    stops: list[Purchase]
    gallons_used: Decimal
    arrival_gallons: Decimal

    @property
    def cost(self):
        return sum((stop.cost for stop in self.stops), Decimal(0))


def optimize(distance, points, *, initial_gallons=50):
    """Buy to the first cheaper reachable station, otherwise fill to capacity.

    The destination has zero price, preventing unnecessary final fuel purchases.
    Existing fuel is consumed first; the initial tank is a fixed input.
    """
    distance = Decimal(str(distance))
    fuel = Decimal(str(initial_gallons))
    if not distance.is_finite() or distance < 0 or not fuel.is_finite() or not 0 <= fuel <= 50:
        raise ValueError("Distance must be nonnegative and initial gallons between 0 and 50.")
    for point in points:
        mile, price = Decimal(str(point.mile)), Decimal(str(point.price))
        if (point.station_id < 0 or not mile.is_finite() or not 0 <= mile <= distance
                or not price.is_finite() or price <= 0):
            raise ValueError("Invalid station position or price.")
    nodes = sorted(points, key=lambda p: (p.mile, p.price, p.station_id))
    nodes.append(FuelPoint(-1, distance, Decimal(0)))
    position = Decimal(0)
    purchases = []
    for index, node in enumerate(nodes):
        mile = Decimal(str(node.mile))
        fuel -= (mile - position) / 10
        if fuel < Decimal('-0.000000001'):
            raise NoFeasiblePlan(f"No fuel available before route mile {mile}.")
        fuel = max(fuel, Decimal(0))
        position = mile
        if node.station_id == -1:
            break
        target = min(Decimal(50), (distance - mile) / 10)
        for following in nodes[index + 1:]:
            gap = Decimal(str(following.mile)) - mile
            if gap > 500:
                break
            if following.price < node.price:
                target = gap / 10
                break
        buy = max(Decimal(0), target - fuel)
        if buy:
            purchases.append(Purchase(node.station_id, mile, buy, node.price))
            fuel += buy
    return Plan(purchases, distance / 10, fuel)
