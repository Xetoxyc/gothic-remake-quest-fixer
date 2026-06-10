# Gothic 1 Remake — Savegame Editor

A **local** web app to edit *Gothic 1 Remake* saves: character stats, skills,
inventory, quest states and the bestiary/locations glossary. Upload your `.sav`,
make changes across tabs, download a ready-to-load save. Everything runs on your
own machine in one container — **nothing is uploaded anywhere**.

[![release](https://github.com/Xetoxyc/gothic-remake-savegame-editor/actions/workflows/release.yml/badge.svg)](https://github.com/Xetoxyc/gothic-remake-savegame-editor/actions/workflows/release.yml)
[![ghcr](https://img.shields.io/badge/ghcr.io-gothic--remake--savegame--editor-2496ed?logo=docker&logoColor=white)](https://github.com/Xetoxyc/gothic-remake-savegame-editor/pkgs/container/gothic-remake-savegame-editor)
![flow](https://img.shields.io/badge/upload-→_edit-→_download-c8862a)

## What you can edit

- **Character** — Strength, Dexterity, Health, Mana, Level, Experience, Learning
  Points, Toughness, … (a value sets both its base and current).
- **Skills** — weapon & thievery tiers (I / II / III), Magic Circle (0–6), hunting
  & crafting & movement perks; unlearn, and *experimentally* learn skills you
  don't have yet.
- **Inventory** — change any item's amount, or **add new items** by name/key from
  the save's own item database.
- **Quests** — set any objective's state (`Available` / `Running` / `Succeeded` …);
  the classic fix is the stuck *Trial of Fire* `OBJ_WATERFALL` / `OBJ_SEA`.
- **Glossary** — creatures & locations bestiary, with the in-game unlock/entry
  dependencies modelled (overviews update automatically).

## Quick start

### Run the published image (no build)

```bash
docker run --rm -p 5000:5000 ghcr.io/xetoxyc/gothic-remake-savegame-editor:latest
```

### …or build it yourself

```bash
docker compose up --build
```

Either way, open **http://localhost:5000**.

### …or run it on GitHub (no install)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Xetoxyc/gothic-remake-savegame-editor)

Click the badge → *Create codespace*. It builds the container, starts the app,
and forwards **port 5000** (a browser preview opens automatically). It's your own
private instance — your save stays in your codespace, not on a shared server.
Codespaces runs on amd64, so Oodle runs natively (no emulation).

> Prefer plain `docker` for a local build?
> ```bash
> docker build -t gothic-remake-savegame-editor .
> docker run --rm -p 5000:5000 gothic-remake-savegame-editor
> ```

On Apple Silicon / ARM the image runs under emulation automatically (it's amd64
because the Oodle library is x86_64) — it just works, only a little slower.

## How to use

1. **Upload** your save (e.g. `G1R-012.sav`, usually under
   `…\Saved\SaveGames\`).
2. Use the **tabs** (Character · Skills · Inventory · Quests · Glossary) to make
   changes. Search where lists are long; only what you change is applied.
3. Click **Generate fixed save** → you get `<name>.fixed.sav`.
4. **Back up your original**, then rename the `.fixed.sav` over it and load.

> The classic quest fix: the *Trial of Fire* objectives `OBJ_WATERFALL` /
> `OBJ_SEA` get stuck on `Running` if you light the shrine early — set them to
> `Succeeded` in the Quests tab.

## How it works

The save is a custom GVAS container: a small header + game state stored as
**Oodle(Kraken)-compressed 128 KiB chunks**. The app:

1. decompresses every chunk with real Oodle,
2. locates the hero's data (attributes, the skill effect-spec array, the
   inventory slot array) and the quest/glossary objective states,
3. applies edits — **length-neutral** for attribute & item-count changes (an
   in-place number write); **length-changing** for quest/skill/glossary state
   strings and for learning skills / adding items, where the value is rewritten
   and **every enclosing container size field** (plus the two decompressed-size
   header copies) is adjusted,
4. recompresses with real Oodle Kraken and rewrites the four header fields
   (`data-end @5`, both `total_unc`, `total_comp`).

Every generated save is structurally **re-validated** (it must parse cleanly end
to end) before download, so a bad edit is refused rather than written. Learning
skills / adding items clone an existing same-family entry and retarget it —
marked *experimental* in the UI since gameplay correctness depends on the game
re-deriving from the class on load.

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

## Releases &amp; CI

Versioning is automatic via [release-please](https://github.com/googleapis/release-please)
and [Conventional Commits](https://www.conventionalcommits.org/) — you never tag
by hand:

| Commit prefix | Bump | Example |
|---|---|---|
| `fix:` | patch | `fix: handle saves with no quests` |
| `feat:` | minor | `feat: batch-edit multiple saves` |
| `feat!:` / `BREAKING CHANGE:` | major | `feat!: change the API response shape` |

The flow ([`.github/workflows/release.yml`](.github/workflows/release.yml)):

1. You push Conventional Commits to `main`.
2. **release-please** keeps an open *“release PR”* with the computed version bump
   and a generated `CHANGELOG.md`.
3. Merging that PR tags `vX.Y.Z` and cuts a GitHub Release.
4. The same workflow builds and pushes the image to **GHCR**:
   - every push to `main` → `:edge` and `:sha-…`
   - a release → also `:latest` and `:X.Y.Z`

`ci.yml` builds the image on every PR (without pushing) so broken Dockerfiles are
caught early. Everything uses the built-in `GITHUB_TOKEN` — no secrets to set up.

> **One-time:** after the first image is published, set the GHCR package
> visibility to **Public** (repo → *Packages* → package → *Settings*) so others
> can `docker run` it without logging in.

## Credits

Container format & the decompress/recompress approach are based on
[wealth's gist](https://gist.github.com/wealth/de5a461e02ab49060d5f418a520ee1e8).

## License

[MIT](LICENSE) © Tobias Sittenauer

## API (if you want to script it)

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/api/load` | multipart `save=@file.sav` | `{token, states, attributes, skills, inventory, item_db, quests}` |
| `POST` | `/api/patch` | `{token, filename, attr_changes, inv_changes, inv_adds, skill_changes, quest_changes}` | the fixed `.sav` (binary) |
| `GET`  | `/api/health` | — | `{ok, oodle}` |

All `*_changes` arrays are optional — send only what you edit.
