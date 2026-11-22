"""Simplified Mech Royale simulator (guns only) for relative bot evaluation.

Models: terrain/walls/roofs movement, shrinking zone, rear-armor gun combat,
vision (45 m proximity, 200 m / 120 deg wedge, line of sight), HP regen, placement.
Not modelled: cubes, crates, NPCs, loot weapons, sound, supply drops, upgrades.
Results are relative ("better/worse than before"), not real scores.
"""
import gzip, json, math, random, sys, os, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from navigation import Arena

DT = 0.05
_arena = None
def arena():
    global _arena
    if _arena is None:
        _arena = Arena(os.path.join(ROOT, 'arena'))
    return _arena

def load_hello():
    with gzip.open(os.path.join(ROOT, 'check', 'stream.jsonl.gz'), 'rt') as f:
        h = json.loads(f.readline())
    h['map']['dir'] = os.path.join(ROOT, 'arena')
    return h

def ang(a): return (a + math.pi) % (2*math.pi) - math.pi

class Mech:
    def __init__(s, i, pos, cfg):
        s.id, s.pos, s.vel = i, list(pos), [0., 0., 0.]
        s.aim = [random.uniform(-math.pi, math.pi), 0.]
        s.cmd_aim = list(s.aim)
        s.hp = s.max_hp = cfg['mech']['hp']; s.energy = 100.
        s.alive, s.grounded = True, True
        s.cd, s.ammo, s.reload_t = 0., 12, 0.
        s.last_dmg_t = -99.; s.action = {}; s.events = []
        s.place = None; s.cause = None; s.stuck_ticks = 0; s.bot = None; s.kind = 'idle'
        s.dist_moved = 0.

class Sim:
    def __init__(s, seed, enemy_kind='runner', bot_factory=None, hello=None, max_s=480):
        s.rng = random.Random(seed); random.seed(seed)
        s.A = arena(); s.hello = hello or load_hello(); s.cfg = s.hello['config']
        s.max_s = max_s; s.t = 0.; s.tick = 0
        s.mechs = []; s.proj = []; s.pid = 1000; s.deaths = []
        s.make_zone()
        s.spawn(enemy_kind, bot_factory)

    def ground(s, x, y, z, step):
        g = s.A.height(x, y)
        for p in s.A.nearby(x, y):
            if g < p['top'] <= z + step + 1e-6 and s.A.solid(p, x, y): g = p['top']
        return g

    def make_zone(s):
        z = s.cfg['zone']; c = list(z['start_center']); r = z['start_radius']; t = 0.; s.phases = []
        for ph in z['phases']:
            nr = ph['radius']; a = s.rng.uniform(0, 2*math.pi); d = s.rng.uniform(0, max(0, r-nr)) * .9
            nc = [c[0]+d*math.cos(a), c[1]+d*math.sin(a)]
            s.phases.append(dict(c0=c, r0=r, c1=nc, r1=nr, t0=t+ph['hold_s'], t1=t+ph['hold_s']+ph['shrink_s'], dps=ph['dps']))
            t += ph['hold_s']+ph['shrink_s']; c, r = nc, nr

    def zone(s):
        for ph in s.phases:
            if s.t < ph['t1']:
                f = 0 if s.t <= ph['t0'] else (s.t-ph['t0'])/(ph['t1']-ph['t0'])
                c = [ph['c0'][k]+(ph['c1'][k]-ph['c0'][k])*f for k in (0, 1)]
                r = ph['r0']+(ph['r1']-ph['r0'])*f
                return dict(center=c, radius=r, next_center=ph['c1'], next_radius=ph['r1'],
                            shrink_start=ph['t0'], shrink_end=ph['t1'], dps=ph['dps'])
        ph = s.phases[-1]
        return dict(center=ph['c1'], radius=0, next_center=ph['c1'], next_radius=0, shrink_start=ph['t0'], shrink_end=ph['t1'], dps=ph['dps'])

    def spawn(s, enemy_kind, bot_factory):
        z = s.cfg['zone']; b = s.A.world['bounds']; pts = []; tries = 0
        while len(pts) < 16 and tries < 20000:
            tries += 1
            a = s.rng.uniform(0, 2*math.pi); r = z['start_radius']*.8*math.sqrt(s.rng.random())
            x, y = z['start_center'][0]+r*math.cos(a), z['start_center'][1]+r*math.sin(a)
            if not (b['minX']+40 < x < b['maxX']-40 and b['minY']+40 < y < b['maxY']-40): continue
            gz = s.A.height(x, y)
            if not s.A.clear_body(x, y, gz, 3, 9, 1.2): continue
            if any(math.dist((x, y), p[:2]) < 130*(1-tries/40000) for p in pts): continue
            pts.append((x, y, gz))
        s.rng.shuffle(pts)
        for i, p in enumerate(pts):
            m = Mech(i, p, s.cfg); s.mechs.append(m)
        s.me = s.mechs[0]; s.me.kind = 'bot'
        s.me.bot = bot_factory(dict(s.hello, slot=0, seed=s.rng.randrange(2**31)))
        for m in s.mechs[1:]: m.kind = enemy_kind

    # ---- visibility
    def sees(s, a, b):
        if not b.alive: return False
        d = math.dist(a.pos[:2], b.pos[:2])
        v = s.cfg['vision']
        if d > v['range']: return False
        if d > v['near_range']:
            yaw = math.atan2(b.pos[1]-a.pos[1], b.pos[0]-a.pos[0])
            if abs(ang(yaw-a.aim[0])) > math.radians(v['fov_deg'])/2: return False
        return s.A.ray_clear([a.pos[0], a.pos[1], a.pos[2]+8], [b.pos[0], b.pos[1], b.pos[2]+4.5])

    def make_obs(s, m, vis):
        z = s.zone(); cfg = s.cfg
        gun = {"kind": "gun", "cooldown": round(max(0, m.cd), 3), "stats": cfg['weapons']['gun'], "ammo": m.ammo, "reload": round(m.reload_t, 3)}
        d = math.dist(m.pos[:2], z['center'])
        others = [dict(id=o.id, kind='mech', pos=[round(c, 2) for c in o.pos], vel=[round(c, 2) for c in o.vel],
                       aim=[round(c, 3) for c in o.aim], hp=round(o.hp), max_hp=o.max_hp, cubes=0, weapons=['gun'], charging=False)
                  for o in s.mechs if o is not m and o.alive and (m.id, o.id) in vis]
        projs = [dict(id=p['id'], kind='gun', pos=[round(c, 2) for c in p['pos']], vel=[round(c, 2) for c in p['vel']])
                 for p in s.proj if p['owner'] != m.id and math.dist(p['pos'], m.pos) < 200]
        return dict(type='obs', tick=s.tick, t=round(s.t, 2), alive=sum(o.alive for o in s.mechs),
                    self=dict(id=m.id, pos=[round(c, 2) for c in m.pos], vel=[round(c, 2) for c in m.vel],
                              aim=[round(c, 4) for c in m.aim], hp=round(m.hp, 1), max_hp=m.max_hp, energy=round(m.energy, 1),
                              max_energy=100, grounded=m.grounded, cubes=0, upgrades=[], in_zone=d <= z['radius'],
                              weapons=[gun, None], stats=cfg['mech']),
                    mechs=others, projectiles=projs, crates=[], cubes=[], pickups=[], sounds=[], beams=[],
                    events=m.events, zone={k: (v if not isinstance(v, float) else round(v, 2)) for k, v in z.items()},
                    offer=None, drops=[])

    # ---- enemy scripted AI
    def enemy_act(s, m, obs):
        z = obs['zone']; me = obs['self']
        tgt = z['center'] if m.kind != 'idle' else None
        act = {'tick': obs['tick'], 'move': [0, 0], 'fire': [False, False]}
        if m.kind == 'idle': return act
        foe = None
        if obs['mechs']:
            foe = min(obs['mechs'], key=lambda o: math.dist(o['pos'], me['pos']))
        if m.kind == 'runner':
            dx, dy = tgt[0]-me['pos'][0], tgt[1]-me['pos'][1]; d = math.hypot(dx, dy)
            if d > z['radius']*.35: act['move'] = [dx/d, dy/d]
        if foe:
            p = foe['pos']; v = foe['vel']
            dist = math.dist(p, me['pos']); t = dist/252
            px, py, pz = p[0]+v[0]*t, p[1]+v[1]*t, p[2]+4.5
            yaw = math.atan2(py-me['pos'][1], px-me['pos'][0])
            pit = math.atan2(pz-(me['pos'][2]+7), math.hypot(px-me['pos'][0], py-me['pos'][1]))
            act['aim'] = [yaw, pit]
            act['fire'] = [abs(ang(yaw-me['aim'][0])) < .06 and dist < 170, False]
            if m.kind == 'runner' and dist < 60: act['move'] = [0, 0]
        return act

    # ---- physics
    def step_mech(s, m):
        c = s.cfg['mech']; a = m.action
        mv = a.get('move', [0, 0]); n = math.hypot(*mv)
        if n > 1: mv = [mv[0]/n, mv[1]/n]
        speed = c['walk_speed']
        if a.get('boost') and m.energy > 0 and n > 0:
            speed = c['boost_speed']; m.energy = max(0, m.energy - c['boost_drain']*DT)
        else:
            m.energy = min(100, m.energy + c['energy_regen']*DT)
        acc = (c['ground_accel'] if m.grounded else c['air_accel'])*DT
        for k in (0, 1):
            want = mv[k]*speed; dv = want-m.vel[k]
            m.vel[k] += max(-acc, min(acc, dv))
        if a.get('jump') and m.grounded: m.vel[2] = c['jump_speed']; m.grounded = False
        if a.get('boost') and not m.grounded and m.energy > 0:
            m.vel[2] = min(c['max_rise_speed'], m.vel[2]+c['jet_accel']*DT); m.energy = max(0, m.energy-c['jet_drain']*DT)
        # torso slew
        ca = a.get('aim', m.cmd_aim); m.cmd_aim = list(ca)
        dy = max(-c['turn_rate']*DT, min(c['turn_rate']*DT, ang(ca[0]-m.aim[0])))
        m.aim[0] = ang(m.aim[0]+dy)
        dp = max(-c['pitch_rate']*DT, min(c['pitch_rate']*DT, ca[1]-m.aim[1]))
        m.aim[1] = max(c['min_pitch'], min(c['max_pitch'], m.aim[1]+dp))
        # horizontal move with sliding
        x0, y0, z0 = m.pos
        for nx, ny in ((x0+m.vel[0]*DT, y0+m.vel[1]*DT), (x0+m.vel[0]*DT, y0), (x0, y0+m.vel[1]*DT)):
            g = s.ground(nx, ny, z0, c['step_up'])
            if g - z0 <= c['step_up'] and s.A.clear_body(nx, ny, max(z0, g) if g <= z0+c['step_up'] else z0, c['radius'], c['height'], c['step_up']):
                m.pos[0], m.pos[1] = nx, ny
                break
        else:
            m.vel[0] *= .0; m.vel[1] *= .0
        # vertical
        g = s.ground(m.pos[0], m.pos[1], m.pos[2], c['step_up'])
        if m.grounded and m.pos[2]-g < 0.5 and m.vel[2] <= 0:
            m.pos[2] = g
        else:
            m.vel[2] -= c['gravity']*DT; m.pos[2] += m.vel[2]*DT
            if m.pos[2] <= g: m.pos[2] = g; m.vel[2] = 0; m.grounded = True
            else: m.grounded = False
        m.dist_moved += math.dist((x0, y0), m.pos[:2])
        # gun
        m.cd = max(0, m.cd-DT)
        if m.reload_t > 0:
            m.reload_t = max(0, m.reload_t-DT)
            if m.reload_t == 0: m.ammo = 12
        elif a.get('reload') and m.ammo < 12: m.reload_t = c_gun['reload']
        fire = a.get('fire', [False, False])
        if fire and fire[0] and m.cd <= 0 and m.reload_t <= 0 and m.ammo > 0:
            sp = math.radians(c_gun['spread_deg'])
            yaw = m.aim[0]+random.gauss(0, sp); pit = m.aim[1]+random.gauss(0, sp)
            d = [math.cos(pit)*math.cos(yaw), math.cos(pit)*math.sin(yaw), math.sin(pit)]
            o = [m.pos[0]+d[0]*3, m.pos[1]+d[1]*3, m.pos[2]+7+d[2]*3]
            s.pid += 1
            s.proj.append(dict(id=s.pid, owner=m.id, pos=o, vel=[v*252 for v in d], dist=0.))
            m.cd = c_gun['cooldown']; m.ammo -= 1
            if m.ammo == 0: m.reload_t = c_gun['reload']

    def hurt(s, m, amount, by, bearing, cause):
        if not m.alive: return
        rear = False
        if by is not None and bearing is not None:
            rear = abs(ang(bearing-m.aim[0])) > math.pi/2
            if rear: amount *= 1.5
        m.hp -= amount; m.last_dmg_t = s.t
        m.events.append(dict(type='damaged', amount=round(amount, 1), weapon=cause, bearing=bearing, by=by, rear=rear) if by is not None
                        else dict(type='damaged', amount=round(amount, 1), weapon='zone', bearing=None, by=None))
        if m.hp <= 0:
            m.alive = False; m.cause = cause if by is None else f'gun(by {by})'
            m.place = sum(o.alive for o in s.mechs)+1
            s.deaths.append((s.t, m.id, m.cause, list(m.pos)))
            if by is not None and s.mechs[by].alive: s.mechs[by].events.append(dict(type='kill', target=m.id))

    def step_projectiles(s):
        keep = []
        for p in s.proj:
            a = list(p['pos']); b = [a[k]+p['vel'][k]*DT for k in range(3)]
            hit = None
            if not s.A.ray_clear(a, b): continue
            for m in s.mechs:
                if not m.alive or m.id == p['owner']: continue
                # segment vs vertical cylinder (xy distance at closest approach, z in range)
                dx, dy = b[0]-a[0], b[1]-a[1]; L2 = dx*dx+dy*dy
                t = 0 if L2 < 1e-9 else max(0, min(1, ((m.pos[0]-a[0])*dx+(m.pos[1]-a[1])*dy)/L2))
                cx, cy, cz = a[0]+dx*t, a[1]+dy*t, a[2]+(b[2]-a[2])*t
                if math.hypot(cx-m.pos[0], cy-m.pos[1]) < 2.8 and m.pos[2] <= cz <= m.pos[2]+9:
                    hit = m; break
            p['pos'] = b; p['dist'] += math.dist(a, b)
            if hit:
                bearing = math.atan2(a[1]-hit.pos[1], a[0]-hit.pos[0])
                s.hurt(hit, 45, p['owner'], bearing, 'gun')
                if s.mechs[p['owner']].alive: s.mechs[p['owner']].events.append(dict(type='hit', target=hit.id, amount=45, weapon='gun'))
                continue
            if p['dist'] < 230: keep.append(p)
        s.proj = keep

    def run(s, log_path=False):
        global c_gun
        c_gun = s.cfg['weapons']['gun']
        path = []; decisions = 0; slow = 0
        obs_cache = {}
        while s.t < s.max_s:
            alive = [m for m in s.mechs if m.alive]
            if len(alive) <= 1 or not s.me.alive: 
                if not s.me.alive or len(alive) <= 1: break
            # decision every 2 ticks
            if s.tick % 2 == 0:
                vis = set()
                for i, a in enumerate(alive):
                    for b in alive:
                        if a is not b and math.dist(a.pos[:2], b.pos[:2]) <= 200 and s.sees(a, b): vis.add((a.id, b.id))
                for m in alive:
                    obs = s.make_obs(m, vis); m.events = []
                    if m.kind == 'bot':
                        t0 = time.perf_counter()
                        try: act = m.bot.act(obs) or {}
                        except Exception as e:
                            print('BOT EXCEPTION', repr(e), file=sys.stderr); act = {}
                        ms = (time.perf_counter()-t0)*1000; decisions += 1; slow += ms > 50
                        if act.get('tick', obs['tick']) != obs['tick']: act = {}
                        act.pop('debug', None)
                    else: act = s.enemy_act(m, obs)
                    m.action = act
                path.append((round(s.t, 1), [round(c, 1) for c in s.me.pos], round(s.me.hp)))
            for m in alive: s.step_mech(m)
            s.step_projectiles()
            # zone damage, regen
            z = s.zone()
            for m in alive:
                if not m.alive: continue
                if math.dist(m.pos[:2], z['center']) > z['radius']:
                    s.hurt(m, z['dps']*DT, None, None, 'zone')
                elif s.t - m.last_dmg_t > 5 and m.hp < m.max_hp:
                    m.hp = min(m.max_hp, m.hp + 45*DT)
            s.t += DT; s.tick += 1
        n_alive = sum(m.alive for m in s.mechs)
        if s.me.alive: s.me.place = max(1, n_alive) if n_alive > 1 else 1
        res = dict(place=s.me.place, score=16-s.me.place, t=round(s.t, 1), cause=s.me.cause or ('alive' if s.me.alive else '?'),
                   dist_moved=round(s.me.dist_moved), end_pos=[round(c) for c in s.me.pos], hp=round(s.me.hp), slow=slow, decisions=decisions)
        if log_path: res['path'] = path
        return res
