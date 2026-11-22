"""Local survival diagnostics, not an implementation of the competition server.

python sim/run_match.py --layer zone --seeds 1 2 3 --output results/zone -- python -u bot.py
"""
import argparse
import copy
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import queue
import random
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from check.check import Schema
from navigation import Arena
from sim.world import World


class Client:
    def __init__(self, command, log, max_bytes):
        self.log = open(log, 'wb')
        self.proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.log, bufsize=0)
        self.lines = queue.Queue(maxsize=64)
        self.max_bytes = max_bytes
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while True:
                raw = self.proc.stdout.readline(self.max_bytes+1)
                self.lines.put((time.perf_counter(), raw))
                if not raw or len(raw) > self.max_bytes:
                    return
        except (OSError, ValueError):
            pass

    def send(self, message):
        self.proc.stdin.write((json.dumps(message, separators=(',', ':'))+'\n').encode())
        self.proc.stdin.flush()

    def receive(self, deadline, tick=None):
        while True:
            try:
                arrived, raw = self.lines.get(timeout=max(0, deadline-time.perf_counter()))
            except queue.Empty:
                return None, None
            if not raw:
                raise RuntimeError('Bot exited before end')
            if len(raw) > self.max_bytes:
                raise ValueError('Bot exceeded protocol line limit')
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError('Protocol line is not an object')
            if tick is not None and message.get('tick') != tick:
                if isinstance(message.get('tick'), int) and message['tick'] < tick:
                    continue  # Discard stale replies after a missed deadline.
                raise ValueError('Action did not echo current tick')
            if arrived > deadline:
                return None, None
            return message, arrived

    def close(self, end):
        clean = False
        try:
            self.send(end)
            self.proc.stdin.close()
            clean = self.proc.wait(timeout=2) == 0
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait(timeout=2)
        finally:
            self.proc.stdout.close()
            self.log.close()
        return clean


def finite_action(action):
    for field, count in (('move', 2), ('aim', 2)):
        if field in action and (len(action[field]) != count or any(not math.isfinite(v) for v in action[field])):
            raise ValueError('Non-finite or malformed '+field)


def run(args, seed, hello, template, schema, arena):
    output = args.output / f'{args.layer}-seed-{seed}'
    output.mkdir(parents=True, exist_ok=True)
    world = World(arena, hello['config'], template, seed, args.layer)
    hello = copy.deepcopy(hello)
    hello.update(slot=0, seed=random.Random(seed ^ 0xB07).getrandbits(32))
    cfg = hello['config']
    client = Client(args.command, output/'stderr.log', cfg['bot']['max_line_bytes'])
    path, times, missed, decisions, longest_stuck, stuck_s = [], [], 0, 0, 0., 0.
    errors = []
    continuous, last_valid = {}, -float('inf')
    window = []
    duration = args.duration if args.duration is not None else cfg['match']['duration_s']
    ticks = math.ceil(duration*cfg['match']['tick_hz'])
    action = {}
    try:
        start = time.perf_counter()
        client.send(hello)
        ready, _ = client.receive(start+cfg['bot']['hello_budget_ms']/1000)
        if ready is None or schema.errors(ready, 'BotMessage') or ready.get('type') != 'ready':
            raise ValueError('Missing or invalid ready')
        while world.tick < ticks and world.me['hp'] > 0:
            obs = world.observation()
            problems = schema.errors(obs, 'ServerMessage')
            if problems:
                raise ValueError('Simulator observation invalid: '+str(problems))
            sent = time.perf_counter()
            client.send(obs)
            reply, arrived = client.receive(sent+cfg['bot']['tick_budget_ms']/1000, world.tick)
            decisions += 1
            if reply is None:
                missed += 1
                action = dict(continuous) if world.t-last_valid <= cfg['bot']['coast_s'] else {}
            else:
                problems = schema.errors(reply, 'BotMessage')
                if problems:
                    raise ValueError('Invalid bot action: '+str(problems))
                finite_action(reply)
                action = reply
                continuous = {k: action[k] for k in ('move', 'aim', 'boost') if k in action}
                last_valid = world.t
                times.append((arrived-sent)*1000)
            world.events.clear()
            before = list(world.me['pos'])
            for sub in range(cfg['match']['decide_every']):
                frame_action = dict(action)
                if sub:
                    frame_action.pop('jump', None)
                world.step(frame_action)
                if world.me['hp'] <= 0 or world.tick >= ticks:
                    break
            window.append((world.t, list(world.me['pos'][:2])))
            while window and world.t-window[0][0] > 2.1:
                window.pop(0)
            wanted = math.hypot(*action.get('move', [0, 0])) > .1
            stationary = len(window)>1 and world.t-window[0][0] >= 2 and math.dist(window[0][1], world.me['pos'][:2]) < 1
            interval = cfg['match']['decide_every']/cfg['match']['tick_hz']
            stuck_s = stuck_s+interval if wanted and stationary else 0.
            longest_stuck = max(longest_stuck, stuck_s)
            path.append(dict(t=round(world.t, 3), tick=world.tick, x=world.me['pos'][0], y=world.me['pos'][1],
                             z=world.me['pos'][2], hp=world.me['hp'], energy=world.me['energy'],
                             in_zone=world.me['in_zone'], blocked=world.blocked_last, stuck=bool(wanted and stationary),
                             moved=math.dist(before, world.me['pos']), command_move=math.hypot(*action.get('move', [0, 0])),
                             zone_x=world.current_zone['center'][0], zone_y=world.current_zone['center'][1],
                             zone_radius=world.current_zone['radius']))
    except (ValueError, RuntimeError, OSError) as exc:
        errors.append(str(exc))
    finally:
        clean = client.close(dict(type='end', place=1, kills=0))
    stderr = (output/'stderr.log').read_text(encoding='utf-8', errors='replace')
    if 'Traceback (most recent call last)' in stderr:
        errors.append('Bot logged a traceback; inspect stderr.log')
    if not clean:
        errors.append('Bot did not exit cleanly')
    if path:
        with open(output/'path.csv', 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(path[0]))
            writer.writeheader()
            writer.writerows(path)
    ordered = sorted(times)
    summary = dict(seed=seed, layer=args.layer, simulated_seconds=round(world.t, 2),
                   survived_requested_duration=world.tick >= ticks and world.me['hp'] > 0,
                   survived_zone_close=(world.t >= world.zone.close_time and not errors) if args.layer == 'zone' else None,
                   zone_close_s=world.zone.close_time, outcome='harness_error' if errors else 'zone_death' if world.me['hp'] <= 0 else 'time_limit',
                   final_hp=round(world.me['hp'], 2), final_pos=world.me['pos'],
                   movement_goal=world.movement_goal,
                   goal_distance=round(math.dist(world.me['pos'][:2], world.movement_goal), 2) if world.movement_goal else None,
                   zone_damage=round(world.zone_damage, 2), outside_s=round(world.outside_s, 2),
                   blocked_s=round(world.blocked_s, 2), longest_stuck_s=round(longest_stuck, 2),
                   decisions=decisions, missed_deadlines=missed,
                   p99_ms=round(ordered[min(len(ordered)-1, int(len(ordered)*.99))], 2) if ordered else None,
                   max_ms=round(max(times), 2) if times else None, clean_exit=clean, errors=errors,
                   command=args.command, config_sha256=hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(),
                   note='Approximate single-mech diagnostics. No enemies, weapons, loot, score, or official seed equivalence.')
    (output/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layer', choices=['harness', 'movement', 'zone'], default='zone')
    parser.add_argument('--seeds', nargs='+', type=int, default=[1])
    parser.add_argument('--duration', type=float)
    parser.add_argument('--output', type=Path, default=ROOT/'results'/'sim')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.command = args.command[1:] if args.command[:1] == ['--'] else args.command
    args.command = args.command or [sys.executable, '-u', str(ROOT/'bot.py')]
    if args.duration is not None and args.duration <= 0:
        parser.error('--duration must be positive')
    args.output = args.output.resolve()
    with gzip.open(ROOT/'check'/'stream.jsonl.gz', 'rt', encoding='utf-8') as f:
        recording = [json.loads(line) for line in f if line.strip()]
    hello = next(m for m in recording if m['type'] == 'hello')
    template = next(m for m in recording if m['type'] == 'obs')
    hello['map']['dir'] = str(ROOT/'arena')
    schema, arena = Schema(ROOT/'mechfight.schema.json'), Arena(ROOT/'arena')
    summaries = [run(args, seed, hello, template, schema, arena) for seed in args.seeds]
    (args.output/'summary.json').write_text(json.dumps(summaries, indent=2), encoding='utf-8')
    return 1 if any(s['errors'] for s in summaries) else 0


if __name__ == '__main__':
    raise SystemExit(main())
