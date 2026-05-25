# TODO

Use this file to capture features, bugs, and decisions you want to come back to. Keep it short. Move durable knowledge into actual notes under `knowledge/`.

## Features

- [ ] _(your wishlist)_

## Bugs

_(none yet)_

## Decisions to revisit

- Whether to keep `archive/processed/` under git or git-lfs once it grows past a few hundred MB.
- Whether to commit `.obsidian/workspace.json`. Currently ignored, since it's per-machine state.

## Manual review

Append one line per file that ingestion couldn't extract, in the format:

```
- archive/failed/<rel/path> — <one-line reason>
```
