"""Layers 2–3: single-mech movement and deterministic synthetic zone schedules."""
import copy
import math
import random


class Zone:
    def __init__(self, config, seed):
        rng = random.Random(seed)
        self.phases = []
        center, radius, t = list(config['start_center']), config['start_radius'], 0.
        for phase in config['phases']:
            angle = rng.uniform(-math.pi, math.pi)
            distance = math.sqrt(rng.random()) * (radius - phase['radius'])
            nxt = [center[0]+math.cos(angle)*distance, center[1]+math.sin(angle)*distance]
            start, end = t+phase['hold_s'], t+phase['hold_s']+phase['shrink_s']
            self.phases.append((center, radius, nxt, phase['radius'], start, end, phase['dps']))
            center, radius, t = nxt, phase['radius'], end
        self.close_time = t

    def at(self, t):
        phase = next((p for p in self.phases if t < p[5]), self.phases[-1])
        old, radius, nxt, new_radius, start, end, dps = phase
        f = max(0., min(1., (t-start)/max(1e-9, end-start)))
        return dict(center=[old[i]+f*(nxt[i]-old[i]) for i in range(2)],
                    radius=radius+f*(new_radius-radius), next_center=list(nxt),
                    next_radius=new_radius, shrink_start=start, shrink_end=end, dps=dps)


def slew(current, target, limit, wrapped=False):
    delta = (target-current+math.pi) % (2*math.pi)-math.pi if wrapped else target-current
    return current + max(-limit, min(limit, delta))


class World:
    def __init__(self, arena, config, template, seed, layer='zone', spawn=None):
        self.arena, self.config, self.layer = arena, config, layer
        self.zone = Zone(config['zone'], seed)
        self.movement_goal = self.spawn(seed ^ 0xCAFE)[:2] if layer == 'movement' else None
        self.me = copy.deepcopy(template['self'])
        s = copy.deepcopy(config['mech'])
        self.me.update(id=0, pos=list(spawn) if spawn else self.spawn(seed), vel=[0., 0., 0.],
                       aim=[0., 0.], hp=s['hp'], max_hp=s['hp'], energy=s['energy'],
                       max_energy=s['energy'], grounded=True, cubes=0, upgrades=[], stats=s,
                       weapons=[dict(kind='gun', ammo=config['weapons']['gun']['mag'],
                                     cooldown=0, reload=0, stats=copy.deepcopy(config['weapons']['gun'])), None])
        self.t, self.tick = 0., 0
        self.events = []
        self.last_energy_spend = self.last_damage = -float('inf')
        self.zone_damage = self.outside_s = self.blocked_s = 0.
        self.blocked_last = False
        self.aim_target = [0., 0.]
        self.refresh_zone()

    def spawn(self, seed):
        rng, s = random.Random(seed ^ 0x5127), self.config['mech']
        b, z = self.arena.world['bounds'], self.config['zone']
        margin = max(s['radius'], self.config['match']['arena_margin'])
        for _ in range(10000):
            x, y = rng.uniform(b['minX']+margin, b['maxX']-margin), rng.uniform(b['minY']+margin, b['maxY']-margin)
            height = self.arena.height(x, y)
            if (math.dist([x, y], z['start_center']) < z['start_radius']*self.config['match']['spawn_zone_fraction']
                    and self.arena.clear_body(x, y, height, s['radius'], s['height'], 0)
                    and self.slope(x, y) <= math.tan(math.radians(s['max_slope_deg']))):
                return [x, y, height]
        raise ValueError('Could not find a clear spawn')

    def slope(self, x, y):
        h = self.arena.height
        return math.hypot((h(x+.25, y)-h(x-.25, y))/.5, (h(x, y+.25)-h(x, y-.25))/.5)

    def support(self, x, y, ceiling):
        floor = self.arena.height(x, y)
        for p in self.arena.nearby(x, y):
            if floor < p['top'] <= ceiling and self.arena.solid(p, x, y):
                floor = p['top']
        return floor

    def refresh_zone(self):
        self.current_zone = self.zone.at(self.t) if self.layer == 'zone' else dict(
            center=list(self.config['zone']['start_center']), radius=self.config['zone']['start_radius'],
            next_center=list(self.config['zone']['start_center']), next_radius=self.config['zone']['start_radius'],
            shrink_start=1e9, shrink_end=1e9+1, dps=0)
        self.me['in_zone'] = math.dist(self.me['pos'][:2], self.current_zone['center']) <= self.current_zone['radius']
        if self.layer == 'movement':
            # Diagnostic-only objective: prompt navigation to a clear point
            # using zone metadata, but apply no shrinking or zone damage.
            self.current_zone.update(next_center=self.movement_goal, next_radius=0,
                                     shrink_start=0, shrink_end=0)

    def observation(self):
        self.refresh_zone()
        # No placement inference: 16 is only a protocol placeholder; opponents
        # and elimination scoring are deliberately not implemented in these layers.
        return dict(type='obs', tick=self.tick, t=self.t, alive=16, self=copy.deepcopy(self.me),
                    mechs=[], projectiles=[], crates=[], cubes=[], pickups=[], sounds=[], beams=[],
                    events=copy.deepcopy(self.events), drops=[], offer=None, zone=copy.deepcopy(self.current_zone))

    def horizontal(self, dx, dy):
        me, s = self.me, self.me['stats']
        x, y, z = me['pos']
        nx, ny = x+dx, y+dy
        floor = self.support(nx, ny, z + (s['step_up'] if me['grounded'] else 0))
        nz = z
        if me['grounded']:
            for p in self.arena.nearby(nx, ny, s['radius']):
                if (floor < p['top'] <= z+s['step_up'] and
                        self.arena.solid(p, nx, ny, s['radius'])):
                    floor = p['top']
            if floor > z+s['step_up']:
                return False
            terrain = self.arena.height(nx, ny)
            if floor == terrain and floor > z+.001 and self.slope(nx, ny) > math.tan(math.radians(s['max_slope_deg'])):
                return False
            if floor >= z-s['step_up']:
                nz = floor
        elif floor > z+.001:
            return False
        if not self.arena.clear_body(nx, ny, nz, s['radius'], s['height'], 0):
            return False
        me['pos'] = [nx, ny, nz]
        if floor < nz-.001:
            me['grounded'] = False
        return True

    def step(self, action):
        dt = 1/self.config['match']['tick_hz']
        self.tick += 1
        self.t = self.tick * dt
        me, s = self.me, self.me['stats']
        self.blocked_last = False
        if self.layer == 'harness':
            self.refresh_zone()
            return
        if 'aim' in action:
            self.aim_target = list(action['aim'])
        me['aim'] = [slew(me['aim'][0], self.aim_target[0], s['turn_rate']*dt, True),
                     slew(me['aim'][1], max(s['min_pitch'], min(s['max_pitch'], self.aim_target[1])), s['pitch_rate']*dt)]
        move = action.get('move', [0, 0])
        length = math.hypot(*move)
        direction = [v/max(1, length) for v in move]
        if action.get('jump') and me['grounded']:
            me['vel'][2], me['grounded'] = s['jump_speed'], False
        boosting = bool(action.get('boost') and (not me['grounded'] or length > 0))
        drain = s['boost_drain'] if me['grounded'] else s['jet_drain']
        boosting = boosting and me['energy'] >= drain*dt
        if boosting:
            me['energy'] -= drain*dt
            self.last_energy_spend = self.t
        elif self.t-self.last_energy_spend >= s['energy_regen_delay']:
            me['energy'] = min(me['max_energy'], me['energy']+s['energy_regen']*dt)
        speed = s['boost_speed'] if boosting and me['grounded'] else s['walk_speed']
        acceleration = s['ground_accel'] if me['grounded'] else s['air_accel']
        delta = [direction[i]*speed-me['vel'][i] for i in range(2)]
        factor = min(1, acceleration*dt/max(1e-9, math.hypot(*delta)))
        for i in range(2):
            me['vel'][i] += delta[i]*factor
        # Substeps prevent high-speed motion from skipping thin geometry.
        n = max(1, math.ceil(math.hypot(*me['vel'][:2])*dt/.4))
        for _ in range(n):
            dx, dy = me['vel'][0]*dt/n, me['vel'][1]*dt/n
            if not self.horizontal(dx, dy):
                self.blocked_last = True
                if not self.horizontal(dx, 0):
                    me['vel'][0] = 0
                if not self.horizontal(0, dy):
                    me['vel'][1] = 0
        if self.blocked_last and length > .1:
            self.blocked_s += dt
        if not me['grounded']:
            me['vel'][2] += ((s['jet_accel'] if boosting else 0)-s['gravity'])*dt
            me['vel'][2] = min(s['max_rise_speed'], me['vel'][2])
            x, y, old = me['pos']
            new = old+me['vel'][2]*dt
            if new > old:
                for p in self.arena.nearby(x, y, s['radius']):
                    if (old+s['height'] <= p['base'] < new+s['height'] and
                            self.arena.solid(p, x, y, s['radius'])):
                        new, me['vel'][2] = p['base']-s['height'], 0
            floor = self.support(x, y, max(old, new))
            if new <= floor and me['vel'][2] <= 0:
                new, me['vel'][2], me['grounded'] = floor, 0, True
            me['pos'][2] = new
        self.refresh_zone()
        if self.layer == 'zone' and not me['in_zone']:
            damage = self.current_zone['dps']*dt
            me['hp'] = max(0, me['hp']-damage)
            self.zone_damage += damage
            self.outside_s += dt
            self.last_damage = self.t
            self.events.append(dict(type='damaged', amount=damage, weapon='zone', bearing=None, by=None))
        elif self.t-self.last_damage >= s['regen_delay']:
            me['hp'] = min(me['max_hp'], me['hp']+s['regen_per_s']*dt)
