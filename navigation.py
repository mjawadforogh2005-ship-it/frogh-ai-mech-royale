"""Static-map queries and bounded local steering; standard library only."""
import array
import json
import math
from pathlib import Path
import sys


def inside(x, y, ring):
    hit = False
    for i in range(0, len(ring), 2):
        ax, ay = ring[i - 2:i] if i else ring[-2:]
        bx, by = ring[i:i + 2]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            hit = not hit
    return hit


def edge_distance(x, y, ring):
    best = float('inf')
    for i in range(0, len(ring), 2):
        ax, ay = ring[i - 2:i] if i else ring[-2:]
        bx, by = ring[i:i + 2]
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / max(1e-12, dx*dx + dy*dy)))
        best = min(best, math.hypot(x - ax - t*dx, y - ay - t*dy))
    return best


class Arena:
    def __init__(self, directory):
        root = Path(directory) / 'v2'
        self.world = json.loads((root / 'world.json').read_text(encoding='utf-8'))
        self.terrain = self.world['terrain']
        self.heights = array.array('h')
        self.heights.frombytes((root / 'terrain.i16').read_bytes())
        if sys.byteorder != 'little':
            self.heights.byteswap()
        if len(self.heights) != self.terrain['nx'] * self.terrain['ny']:
            raise ValueError('Unexpected terrain grid size')
        self.cells = {}
        for p in self.world['prisms']:
            xs, ys = p['ring'][::2], p['ring'][1::2]
            for ix in range(math.floor(min(xs)/24), math.floor(max(xs)/24) + 1):
                for iy in range(math.floor(min(ys)/24), math.floor(max(ys)/24) + 1):
                    self.cells.setdefault((ix, iy), []).append(p)

    def height(self, x, y):
        t = self.terrain
        u = max(0, min(t['nx'] - 1.000001, (x - t['minX']) / t['step']))
        v = max(0, min(t['ny'] - 1.000001, (y - t['minY']) / t['step']))
        i, j = int(u), int(v)
        a, b = u - i, v - j
        k, n = j * t['nx'] + i, t['nx']
        h = self.heights
        return ((1-b)*((1-a)*h[k]+a*h[k+1]) + b*((1-a)*h[k+n]+a*h[k+n+1])) * t.get('scale', .01)

    def nearby(self, x, y, radius=0):
        seen = set()
        for ix in range(math.floor((x-radius)/24), math.floor((x+radius)/24)+1):
            for iy in range(math.floor((y-radius)/24), math.floor((y+radius)/24)+1):
                for p in self.cells.get((ix, iy), ()):
                    if id(p) not in seen:
                        seen.add(id(p))
                        yield p

    @staticmethod
    def solid(p, x, y, radius=0):
        outer = inside(x, y, p['ring']) or (radius > 0 and edge_distance(x, y, p['ring']) < radius)
        return outer and not any(inside(x, y, h) and edge_distance(x, y, h) >= radius for h in p.get('holes', []))

    def clear_body(self, x, y, z, radius, height, step):
        b = self.world['bounds']
        if not (b['minX']+radius <= x <= b['maxX']-radius and b['minY']+radius <= y <= b['maxY']-radius):
            return False
        for p in self.nearby(x, y, radius):
            if p['top'] > z + step and p['base'] < z + height and self.solid(p, x, y, radius):
                return False
        return True

    def ray_clear(self, start, end):
        # Intersect polygon boundary intervals exactly so thin walls are not skipped.
        dx, dy, dz = [end[i]-start[i] for i in range(3)]
        length = math.dist(start, end)
        candidates = {}
        for k in range(max(1, math.ceil(length/12))+1):
            t = k/max(1, math.ceil(length/12))
            for p in self.nearby(start[0]+dx*t, start[1]+dy*t, 12):
                candidates[id(p)] = p
        for p in candidates.values():
            cuts = [0., 1.]
            for ring in [p['ring'], *p.get('holes', [])]:
                for i in range(0, len(ring), 2):
                    ax, ay = ring[i-2:i] if i else ring[-2:]
                    bx, by = ring[i:i+2]
                    ex, ey = bx-ax, by-ay
                    den = dx*ey-dy*ex
                    if abs(den) > 1e-9:
                        qx, qy = ax-start[0], ay-start[1]
                        t, u = (qx*ey-qy*ex)/den, (qx*dy-qy*dx)/den
                        if 0 < t < 1 and 0 <= u <= 1:
                            cuts.append(t)
            if abs(dz) > 1e-9:
                cuts += [t for z in (p['base'], p['top']) if 0 < (t := (z-start[2])/dz) < 1]
            cuts.sort()
            for lo, hi in zip(cuts, cuts[1:]):
                t = (lo+hi)/2
                z = start[2]+dz*t
                if p['base'] < z < p['top'] and self.solid(p, start[0]+dx*t, start[1]+dy*t):
                    return False
        steps = max(1, math.ceil(length/2))
        return all(start[2]+dz*k/steps > self.height(start[0]+dx*k/steps, start[1]+dy*k/steps) for k in range(1, steps+1))

    def travel_clear(self, pos, direction, distance, stats, max_drop=4):
        radius, height = stats['radius'], stats['height']
        step = stats['step_up']
        previous = pos[2]
        for k in range(1, max(1, math.ceil(distance/1.5))+1):
            d = min(distance, k*1.5)
            x, y = pos[0]+direction[0]*d, pos[1]+direction[1]*d
            ground = self.height(x, y)
            # Roofs and bridge decks are walking surfaces too. Only consider
            # surfaces reachable from our previous foot height, not roofs above us.
            for p in self.nearby(x, y):
                if ground < p['top'] <= previous + step and self.solid(p, x, y):
                    ground = p['top']
            # Ground navigation deliberately avoids large drops and unwalkable rises.
            if ground - previous > step or previous - ground > max_drop:
                return False
            if not self.clear_body(x, y, ground, radius, height, step):
                return False
            previous = ground
        return True


def unit(x, y):
    d = math.hypot(x, y)
    return [x/d, y/d] if d > 1e-6 else [0., 0.]


def steer(arena, pos, goal, stats, zone, last, side, max_drop=4):
    desired = unit(goal[0]-pos[0], goal[1]-pos[1])
    if desired == [0., 0.]:
        return desired
    if arena is None:
        return desired
    angle = math.atan2(desired[1], desired[0])
    candidates = []
    for i in range(16):
        a = angle + side * i * math.pi/8
        direction = [math.cos(a), math.sin(a)]
        if not arena.travel_clear(pos, direction, 6, stats, max_drop):
            continue
        x, y = pos[0]+direction[0]*6, pos[1]+direction[1]*6
        outside = max(0, math.hypot(x-zone['center'][0], y-zone['center'][1])-zone['radius'])
        score = 3*(direction[0]*desired[0]+direction[1]*desired[1]) + .8*(direction[0]*last[0]+direction[1]*last[1]) - outside*.3
        candidates.append((score, -i, direction))
    return max(candidates)[2] if candidates else [0., 0.]


class Planner:
    """Incremental grid A* over ground-level walkable cells.

    Fixes the failure where local steering jitters against a wall (courtyards,
    building fronts) because it only looks 6 m ahead. Work is spread across
    decisions (`advance(budget)`) so one decision never exceeds the 50 ms limit.
    """
    NEIGH = [(1, 0, 1.), (-1, 0, 1.), (0, 1, 1.), (0, -1, 1.),
             (1, 1, 1.4143), (1, -1, 1.4143), (-1, 1, 1.4143), (-1, -1, 1.4143)]

    def __init__(self, arena, stats, cell=2.0, weight=2.0, max_expansions=60000):
        self.arena, self.cell, self.weight, self.max_exp = arena, cell, weight, max_expansions
        self.radius = stats['radius'] + .1
        self.height, self.step = stats['height'], stats['step_up']
        b = arena.world['bounds']
        self.minx, self.miny = b['minX'], b['minY']
        self.memo = {}
        self.reset()

    def reset(self):
        self.state = None   # None / 'search' / 'done' / 'failed'
        self.path = []
        self.goal_xy = None
        self.age_t = None

    def cell_of(self, x, y):
        return int((x-self.minx)//self.cell), int((y-self.miny)//self.cell)

    def center(self, c):
        return self.minx+(c[0]+.5)*self.cell, self.miny+(c[1]+.5)*self.cell

    def walkable(self, c):
        v = self.memo.get(c)
        if v is None:
            x, y = self.center(c)
            h = self.arena.height(x, y)
            v = self.arena.clear_body(x, y, h, self.radius, self.height, self.step)
            self.memo[c] = v
        return v

    def snap(self, c, limit=10):
        """Nearest walkable cell to c (ring search)."""
        if self.walkable(c):
            return c
        for r in range(1, limit+1):
            best = None
            for dx in range(-r, r+1):
                for dy in range(-r, r+1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    n = (c[0]+dx, c[1]+dy)
                    if self.walkable(n):
                        d = dx*dx+dy*dy
                        if best is None or d < best[0]:
                            best = (d, n)
            if best:
                return best[1]
        return None

    def edge_ok(self, a, b):
        xa, ya = self.center(a)
        xb, yb = self.center(b)
        d = math.hypot(xb-xa, yb-ya)
        if abs(self.arena.height(xb, yb)-self.arena.height(xa, ya)) > .85*d:
            return False
        xm, ym = (xa+xb)/2, (ya+yb)/2
        return self.arena.clear_body(xm, ym, self.arena.height(xm, ym), self.radius, self.height, self.step)

    def start(self, start_xy, goal_xy, now=0.):
        s = self.snap(self.cell_of(*start_xy))
        g = self.snap(self.cell_of(*goal_xy), 20)
        self.path, self.goal_xy, self.age_t = [], list(goal_xy), now
        if s is None or g is None:
            self.state = 'failed'
            return
        self.goal = g
        self.open = [(0., 0., s)]
        self.gscore = {s: 0.}
        self.parent = {}
        self.closed = set()
        self.expansions = 0
        self.state = 'search'

    def h(self, c):
        dx, dy = abs(c[0]-self.goal[0]), abs(c[1]-self.goal[1])
        return (max(dx, dy)+.4143*min(dx, dy))

    def advance(self, budget=400, max_ms=None):
        import heapq
        import time
        if self.state != 'search':
            return self.state
        deadline = time.perf_counter()+max_ms/1000. if max_ms else None
        for n in range(budget):
            if deadline and time.perf_counter() > deadline:
                return self.state
            if not self.open or self.expansions > self.max_exp:
                self.state = 'failed'
                return self.state
            _, g, cur = heapq.heappop(self.open)
            if cur in self.closed:
                continue
            self.closed.add(cur)
            self.expansions += 1
            if cur == self.goal:
                cells = [cur]
                while cells[-1] in self.parent:
                    cells.append(self.parent[cells[-1]])
                self.path = [self.center(c) for c in reversed(cells)]
                self.state = 'done'
                return self.state
            for dx, dy, cost in self.NEIGH:
                n = (cur[0]+dx, cur[1]+dy)
                if n in self.closed or not self.walkable(n):
                    continue
                if dx and dy and not (self.walkable((cur[0]+dx, cur[1])) and self.walkable((cur[0], cur[1]+dy))):
                    continue
                if not self.edge_ok(cur, n):
                    continue
                ng = g+cost
                if ng < self.gscore.get(n, 1e18):
                    self.gscore[n] = ng
                    self.parent[n] = cur
                    heapq.heappush(self.open, (ng+self.weight*self.h(n), ng, n))
        return self.state

    def waypoint(self, pos, stats, lookahead=30.):
        """Farthest path point within lookahead that is straight-line clear."""
        if self.state != 'done' or not self.path:
            return None
        # progress: closest path point
        i0 = min(range(len(self.path)), key=lambda i: math.dist(pos[:2], self.path[i]))
        self.path = self.path[i0:]
        # Check farthest candidates first and stop at the first clear one:
        # clear straight lines are the expensive checks, blocked ones fail fast.
        cands = [p for p in self.path[:14] if 1. <= math.dist(pos[:2], p) <= lookahead]
        for p in reversed(cands):
            d = math.dist(pos[:2], p)
            u = unit(p[0]-pos[0], p[1]-pos[1])
            if self.arena.travel_clear(pos, u, d, stats):
                return p
        return self.path[1] if len(self.path) > 1 else None
