# Gothic 1 Remake — Quest Fixer

A tiny **local** web app to fix stuck/bugged quests in *Gothic 1 Remake* saves.
Upload your `.sav`, search for the quest, set its state, download a ready-to-load
save. Everything runs on your own machine in one container — nothing is uploaded
anywhere.

[![build](https://github.com/Xetoxyc/gothic-remake-quest-fixer/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Xetoxyc/gothic-remake-quest-fixer/actions/workflows/docker-publish.yml)
[![ghcr](https://img.shields.io/badge/ghcr.io-gothic--remake--quest--fixer-2496ed?logo=docker&logoColor=white)](https://github.com/Xetoxyc/gothic-remake-quest-fixer/pkgs/container/gothic-remake-quest-fixer)
![flow](https://img.shields.io/badge/upload-→_search_→_recompile-c8862a)

## Quick start

### Run the published image (no build)

```bash
docker run --rm -p 3000:3000 ghcr.io/xetoxyc/gothic-remake-quest-fixer:latest
```

### …or build it yourself

```bash
docker compose up --build
```

Either way, open **http://localhost:3000**.

> Prefer plain `docker` for a local build?
> ```bash
> docker build -t gothic-remake-quest-fixer .
> docker run --rm -p 3000:3000 gothic-remake-quest-fixer
> ```

On Apple Silicon / ARM the image runs under emulation automatically (it's amd64
because the Oodle library is x86_64) — it just works, only a little slower.

## How to use

1. **Upload** your save (e.g. `G1R-012.sav`, usually under
   `…\Saved\SaveGames\`).
2. **Search** the quest list (try `trialoffire`, `waterfall`, a quest name…).
   Each row shows its current state.
3. Pick a new **state** (`None`, `Available`, `Running`, `Succeeded`, `Failed`)
   from the dropdown. Change as many as you like.
4. Click **Generate fixed save** → you get `<name>.fixed.sav`.
5. **Back up your original**, then rename the `.fixed.sav` over it and load.

The classic case: the "Trial of Fire" objectives `OBJ_WATERFALL` / `OBJ_SEA` get
stuck on `Running` if you light the shrine early — set them to `Succeeded`.

## How it works

The save is a custom GVAS container: a small header + game state stored as
**Oodle(Kraken)-compressed 128 KiB chunks**. The app:

1. decompresses every chunk with real Oodle,
2. lists every objective's `EQuestState` (`CurrentState` enum),
3. on edit, rewrites the enum string and bumps **every enclosing container
   size field** plus the two decompressed-size header copies,
4. recompresses with real Oodle Kraken and rewrites the four header fields
   (`data-end @5`, both `total_unc`, `total_comp`).

Every generated save is structurally re-validated (it must parse cleanly end to
end) before download, so a bad edit is refused rather than written.

## Oodle library

Compression uses **Oodle**, which ships with Unreal Engine / the game. This image
**does not bundle it** — `entrypoint.sh` downloads `liboo2corelinux64.so.9` on
first start (override the source with `OODLE_URL`, or mount your own at
`/app/liboo2corelinux64.so.9`; see the commented block in `docker-compose.yml`).

## Safety

- Always keep a **backup** of your original save.
- Editing the save has **no known effect on achievements**: the savegame stores
  no achievement state and no cheat/tamper flag, and it isn't integrity-checked.
  Only the **in-game console** is known to disable achievements (it flags the
  live session) — a path this tool never touches. Use at your own risk.
- This is a community tool, not affiliated with Alkimia Interactive / THQ Nordic.

## Continuous build

Every push to `main` (and every `v*` tag) builds the image and publishes it to
**GHCR** via [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml):
`ghcr.io/xetoxyc/gothic-remake-quest-fixer:latest` (plus `:vX.Y.Z` and `:sha-…`).
No secrets to configure — it uses the built-in `GITHUB_TOKEN`. After the first
successful run, set the package visibility to **Public** in the repo's
*Packages* settings so others can `docker run` it.

## Credits

Container format & the decompress/recompress approach are based on
[wealth's gist](https://gist.github.com/wealth/de5a461e02ab49060d5f418a520ee1e8).

## API (if you want to script it)

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/api/load` | multipart `save=@file.sav` | `{token, quests:[{id,key,name,state}], states}` |
| `POST` | `/api/patch` | `{token, filename, changes:[{id,new_state}]}` | the fixed `.sav` (binary) |
| `GET`  | `/api/health` | — | `{ok, oodle}` |
