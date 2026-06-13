# AGENTS.md — working notes for the Gothic 1 Remake save editor

Notes for anyone (human or AI agent) extending this editor. The reverse-engineered
save format is mostly self-describing, **except** for one problem: a *fresh* save
only contains the things the hero already has. You cannot learn what skills/items
exist by scanning one save. So we keep **catalogs** of everything the game defines,
and this file explains them and how to grow them.

> Golden rule: **when you discover something new in a save that the editor can't
> offer yet, add it to the catalog** (and note it here if it's a new pattern).

---

## The save format in one paragraph

A `.sav` is an Oodle/Kraken container (`g1r.py: Container`, `decompress`, `rebuild`).
The decompressed **payload** is GVAS: a tree of tagged properties, each carrying a
byte-size field. Every length-changing edit must update **all enclosing size fields**
and re-validate — `g1r.validate()` is the structural oracle (a valid payload parses
to exactly `len - 4`). Never ship an edit that doesn't re-validate. The generic
length-changing editor is `g1r.apply_ops()` (FString replace / array-element delete);
array-element **insert** is done by hand in `learn_skill` / `add_item`.

---

## Skills catalog — `app/catalog.py`

Skills are `GameplayEffectSpec`s in the hero's `ActiveEffects` array. A learned
skill is one effect whose GE class is `…/GE_Skill_<base>[_<tier>]`. The tier (and
sometimes the whole "is it learned") lives in the **class name**.

`catalog.SKILLS` is the authoritative list: `base → {label, category, kind, …}`.

### `kind` decides how the GE class name is built

| kind       | example base       | learned GE class                  | tiers offered |
|------------|--------------------|-----------------------------------|---------------|
| `ladder`   | `Melee_OneHanded`  | `GE_Skill_Melee_OneHanded_Master` | `ladder` list |
| `circle`   | `Mage_Circle`      | `GE_Skill_Mage_Circle_3`          | Amateur, 1..6 |
| `hunting`  | `Hunting_Teeth`    | `GE_Skill_Hunting_Teeth_Trained`  | on/off (Trained) |
| `binary`   | `Acrobatics`       | `GE_Skill_Acrobatics` (no suffix) | on/off |
| `language` | `Orcish`           | `GE_Skill_Orcish_Untrained`       | on/off |

`catalog.skill_class(base, value)` returns the full class path for any kind — use
it everywhere instead of string-concatenating `base + "_" + tier` (binary skills
have **no** suffix; `language`/`hunting` have fixed suffixes that differ from the UI
value).

### Other fields

- `category` — UI grouping (Combat / Thievery / Hunting / Movement / Crafting / Magic / Language).
- `ladder` — (ladder/circle only) ordered tiers above Untrained.
- `has_untrained` — `True` if an `_Untrained` GE class exists, so lowering to
  Untrained is a **rename**; otherwise "Untrained" means **unlearn/delete**.
- `tier_labels` — *(optional)* `{tier: hint}` extra UI text per tier, e.g.
  `Crafting_Blacksmith` shows Trained → "1H weapons", Master → "2H weapons".

`g1r.py` consumes derived views (`catalog.LABELS / CATEGORY / TIER_LADDER /
HAS_UNTRAINED / LEARNABLE`). `LEARNABLE` is what makes a **fresh hero** show every
skill in the roster, not just the ones already in the save.

### How learning works on a fresh hero (no donor to clone)

`learn_skill()` normally clones an existing learned effect and retargets its class.
With nothing to clone, it falls back to `_learn_from_template()`:

1. **Locate the hero's array even when empty.** `_hero_ae_array()` finds the unique
   `Hero` map-key immediately followed by the `ActiveEffects` ArrayProperty
   (`_HERO_AE` regex). Contents-based detection (`_player_skill_span`, the hunting
   anchor) can't do this when the hero has no skills yet.
2. **Append a captured donor element** (`app/skill_donor.py`, base64) to the array:
   `count++`, grow every enclosing size field, splice the bytes, re-validate.
3. **Retarget** the appended element's GE reference to `catalog.skill_class(...)`.

Caveat (same as the pre-existing clone path): only the GE **class** is retargeted;
the effect's internal tags still read like the donor until the game re-derives them
from the class on load. This is why the feature is labelled *experimental*.

---

## How to extend the skill catalog

### 1. Rescan a save for every skill class it contains

```bash
cd app && python3 -c "import g1r,collections; \
  b=open('../../local-test/G1R-012.payload.bin','rb').read(); \
  c=collections.defaultdict(set); \
  [c[g1r._skill_split(m.group(1).decode())[0]].add(m.group(1).decode()) \
   for m in g1r._SKILL_REF.finditer(b)]; \
  [print(k, sorted(v)) for k,v in sorted(c.items())]"
```

(Point it at a *different* save to discover skills `G1R-012` doesn't have, e.g.
`Crafting_Smithing`, `Diving` — these are intentionally **not** in the catalog yet
because no save we have proves their exact GE class name.)

### 2. Add the entry to `catalog.SKILLS`

Pick the right `kind` from the table above, set `label`/`category`, and `ladder` /
`has_untrained` if relevant. That's it — the roster, dropdowns and class paths all
derive from it. Add a quick check in the verify step below.

### 3. (Rarely) refresh the donor template

`app/skill_donor.py` is one real `ActiveGameplayEffect` element captured from
`G1R-012`. It only needs replacing if the GE struct schema changes (new game
patch). To regenerate:

```bash
cd app && python3 -c "import g1r,base64,textwrap; \
  b=open('../../local-test/G1R-012.payload.bin','rb').read(); \
  d=next(s for s in g1r.list_player_skills(b) if s.get('base')=='Melee_OneHanded' and s['learned']); \
  es,ee,_,_=g1r._find_array_element(b,d['id']); tmpl=b[es:ee]; \
  print('size',len(tmpl)); open('/tmp/donor.b64','w').write(base64.b64encode(tmpl).decode())"
# then paste the base64 into _DONOR_B64 in skill_donor.py
```

The donor must contain exactly **one** `GE_Skill_*` reference (so retargeting is
unambiguous) — the helper above picks `Melee_OneHanded`, which does.

---

## Verifying a catalog change

No save upload needed; the payload is the test fixture.

```bash
cd app && python3 -c "
import g1r, catalog
b=open('../../local-test/G1R-012.payload.bin','rb').read()
# class paths look right for the kind you added:
print(catalog.skill_class('YOUR_BASE', 'YOUR_VALUE'))
# learning it produces a structurally valid payload and is detected:
out=g1r._learn_from_template(b,'YOUR_BASE','YOUR_VALUE')
print('valid', g1r.validate(out))
print([(s['base'],s['tier']) for s in g1r.list_player_skills(out) if s['base']=='YOUR_BASE' and s['learned']])
"
```

Then rebuild the container and smoke-test in the browser:

```bash
docker compose build && docker run -d --name g1r-test -p 5055:5000 gothic-remake-savegame-editor
# open http://localhost:5055 ; docker logs -f g1r-test ; docker rm -f g1r-test when done
```

(Host port 5000 is often taken by macOS Control Center — 5055 avoids it.)

---

## Other catalogs (status)

- **Items** — added by name/key from the **save's own** item database
  (`g1r.list_item_catalog` / `add_item`, anchored on `m_ItemDefinition`). Items are
  discoverable from the save, so there is no static item catalog *yet*. If we ever
  want to add items a fresh save has never seen, mirror the skill approach here:
  a static `catalog.ITEMS` + a donor `ItemSlot` template + the inventory anchor.
- **Story flags** — `add_passage` / story-flag listing. Same note: currently
  discovered from the save; promote to a static catalog only if fresh-save coverage
  is needed.

When you build either of those, document the schema and the discover→add workflow
here next to the skills section.
