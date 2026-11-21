# Frogh AI Lab — Mech Royale

Python bot project for the Builders Mech Royale competition. This initial version is the unchanged official Python example, not a competitive strategy yet.

## Local check

Python 3.12 works for the supplied baseline. The supplied Dockerfile uses Python 3.13. No third-party Python packages are needed.

From this directory:

```sh
python check/check.py -- python -u example_bot.py
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

- `example_bot.py`: unchanged official baseline; current entrypoint.
- `mechfight.py`: official typed protocol wrapper.
- `Dockerfile`: official Python image configuration at repository root.
- `protocol.md` and `mechfight.schema.json`: supplied specification and schema.
- `check/`: supplied conformance checker and observation recording.
- `arena/`: supplied map for local checks.

## Development plan

1. Preserve the passing baseline in Git.
2. Implement zone-aware survival and navigation, then aiming and resource collection.
3. Check protocol compliance after meaningful changes.
4. Validate the Linux container under match restrictions.
5. Publish and submit only when ready; evaluate strategy using actual practice matches.

According to the supplied protocol, submission requires a public GitHub repository, a top-level Dockerfile, a submitted repository no larger than 20 MB, and a linux/amd64 build finishing within ten minutes. Each submission runs 48 practice matches. There is no network during matches; decisions have a 50 ms budget, one CPU, and 512 MB RAM. Placement determines points, so survival is the primary objective.

Competition deadline, participant eligibility, AI-assistance rules, redistribution terms, and certificate availability are not established by this kit. Verify those with the organizer before publishing or submitting.

## Setup validation — 2026-11-21

The unchanged example passed the official checker locally on Windows with Python 3.12: 300/300 observations on time, no wrong ticks or bad lines, and no debug-cap violations. Local container validation is pending because Docker was not found on PATH. No competition submission has been created.

GitHub Actions runs the Python checker and a sandboxed Linux container check on pushes and pull requests. Check the Actions result before treating container validation as passed.
