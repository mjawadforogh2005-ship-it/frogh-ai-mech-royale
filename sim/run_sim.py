"""Usage: python sim/run_sim.py [--n 10] [--enemy idle|runner|wander] [--seed0 1] [--bot bot|example]"""
import argparse, os, sys, statistics, time, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim import Sim, ROOT
sys.path.insert(0, ROOT)

def factory(name):
    if name == 'bot':
        import bot; return bot.Bot
    import example_bot; return example_bot.Bot

ap = argparse.ArgumentParser()
ap.add_argument('--n', type=int, default=5); ap.add_argument('--seed0', type=int, default=1)
ap.add_argument('--enemy', default='runner'); ap.add_argument('--bot', default='bot')
ap.add_argument('--max-s', type=float, default=480)
a = ap.parse_args()
F = factory(a.bot); scores = []; causes = collections.Counter()
for i in range(a.n):
    t0 = time.time(); r = Sim(a.seed0+i, a.enemy, F, max_s=a.max_s).run()
    scores.append(r['score']); causes[r['cause'].split('(')[0]] += 1
    print(f"seed {a.seed0+i}: place {r['place']:>2} score {r['score']:>2} t={r['t']:>5}s cause={r['cause']:<14} moved={r['dist_moved']}m end={r['end_pos']} hp={r['hp']} slow={r['slow']} ({time.time()-t0:.0f}s wall)")
print(f"\nMEAN SCORE {statistics.mean(scores):.2f} over {len(scores)} matches | causes: {dict(causes)}")
