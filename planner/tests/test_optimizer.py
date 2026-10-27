"""Public optimizer contract; expected amounts are hand-worked examples."""
from decimal import Decimal
import unittest

from planner.optimizer import FuelPoint, NoFeasiblePlan, optimize


class OptimizerTests(unittest.TestCase):
    def test_short_trip_uses_initial_fuel_without_buying_more(self):
        plan = optimize(200, [], initial_gallons=50)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.gallons_used, Decimal('20'))
        self.assertEqual(plan.arrival_gallons, Decimal('30'))

    def test_buy_only_enough_to_reach_cheaper_fuel_then_finish(self):
        # 1,000 mi: 50 gal initially; reach mile 400 with 10 gal;
        # buy 10 at $5 to reach mile 600, then buy 40 at $3. Total $170.
        plan = optimize(1000, [FuelPoint(1, 400, Decimal('5')),
                               FuelPoint(2, 600, Decimal('3'))])
        self.assertEqual([(s.station_id, s.gallons) for s in plan.stops],
                         [(1, Decimal('10')), (2, Decimal('40'))])
        self.assertEqual(plan.cost, Decimal('170'))
        self.assertEqual(plan.arrival_gallons, 0)

    def test_gap_over_500_miles_is_infeasible(self):
        with self.assertRaises(NoFeasiblePlan):
            optimize(1001, [FuelPoint(1, 500, Decimal('3'))])

    def test_exact_500_mile_legs_are_feasible(self):
        plan = optimize(1500, [FuelPoint(1, 500, Decimal('4')),
                               FuelPoint(2, 1000, Decimal('3'))])
        self.assertEqual(plan.cost, Decimal('350'))
        self.assertEqual(plan.arrival_gallons, 0)

    def test_empty_initial_tank_can_buy_at_origin(self):
        plan = optimize(100, [FuelPoint(1, 0, Decimal('3.259'))], initial_gallons=0)
        self.assertEqual(plan.cost, Decimal('32.590'))

    def test_rejects_invalid_physical_inputs(self):
        for distance, fuel in [(-1, 50), (100, 51), (100, -1), (float('nan'), 50)]:
            with self.subTest(distance=distance, fuel=fuel), self.assertRaises(ValueError):
                optimize(distance, [], initial_gallons=fuel)

    def test_greedy_matches_independent_inventory_search(self):
        """A small exhaustive inventory oracle verifies 100 seeded price layouts."""
        import random
        generator = random.Random(20261027)
        for _ in range(100):
            miles = sorted(generator.sample(range(100, 1000, 100), 5))
            points = [FuelPoint(i, mile, Decimal(generator.randint(2, 7)))
                      for i, mile in enumerate(miles)]
            # Fuel is integral here: every route segment consumes multiples of 10 gal.
            costs = {50: Decimal(0)}
            previous = 0
            for point in points + [FuelPoint(999, 1000, Decimal(0))]:
                burn = (point.mile-previous)//10
                arrived = {fuel-burn: cost for fuel, cost in costs.items() if fuel >= burn}
                if point.station_id == 999:
                    costs = arrived
                    break
                costs = {}
                for fuel, cost in arrived.items():
                    for purchase in range(51-fuel):
                        level = fuel+purchase
                        candidate = cost + purchase*point.price
                        costs[level] = min(costs.get(level, Decimal('Infinity')), candidate)
                previous = point.mile
            if costs:
                self.assertEqual(optimize(1000, points).cost, min(costs.values()))
            else:
                with self.assertRaises(NoFeasiblePlan):
                    optimize(1000, points)
