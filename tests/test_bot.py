import copy
import gzip
import json
import math
from pathlib import Path
import unittest

from bot import Bot, intercept
from navigation import Arena, inside, steer


ROOT = Path(__file__).resolve().parents[1]
with gzip.open(ROOT / 'check/stream.jsonl.gz', 'rt', encoding='utf-8') as stream:
    RECORDING = [json.loads(line) for line in stream]
HELLO = next(m for m in RECORDING if m['type'] == 'hello')
HELLO['map']['dir'] = str(ROOT / 'arena')


def observation():
    o = copy.deepcopy(next(m for m in RECORDING if m['type'] == 'obs'))
    o.update(t=10, tick=200, mechs=[], projectiles=[], crates=[], cubes=[], pickups=[], drops=[], events=[], sounds=[], offer=None)
    o['zone'] = dict(center=[0, 0], radius=500, next_center=[0, 0], next_radius=300, shrink_start=60, shrink_end=110, dps=20)
    c = HELLO['config']
    o['self'].update(pos=[0, 0, 0], vel=[0, 0, 0], aim=[0, 0], hp=1400, max_hp=1400, energy=100, max_energy=100, grounded=True, in_zone=True, cubes=0, stats=copy.deepcopy(c['mech']))
    o['self']['weapons'] = [dict(kind='gun', cooldown=0, ammo=12, reload=0, stats=copy.deepcopy(c['weapons']['gun'])), None]
    return o


def enemy(x=80, y=0):
    return dict(id=9, kind='mech', pos=[x, y, 2.5], vel=[0, 0, 0], aim=[math.pi, 0], hp=1400, max_hp=1400, cubes=0, weapons=['gun'], charging=False)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.bot = Bot(HELLO)
        self.bot.arena = None  # Strategy tests isolate decisions from campus geometry.
        self.obs = observation()

    def test_zone_overrides_loot(self):
        self.obs['self']['pos'] = [510, 0, 0]
        self.obs['cubes'] = [dict(id=5001, pos=[520, 0, 0])]
        a = self.bot.act(self.obs)
        self.assertLess(a['move'][0], -.9)
        self.assertTrue(a['boost'])

    def test_healing_avoids_resetting_regeneration(self):
        self.obs['self']['hp'] = 300
        self.obs['mechs'] = [enemy()]
        self.obs['crates'] = [dict(id=1000, pos=[20, 0, 0], hp=300)]
        a = self.bot.act(self.obs)
        self.assertEqual(a['fire'], [False, False])
        self.assertLess(a['move'][0], 0)

    def test_pickup_rejects_different_floor(self):
        self.obs['pickups'] = [dict(id=8000, kind='laser', pos=[1, 0, 8])]
        self.assertNotIn('take', self.bot.act(self.obs))
        self.obs['pickups'][0]['pos'][2] = 0
        self.assertEqual(self.bot.act(self.obs)['take'], 8000)

    def test_actual_aim_gates_firing(self):
        self.obs['mechs'] = [enemy()]
        self.obs['self']['aim'] = [math.pi, 0]
        self.assertFalse(self.bot.act(self.obs)['fire'][0])
        self.obs['self']['aim'] = [0, 0]
        self.assertTrue(self.bot.act(self.obs)['fire'][0])

    def test_empty_gun_reloads(self):
        self.obs['self']['weapons'][0]['ammo'] = 0
        a = self.bot.act(self.obs)
        self.assertTrue(a['reload'])
        self.assertFalse(a['fire'][0])

    def test_cooldown_keeps_target_aim_without_midfight_reload(self):
        self.obs['mechs'] = [enemy(80, 40)]
        self.obs['self']['weapons'][0].update(ammo=6, cooldown=.2)
        a = self.bot.act(self.obs)
        self.assertAlmostEqual(a['aim'][0], math.atan2(40, 80))
        self.assertFalse(a['reload'])
        self.assertFalse(a['fire'][0])

    def test_tiny_zone_requires_precise_arrival(self):
        self.obs['zone'].update(radius=.5, next_radius=0)
        self.obs['self']['pos'] = [1, 0, 0]
        a = self.bot.act(self.obs)
        self.assertLess(a['move'][0], -.9)

    def test_charging_laser_retains_aim_over_gun_target(self):
        self.obs['mechs'] = [enemy(80, 40)]
        self.obs['mechs'][0]['vel'] = [0, 15, 0]
        self.obs['self']['weapons'][1] = dict(kind='laser', cooldown=0, charge=.4, stats=HELLO['config']['weapons']['laser'])
        a = self.bot.act(self.obs)
        self.assertAlmostEqual(a['aim'][0], math.atan2(40, 80))
        self.assertTrue(a['fire'][1])

    def test_laser_charges_then_releases(self):
        self.obs['mechs'] = [enemy()]
        self.obs['self']['weapons'] = [None, dict(kind='laser', cooldown=0, charge=0, stats=HELLO['config']['weapons']['laser'])]
        self.assertTrue(self.bot.act(self.obs)['fire'][1])
        self.obs['self']['weapons'][1]['charge'] = .75
        self.assertFalse(self.bot.act(self.obs)['fire'][1])
        self.obs['self']['aim'] = [math.pi, 0]
        self.assertTrue(self.bot.act(self.obs)['fire'][1])

    def test_lightning_preserves_energy(self):
        self.obs['mechs'] = [enemy(25)]
        self.obs['self']['weapons'] = [None, dict(kind='lightning', cooldown=0, stats=HELLO['config']['weapons']['lightning'])]
        self.obs['self']['energy'] = 3
        self.assertFalse(self.bot.act(self.obs)['fire'][1])

    def test_mortar_rejects_close_and_unreachable_targets(self):
        w = dict(kind='mortar', stats=HELLO['config']['weapons']['mortar'])
        self.assertIsNone(self.bot.solution(self.obs['self'], enemy(10), w))
        self.assertIsNone(self.bot.solution(self.obs['self'], enemy(1000), w))
        self.assertIsNotNone(self.bot.solution(self.obs['self'], enemy(100), w))

    def test_stuck_recovery_triggers(self):
        self.obs['self']['pos'] = [510, 0, 0]
        for i in range(15):
            self.obs['t'] = 10+i*.1
            a = self.bot.act(self.obs)
            if a['jump']:
                break
        else:
            self.fail('No stuck recovery jump')

    def test_intercept_solution_hits_moving_target(self):
        r, v, speed = [100, 0, 0], [0, 12, 0], 252
        t = intercept(r, v, speed)
        self.assertGreater(t, 0)
        self.assertAlmostEqual(math.sqrt(sum((r[i]+v[i]*t)**2 for i in range(3))), speed*t)

    def test_recording_has_no_hidden_strategy_exceptions(self):
        bot = Bot(HELLO)
        # Call act directly: the wrapper's exception fallback cannot hide bugs here.
        for o in RECORDING:
            if o['type'] == 'obs':
                a = bot.act(o)
                self.assertEqual(a['tick'], o['tick'])
                self.assertLessEqual(math.hypot(*a['move']), 1.000001)
                self.assertTrue(all(math.isfinite(v) for v in a['aim']))


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.arena = Arena.__new__(Arena)
        self.arena.world = {'bounds': dict(minX=-100, maxX=100, minY=-100, maxY=100)}
        self.arena.height = lambda x, y: 0
        self.prism = dict(base=0, top=20, ring=[5, -10, 5.1, -10, 5.1, 10, 5, 10], holes=[])
        self.arena.nearby = lambda *args: iter([self.prism])

    def test_thin_wall_blocks_bullet(self):
        self.assertFalse(self.arena.ray_clear([0, 0, 7], [20, 0, 7]))

    def test_bridge_allows_shot_underneath(self):
        self.prism['base'] = 10
        self.assertTrue(self.arena.ray_clear([0, 0, 7], [20, 0, 7]))
        self.assertFalse(self.arena.ray_clear([0, 0, 12], [20, 0, 12]))

    def test_body_radius_cannot_clip_wall(self):
        self.assertFalse(self.arena.clear_body(4, 0, 0, 2.5, 9, 1.2))

    def test_hole_is_open(self):
        self.prism['ring'] = [-10, -10, 10, -10, 10, 10, -10, 10]
        self.prism['holes'] = [[-5, -5, 5, -5, 5, 5, -5, 5]]
        self.assertTrue(self.arena.clear_body(0, 0, 0, 2.5, 9, 1.2))
        self.assertFalse(self.arena.clear_body(4, 0, 0, 2.5, 9, 1.2))

    def test_steering_routes_along_wall(self):
        stats = HELLO['config']['mech']
        move = steer(self.arena, [0, 0, 0], [20, 0], stats, dict(center=[0, 0], radius=90), [0, 0], 1)
        self.assertNotEqual(move, [0., 0.])
        self.assertTrue(self.arena.travel_clear([0, 0, 0], move, 6, stats))

    def test_walk_on_roof_without_treating_ground_as_drop(self):
        self.prism.update(ring=[-20, -20, 20, -20, 20, 20, -20, 20], top=20)
        self.assertTrue(self.arena.travel_clear([0, 0, 20], [1, 0], 6, HELLO['config']['mech']))

    def test_roof_above_head_is_not_a_walkable_surface(self):
        self.prism.update(ring=[-20, -20, 20, -20, 20, 20, -20, 20], base=10, top=12)
        self.assertTrue(self.arena.travel_clear([0, 0, 0], [1, 0], 6, HELLO['config']['mech']))

    def test_roof_edge_does_not_allow_large_drop(self):
        self.prism.update(ring=[-20, -20, 2, -20, 2, 20, -20, 20], top=20)
        self.assertFalse(self.arena.travel_clear([0, 0, 20], [1, 0], 6, HELLO['config']['mech']))


if __name__ == '__main__':
    unittest.main()
