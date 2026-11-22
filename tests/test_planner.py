import unittest
from pathlib import Path
from navigation import Arena, Planner

ARENA = Path(__file__).resolve().parent.parent / 'arena'
STATS = dict(radius=2.5, height=9, step_up=1.2)


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.arena = Arena(ARENA)

    def run_plan(self, start, goal):
        p = Planner(self.arena, STATS)
        p.start(start, goal)
        while p.state == 'search':
            p.advance(5000)
        return p

    def test_escapes_courtyard_where_local_steering_stalls(self):
        # Inside a building courtyard east of the stadium; the zone lies to the south-west.
        p = self.run_plan((417, 31), (43, -73))
        self.assertEqual(p.state, 'done')
        self.assertGreater(len(p.path), 10)

    def test_waypoint_is_near_and_ahead(self):
        p = self.run_plan((417, 31), (43, -73))
        wp = p.waypoint([417, 31, self.arena.height(417, 31)], STATS)
        self.assertIsNotNone(wp)
        self.assertLessEqual(((wp[0]-417)**2+(wp[1]-31)**2)**.5, 31)

    def test_time_budget_is_respected(self):
        import time
        best = float('inf')
        for _ in range(3):  # best of three: a busy machine must not fail the test
            p = Planner(self.arena, STATS)
            p.start((417, 31), (43, -73))
            t = time.perf_counter()
            p.advance(10**6, max_ms=5)
            best = min(best, (time.perf_counter()-t)*1000)
        self.assertLess(best, 20)


if __name__ == '__main__':
    unittest.main()
