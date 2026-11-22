"""Frogh AI Lab: deterministic survival-first mechfight/1 bot."""
import math
import random
import mechfight as mf
from navigation import Arena, steer, unit


def angle_error(a, b):
    return abs((a-b+math.pi) % (2*math.pi)-math.pi)


def intercept(relative, velocity, speed):
    a = sum(v*v for v in velocity)-speed*speed
    b = 2*sum(x*v for x, v in zip(relative, velocity))
    c = sum(x*x for x in relative)
    if abs(a) < 1e-9:
        return max(0., -c/b) if b < -1e-9 else 0.
    disc = b*b-4*a*c
    roots = [t for t in ((-b-math.sqrt(disc))/(2*a), (-b+math.sqrt(disc))/(2*a)) if t > 0] if disc >= 0 else []
    return min(roots) if roots else 0.


class Bot:
    name = 'Frogh AI Lab'

    def __init__(self, hello):
        self.config = hello['config']
        self.rng = random.Random(hello['seed'])
        self.side = self.rng.choice([-1, 1])
        self.last_move = [0., 0.]
        self.last_wanted = False
        self.recovery_direction = [1., 0.]
        self.last_pos = None
        self.stuck_since = None
        self.recover_until = 0
        self.healing = False
        try:
            self.arena = Arena(hello['map']['dir'])
        except (OSError, ValueError, KeyError) as exc:
            mf.log('Map unavailable; using reactive navigation:', exc)
            self.arena = None

    def stats(self, me):
        return {**self.config['mech'], **me['stats']}

    def weapon_stats(self, w):
        return {**self.config['weapons'][w['kind']], **w.get('stats', {})}

    def safe(self, point, zone, margin=12):
        return math.dist(point[:2], zone['center']) < max(0, zone['radius']-margin)

    def target_point(self, target):
        kind = target.get('kind', 'crate')
        scale = 1+self.config['cubes']['size_per_cube']*target.get('cubes', 0) if kind == 'mech' else 1
        height = self.config['mech']['height']*scale if kind == 'mech' else self.config['npc' if kind == 'npc' else 'crates']['height']
        return [*target['pos'][:2], target['pos'][2]+height*.5]

    def solution(self, me, target, weapon):
        stats = self.stats(me)
        ws = self.weapon_stats(weapon)
        kind = weapon['kind']
        start = [*me['pos'][:2], me['pos'][2]+stats['muzzle_height']]
        if kind == 'mortar':
            start[2] = me['pos'][2]+stats['height']+.5
        point = self.target_point(target)
        vel = target.get('vel', [0, 0, 0])
        if kind == 'gun':
            t = intercept([point[i]-start[i] for i in range(3)], vel, ws['speed'])
            point = [point[i]+vel[i]*min(t, 2) for i in range(3)]
        dx, dy, dz = [point[i]-start[i] for i in range(3)]
        distance = math.hypot(dx, dy)
        yaw, pitch = math.atan2(dy, dx), math.atan2(dz, distance)
        if kind == 'mortar':
            # Low arc, iterated to account for target movement during flight.
            for _ in range(3):
                v, g = ws['speed'], ws['gravity']
                disc = v**4-g*(g*distance**2+2*dz*v*v)
                if disc < 0 or distance < max(20, ws['splash_radius']*3):
                    return None
                pitch = math.atan2(v*v-math.sqrt(disc), g*distance)
                flight = distance/max(1e-6, v*math.cos(pitch))
                point = [self.target_point(target)[i]+vel[i]*flight for i in range(3)]
                dx, dy, dz = [point[i]-start[i] for i in range(3)]
                distance, yaw = math.hypot(dx, dy), math.atan2(dy, dx)
            if flight > ws.get('ttl', 7):
                return None
            if self.arena:
                prev = start
                for k in range(1, 17):
                    t = flight*k/16
                    p = [start[0]+v*math.cos(pitch)*math.cos(yaw)*t, start[1]+v*math.cos(pitch)*math.sin(yaw)*t, start[2]+v*math.sin(pitch)*t-g*t*t/2]
                    if not self.arena.ray_clear(prev, p):
                        return None
                    prev = p
        elif math.dist(start, point) > ws['range'] or (self.arena and not self.arena.ray_clear(start, point)):
            return None
        if not stats['min_pitch'] <= pitch <= stats['max_pitch']:
            return None
        return [yaw, pitch], distance

    def choose_upgrade(self, offer, me):
        weights = {'armor': 100, 'nanites': 95, 'plating': 90, 'servos': 85, 'reactor': 78, 'gyros': 72, 'gun_velocity': 70, 'gun_rate': 68, 'gun_damage': 66, 'laser_charge': 75, 'laser_damage': 72, 'lightning_range': 74, 'gun_mag': 60, 'jets': 58, 'capacitor': 55, 'sensors': 50}
        if me['hp'] < me['max_hp']*.5:
            weights['plating'] = 110
        return max(range(len(offer['choices'])), key=lambda i: weights.get(offer['choices'][i]['id'], 40))

    def act(self, obs):
        me, zone, now = obs['self'], obs['zone'], obs['t']
        pos, stats = me['pos'], self.stats(me)
        ratio = me['hp']/max(1, me['max_hp'])
        self.healing = ratio < .42 or (self.healing and ratio < .82)
        enemies = sorted(
            (e for e in obs['mechs'] if e['kind'] == 'mech'),
            key=lambda e: (
                e['hp']/max(1, e['max_hp']),
                math.dist(pos, e['pos']),
            ),
        )
        nearest = min(enemies, key=lambda e: math.dist(pos, e['pos'])) if enemies else None
        threat_distance = math.dist(pos, nearest['pos']) if nearest else float('inf')
        close_enemies = sum(math.dist(pos, e['pos']) < 130 for e in enemies)
        retreat = self.healing or close_enemies >= 2 or (ratio < .55 and threat_distance < 150)
        # Move early enough to reach the next circle before the shrink ends.
        to_next = max(0, math.dist(pos[:2], zone['next_center'])-zone['next_radius']+18)
        urgent = not self.safe(pos, zone, 18) or now + to_next/max(1, stats['walk_speed'])*1.6 + 8 > zone['shrink_end']
        goal = list(pos[:2])
        mode = 'recover' if self.healing else 'hold'
        if urgent:
            goal, mode = list(zone['next_center']), 'zone'
        elif nearest and retreat and threat_distance < 150:
            away = unit(pos[0]-nearest['pos'][0], pos[1]-nearest['pos'][1])
            goal, mode = [pos[0]+away[0]*35, pos[1]+away[1]*35], 'retreat'
        else:
            loot = []
            if not retreat:
                loot += [(math.dist(pos, c['pos']), c['pos']) for c in obs['cubes'] if abs(c['pos'][2]-pos[2]) < 4]
                if any(w is None for w in me['weapons']):
                    held = {w['kind'] for w in me['weapons'] if w}
                    loot += [(math.dist(pos, p['pos'])+8, p['pos']) for p in obs['pickups'] if p['kind'] not in held and abs(p['pos'][2]-pos[2]) < 4]
            for drop in obs['drops']:
                if drop['lands_in'] <= 3 and threat_distance > 100 and drop.get('holder') in (None, me['id']):
                    loot.append((math.dist(pos[:2], drop['pos'][:2])*.6, drop['pos']))
            loot = [p for p in loot if p[0] < 95 and self.safe(p[1], zone, 25)]
            if loot:
                goal, mode = min(loot, key=lambda p: p[0])[1][:2], 'loot'
            elif nearest and not retreat and threat_distance < 180:
                toward = unit(nearest['pos'][0]-pos[0], nearest['pos'][1]-pos[1])
                desired_range = 125 if 'lightning' in nearest.get('weapons', []) else 105
                radial = max(-1, min(1, (threat_distance-desired_range)/35))
                goal = [pos[0]+25*(toward[0]*radial-toward[1]*self.side), pos[1]+25*(toward[1]*radial+toward[0]*self.side)]
                mode = 'engage'
        # Keep nonurgent destinations inside the current circle.
        if not urgent:
            delta = unit(goal[0]-zone['center'][0], goal[1]-zone['center'][1])
            r = max(0, zone['radius']-20)
            if math.dist(goal[:2], zone['center']) > r:
                goal = [zone['center'][0]+delta[0]*r, zone['center'][1]+delta[1]*r]
        if self.last_pos and math.dist(pos[:2], self.last_pos[:2]) < .15 and self.last_wanted:
            self.stuck_since = now if self.stuck_since is None else self.stuck_since
        else:
            self.stuck_since = None
        stuck = self.stuck_since is not None and now-self.stuck_since > 1.0
        if stuck:
            self.side *= -1
            self.recover_until, self.stuck_since = now+1.2, None
            toward = unit(goal[0]-pos[0], goal[1]-pos[1])
            self.recovery_direction = [-toward[1]*self.side, toward[0]*self.side]
        if now < self.recover_until:
            goal = [pos[0]+self.recovery_direction[0]*12, pos[1]+self.recovery_direction[1]*12]
        arrival = min(2., max(.05, zone['radius']*.2))
        move = steer(self.arena, pos, goal, stats, zone, self.last_move, self.side) if math.dist(pos[:2], goal[:2]) > arrival else [0., 0.]
        # React to approaching projectiles using their closest approach in the next second.
        for p in obs['projectiles'][:24]:
            if p['kind'] == 'mortar':
                continue
            rel = [p['pos'][i]-pos[i]-(stats['height']/2 if i == 2 else 0) for i in range(3)]
            vel = [p['vel'][i]-me['vel'][i] for i in range(3)]
            vv = sum(v*v for v in vel)
            t = -sum(r*v for r, v in zip(rel, vel))/max(1, vv)
            if 0 < t < .65 and math.sqrt(sum((rel[i]+vel[i]*t)**2 for i in range(3))) < stats['radius']+2:
                dodge = unit(-vel[1]*self.side, vel[0]*self.side)
                dest = [pos[0]+dodge[0]*8, pos[1]+dodge[1]*8]
                if self.safe(dest, zone, 4) and (self.arena is None or self.arena.travel_clear(pos, dodge, 6, stats)):
                    move = dodge
                    break
        action = {'tick': obs['tick'], 'move': move, 'fire': [False, False], 'boost': bool(me['grounded'] and me['energy'] > 25 and (urgent or (retreat and threat_distance < 100))), 'jump': bool(stuck and me['grounded'] and me['energy'] > 45)}
        if not me['grounded'] and now < self.recover_until and me['energy'] > 25:
            action['boost'] = True
            action['move'] = self.recovery_direction
        action['aim'] = [math.atan2(nearest['pos'][1]-pos[1], nearest['pos'][0]-pos[0]), 0] if nearest else [(now*.65+self.side) % (2*math.pi)-math.pi, 0]
        if obs['offer'] and obs['offer']['choices']:
            action['pick'] = self.choose_upgrade(obs['offer'], me)
        held = {w['kind'] for w in me['weapons'] if w}
        pickups = [p for p in obs['pickups'] if p['kind'] not in held and math.dist(pos[:2], p['pos'][:2]) <= self.config['loot']['pickup_radius'] and abs(pos[2]-p['pos'][2]) <= 4]
        if pickups and any(w is None for w in me['weapons']):
            action['take'] = max(pickups, key=lambda p: {'laser': 3, 'lightning': 2, 'mortar': 1, 'gun': 0}[p['kind']])['id']
        targets = enemies[:4]
        if enemies:
            targets = enemies[:4]
        elif not retreat and threat_distance > 90:
            targets += sorted(obs['crates'], key=lambda c: math.dist(pos, c['pos']))[:2]
            targets += sorted((e for e in obs['mechs'] if e['kind'] == 'npc'), key=lambda e: math.dist(pos, e['pos']))[:1]
        if self.healing and threat_distance > 45:
            targets = []  # Dealing damage resets health regeneration too.
        choices = []
        solutions = {}
        for target in targets:
            for slot, w in enumerate(me['weapons']):
                if not w or w.get('reload', 0) > 0 or (w['kind'] == 'gun' and w.get('ammo', 0) == 0):
                    continue
                # Continue tracking through cooldown: losing aim between gun
                # shots wastes turn time and used to trigger premature reloads.
                if w['kind'] == 'laser' and retreat and w.get('charge', 0) == 0:
                    continue
                sol = self.solution(me, target, w)
                solutions[(target['id'], slot)] = sol
                if sol:
                    score = (100 if target.get('kind') == 'mech' else 35) - sol[1]*.15 + (100 if w.get('charge', 0) > 0 else 0)
                    if target.get('kind') == 'mech':
                        target_ratio = target['hp']/max(1, target['max_hp'])
                        score += 55*(1-target_ratio)
                    score += {'laser': 12, 'lightning': 15, 'gun': 5, 'mortar': 0}[w['kind']]
                    if w.get('cooldown', 0) > .1:
                        score -= 30
                    choices.append((score, slot, target, sol))
        if choices:
            _, slot, target, sol = max(choices, key=lambda c: c[0])
            action['aim'] = sol[0]
            for i, w in enumerate(me['weapons']):
                if not w:
                    continue
                key = (target['id'], i)
                aim = solutions[key] if key in solutions else self.solution(me, target, w)
                if aim is None:
                    continue
                ws, kind = self.weapon_stats(w), w['kind']
                tolerance = min(.12, math.atan2(1.5, max(1, aim[1])))
                aligned = angle_error(me['aim'][0], aim[0][0]) < tolerance and abs(me['aim'][1]-aim[0][1]) < tolerance
                compatible = angle_error(action['aim'][0], aim[0][0]) < tolerance and abs(action['aim'][1]-aim[0][1]) < tolerance
                ready = w.get('cooldown', 0) <= 0 and w.get('reload', 0) <= 0
                if kind == 'laser':
                    # Release at full charge only once actual aim is aligned.
                    action['fire'][i] = bool(compatible and (w.get('charge', 0) > 0 or (ready and not retreat)) and not (aligned and w.get('charge', 0) >= ws['charge']))
                elif kind == 'lightning':
                    action['fire'][i] = bool(ready and compatible and angle_error(me['aim'][0], aim[0][0]) < math.radians(ws['cone_deg'])*.45 and me['energy'] > ws['energy']+15)
                else:
                    action['fire'][i] = bool(ready and aligned and compatible and (kind != 'gun' or w.get('ammo', 0) > 0))
        for w in me['weapons']:
            if w and w['kind'] == 'gun' and not w.get('reload', 0):
                action['reload'] = bool(w.get('ammo', 0) == 0 or (not choices and threat_distance > 150 and w.get('ammo', 0) < self.weapon_stats(w)['mag']*.65))
        self.last_pos, self.last_move = list(pos), move
        self.last_wanted = math.dist(pos[:2], goal[:2]) > arrival
        mf.draw.label(f'{mode} | hp {ratio:.0%}')
        mf.draw.point([*goal[:2], pos[2]], '#40c0ff', 5)
        return action


if __name__ == '__main__':
    mf.run(Bot)
