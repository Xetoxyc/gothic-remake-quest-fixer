"""Authoritative catalog of editable Gothic 1 Remake save entities.

WHY THIS FILE EXISTS
--------------------
A *fresh* savegame only contains the handful of skills the hero has already
learned, so the editor cannot discover the rest by scanning the save. This
catalog records everything the game defines, letting the editor offer (and
synthesize) any skill even on an empty hero. When you find something new in a
save that isn't here yet, ADD IT HERE -- see AGENTS.md for the workflow.

SOURCE OF TRUTH
---------------
The `GE_Skill_*` GameplayEffect classes observed in real savegames
(local-test/*.payload.bin). To rescan a save for new entries:

    python3 -c "import g1r,collections; b=open('PAYLOAD','rb').read(); \
        c=collections.defaultdict(set); \
        [c[g1r._skill_split(m.group(1).decode())[0]].add(m.group(1).decode()) \
         for m in g1r._SKILL_REF.finditer(b)]; \
        [print(k, sorted(v)) for k,v in sorted(c.items())]"
"""

GE_PREFIX = "/Script/Angelscript.Default__GE_Skill_"

# Skill "kinds" decide how the GE class name is built and how tiers are offered:
#   ladder : ranked; class = GE_Skill_<base>_<tier> (e.g. Melee_OneHanded_Master)
#   circle : Mage_Circle ladder (Amateur, 1..6); class = GE_Skill_Mage_Circle_<t>
#   hunting: single learned state "Trained"; class = GE_Skill_<base>_Trained
#   binary : on/off skill with NO tier suffix; class = GE_Skill_<base>
#   language: Orcish; learned class = GE_Skill_<base>_Untrained (its only form)
#
# Fields per skill:
#   label        human-readable name
#   category     UI grouping (Combat/Thievery/Hunting/Movement/Crafting/Magic/Language)
#   kind         one of the kinds above
#   ladder       (ladder/circle only) ordered learnable tiers above Untrained
#   has_untrained True if an _Untrained GE class exists, so lowering to Untrained
#                is a rename rather than an unlearn/delete.
#   tier_labels  (optional) {tier: hint} extra text shown next to a tier in the UI
#                (e.g. Blacksmithing Trained -> "1H weapons").
SKILLS = {
    # ---- Combat (ranked) -------------------------------------------------
    "Melee_OneHanded": dict(label="One-Handed", category="Combat", kind="ladder",
                            ladder=["Trained", "Master"], has_untrained=False),
    "Melee_TwoHanded": dict(label="Two-Handed", category="Combat", kind="ladder",
                            ladder=["Trained", "Master"], has_untrained=False),
    "Melee_Fists":     dict(label="Fists", category="Combat", kind="ladder",
                            ladder=["Trained", "Master"], has_untrained=False),
    "Ranged_Bow":      dict(label="Bow", category="Combat", kind="ladder",
                            ladder=["Trained", "Master"], has_untrained=True),
    "Ranged_Crossbow": dict(label="Crossbow", category="Combat", kind="ladder",
                            ladder=["Trained", "Master"], has_untrained=True),
    # ---- Thievery (ranked) ----------------------------------------------
    "Picklock":   dict(label="Lockpicking", category="Thievery", kind="ladder",
                       ladder=["Skilled", "Master"], has_untrained=True),
    "Pickpocket": dict(label="Pickpocketing", category="Thievery", kind="ladder",
                       ladder=["Skilled", "Master"], has_untrained=True),
    # ---- Hunting (single "Trained" state) -------------------------------
    "Hunting_Organ":          dict(label="Take Organs", category="Hunting", kind="hunting"),
    "Hunting_Teeth":          dict(label="Break Teeth", category="Hunting", kind="hunting"),
    "Hunting_Claw":           dict(label="Take Claws", category="Hunting", kind="hunting"),
    "Hunting_Fur":            dict(label="Skin Fur", category="Hunting", kind="hunting"),
    "Hunting_Skin":           dict(label="Skin", category="Hunting", kind="hunting"),
    "Hunting_Fins":           dict(label="Take Fins", category="Hunting", kind="hunting"),
    "Hunting_Stings":         dict(label="Take Stingers", category="Hunting", kind="hunting"),
    "Hunting_Secretion":      dict(label="Take Secretion", category="Hunting", kind="hunting"),
    "Hunting_SkullArmor":     dict(label="Take Skull Plates", category="Hunting", kind="hunting"),
    "Hunting_SkinSwampshark": dict(label="Skin Swampshark", category="Hunting", kind="hunting"),
    # ---- Movement / utility (binary) ------------------------------------
    "Acrobatics":   dict(label="Acrobatics", category="Movement", kind="binary"),
    "Wallclimbing": dict(label="Wall Climbing", category="Movement", kind="binary"),
    "Riding":       dict(label="Riding", category="Movement", kind="binary"),
    "Sneak":        dict(label="Sneaking", category="Movement", kind="binary"),
    # ---- Crafting -------------------------------------------------------
    "Crafting_Alchemy":     dict(label="Alchemy", category="Crafting", kind="binary"),
    "Crafting_Inscription": dict(label="Rune Inscription", category="Crafting", kind="binary"),
    # Blacksmithing is ranked: Trained (II) forges 1H weapons, Master (III) forges 2H.
    "Crafting_Blacksmith":  dict(label="Blacksmithing", category="Crafting", kind="ladder",
                                 ladder=["Trained", "Master"], has_untrained=False,
                                 tier_labels={"Trained": "1H weapons", "Master": "2H weapons"}),
    # ---- Magic ----------------------------------------------------------
    "Mage_Circle": dict(label="Magic Circle", category="Magic", kind="circle",
                        ladder=["Amateur", "1", "2", "3", "4", "5", "6"],
                        has_untrained=False),
    # ---- Language -------------------------------------------------------
    "Orcish": dict(label="Orcish Language", category="Language", kind="language"),
}

# The UI "value" that represents the freshly-learned state for non-ladder skills,
# and the GE class suffix that value maps to. (Decoupled because Orcish is learned
# as the "_Untrained" class but we don't want to label it "Untrained" in the UI.)
_LEARN_VALUE = {"hunting": "Trained", "binary": "Learned", "language": "Learned"}
_LEARN_SUFFIX = {"hunting": "Trained", "binary": "", "language": "Untrained"}


def skill_class(base, value):
    """Full GE class path for a (base, chosen-value). For ladder/circle skills the
    value IS the tier suffix; for hunting/binary/language the suffix is fixed."""
    s = SKILLS.get(base)
    kind = s["kind"] if s else "ladder"
    if kind in ("ladder", "circle"):
        suffix = value
    else:
        suffix = _LEARN_SUFFIX[kind]
    return GE_PREFIX + base + (("_" + suffix) if suffix else "")


def learn_value(base):
    """The UI value that means 'learn this' for a non-ladder skill."""
    return _LEARN_VALUE[SKILLS[base]["kind"]]


# ---- derived convenience maps consumed by g1r.py ------------------------
LABELS = {b: s["label"] for b, s in SKILLS.items()}
CATEGORY = {b: s["category"] for b, s in SKILLS.items()}
TIER_LADDER = {b: s["ladder"] for b, s in SKILLS.items() if "ladder" in s}
HAS_UNTRAINED = {b for b, s in SKILLS.items() if s.get("has_untrained")}
# every learnable base, so a fresh hero can be offered all of them
LEARNABLE = list(SKILLS.keys())
