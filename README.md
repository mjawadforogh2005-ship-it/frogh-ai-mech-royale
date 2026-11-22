# Frogh AI Lab — Mech Royale

Python bot for the Builders Mech Royale competition. The custom bot is `bot.py`; the unchanged official `example_bot.py` remains available for comparison. No third-party runtime dependencies or network access are required.

## Strategy

- Prioritize the current safe zone and start moving toward the next circle before travel time becomes critical.
- Retreat when health is low or nearby opponents outnumber us; stop attacking at a distance to allow regeneration.
- Use the supplied terrain and building polygons for ground clearance, local steering, and firing-path checks. Avoid steep steps and large drops, and attempt recovery when stuck.
- Lead gun targets using projectile interception. Gate shots on actual torso aim; handle laser charge/release, lightning energy reserves, and low-arc mortar shots separately.
- Collect reachable cubes and weapons, approach nearby uncontested supply drops, and prioritize defensive upgrades.
- React to incoming straight-line projectiles and preserve energy for escapes.

These are deterministic heuristics, not a trained model. Navigation uses local steering rather than a complete route planner; complex courtyards, rooftop travel, and prolonged obstacle avoidance remain limitations. Map and projectile checks approximate the server physics. Balance is read from the match configuration and current upgraded stats. Practice-match performance has not yet been measured.

## Local check

Python 3.12 works for the supplied baseline. The supplied Dockerfile uses Python 3.13. No third-party Python packages are needed.

From this directory:

```sh
python -m unittest discover -s tests -v
python check/check.py -- python -u bot.py
```

The checker replays 300 recorded observations. It verifies protocol behavior and response timing; it is not a simulator and cannot measure playing strength.

## Container check

Install/start Docker with Linux containers before running these commands. In PowerShell:

```powershell
docker build --platform linux/amd64 -t frogh-ai-mech-royale .
python check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 --cap-drop ALL --pids-limit 128 --mount "type=bind,source=$($PWD.Path)/arena,target=/arena,readonly" frogh-ai-mech-royale
```

The arena is mounted at runtime and excluded from the image build context.

## Files

- `bot.py`: custom Frogh AI Lab strategy and current entrypoint.
- `navigation.py`: terrain, polygon collision, firing paths, and local steering.
- `tests/`: focused strategy and geometry regression tests.
- `example_bot.py`: unchanged official baseline.
- `mechfight.py`: official typed protocol wrapper.
- `Dockerfile`: official Python image configuration at repository root.
- `protocol.md` and `mechfight.schema.json`: supplied specification and schema.
- `check/`: supplied conformance checker and observation recording.
- `arena/`: supplied map for local checks.

## Development plan

1. Check protocol compliance and regression tests after meaningful changes.
2. Confirm the GitHub Actions Linux container check passes for the exact commit.
3. When ready to submit, evaluate the strategy using actual practice matches.
4. Use match outcomes to improve route planning, threat assessment, and weapon selection.

According to the supplied protocol, submission requires a public GitHub repository, a top-level Dockerfile, a submitted repository no larger than 20 MB, and a linux/amd64 build finishing within ten minutes. Each submission runs 48 practice matches. There is no network during matches; decisions have a 50 ms budget, one CPU, and 512 MB RAM. Placement determines points, so survival is the primary objective.

Competition deadline, participant eligibility, AI-assistance rules, redistribution terms, and certificate availability are not established by this kit. Verify those with the organizer before publishing or submitting.

## Validation — 2026-11-22

The reviewed custom bot passed all 22 regression tests and answered 300/300 recorded observations on time with no wrong ticks, bad lines, or debug-cap violations. Its latest local protocol run measured p99 36.2 ms and maximum 41.9 ms against the 50 ms budget. A separate 100-decision synthetic combat workload with four moving enemies and both gun and mortar available measured a maximum of 36.68 ms, with no decisions over 50 ms. These timings are machine-specific, not measurements under the match CPU limit or evidence of winning performance. Direct strategy execution on the recording also checks for exceptions that the protocol wrapper might mask.

The review corrected cooldown aim tracking, premature combat reloads, charged-laser priority, walking on roof and bridge surfaces, and arrival precision in tiny zones. Regression tests cover these cases. Navigation still uses local steering, and complex multi-building routes need practice-match evaluation.

The earlier commit `a61f9cb` passed GitHub's Linux container checks. The reviewed revision needs its own green Actions run after pushing. Local container validation is unavailable because Docker was not found on PATH. No competition submission has been made through this project workflow.

GitHub Actions runs the Python checker and a sandboxed Linux container check on pushes and pull requests. Check the Actions result before treating container validation as passed.
