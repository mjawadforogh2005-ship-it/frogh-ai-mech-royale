"""Static-map queries and bounded local steering; standard library only."""
import array
import json
import math
import heapq
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

    def travel_clear(self, pos, direction, distance, stats):
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
            if ground - previous > step or previous - ground > 4:
                return False
            if not self.clear_body(x, y, ground, radius, height, step):
                return False
            previous = ground
        return True


def unit(x, y):
    d = math.hypot(x, y)
    return [x/d, y/d] if d > 1e-6 else [0., 0.]


def steer(arena, pos, goal, stats, zone, last, side):
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
        if not arena.travel_clear(pos, direction, 6, stats):
            continue
        x, y = pos[0]+direction[0]*6, pos[1]+direction[1]*6
        outside = max(0, math.hypot(x-zone['center'][0], y-zone['center'][1])-zone['radius'])
        score = 3*(direction[0]*desired[0]+direction[1]*desired[1]) + .8*(direction[0]*last[0]+direction[1]*last[1]) - outside*.3
        candidates.append((score, -i, direction))
    return max(candidates)[2] if candidates else [0., 0.]


def plan_route(arena, pos, goal, stats, spacing=12):
    """Find a bounded grid route around buildings, falling back to local steering."""
    if arena is None:
        return []
    bounds = arena.world['bounds']
    radius = stats['radius']

    def snap(point):
        x = max(bounds['minX'] + radius, min(bounds['maxX'] - radius, point[0]))
        y = max(bounds['minY'] + radius, min(bounds['maxY'] - radius, point[1]))
        ix = round((x-bounds['minX'])/spacing)
        iy = round((y-bounds['minY'])/spacing)
        return ix, iy

    def point(node):
        return (bounds['minX']+node[0]*spacing, bounds['minY']+node[1]*spacing)

    start, destination = snap(pos[:2]), snap(goal)
    if start == destination:
        return []

    min_ix = 0
    max_ix = int(math.ceil((bounds['maxX']-bounds['minX'])/spacing))
    min_iy = 0
    max_iy = int(math.ceil((bounds['maxY']-bounds['minY'])/spacing))
    open_nodes = [(math.dist(point(start), goal), 0, start)]
    came_from = {}
    cost = {start: 0}
    destination_point = point(destination)

    def passable(from_node, node):
        ax, ay = point(from_node)
        bx, by = point(node)
        dx, dy = bx-ax, by-ay
        distance = math.hypot(dx, dy)
        if distance < 1e-9:
            return True
        direction = unit(dx, dy)
        z = arena.height(bx, by)
        if not arena.clear_body(bx, by, z, radius, stats['height'], stats['step_up']):
            return False
        return arena.travel_clear([ax, ay, pos[2]], direction, distance, stats)

    while open_nodes:
        _, _, current = heapq.heappop(open_nodes)
        if current == destination:
            path = []
            while current in came_from:
                path.append(point(current))
                current = came_from[current]
            path.reverse()
            return path

        cx, cy = current
        for dx, dy in ((1,0), (-1,0), (0,1), (0,-1), (1,1), (1,-1), (-1,1), (-1,-1)):
            neighbor = cx+dx, cy+dy
            nx, ny = neighbor
            if not (min_ix <= nx <= max_ix and min_iy <= ny <= max_iy):
                continue
            if not passable(current, neighbor):
                continue
            next_cost = cost[current]+math.dist(point(current), point(neighbor))
            if next_cost >= cost.get(neighbor, float('inf')):
                continue
            came_from[neighbor] = current
            cost[neighbor] = next_cost
            h = math.dist(point(neighbor), destination_point)
            heapq.heappush(open_nodes, (next_cost+h, next_cost, neighbor))
    return []
