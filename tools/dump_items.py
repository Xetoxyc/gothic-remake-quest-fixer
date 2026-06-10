#!/usr/bin/env python3
"""Build an item DB from a decompressed G1R payload: every distinct item class
that appears as an m_ItemDefinition anywhere in the save -> items.json.

Usage:  python dump_items.py G1R-012.payload.bin [items.json]
(payload = the decompressed blob; e.g. produced by decompress.py)
"""
import json
import re
import struct
import sys

_DEF = re.compile(rb"m_ItemDefinition\x00\x0f\x00\x00\x00ObjectProperty\x00")
_CATS = {
    "ItMi": "Misc", "ItFo": "Food / Potion", "ItMw": "Melee Weapon",
    "ItRw": "Ranged Weapon", "ItAm": "Ammo", "ItAr": "Armor",
    "ItAt": "Amulet / Trophy", "ItKe": "Key / Lockpick", "ItWr": "Written",
    "ItMs": "Misc Stack", "ItAI": "AI / Special",
}


def label(item):
    s = re.sub(r"^It[A-Z][a-z]_", "", item)
    s = re.sub(r"^(1H|2H)_", "", s)
    s = re.sub(r"_\d+$", "", s)
    return s.replace("_", " ").strip() or item


def category(item):
    return _CATS.get(item[:4], "Other") if item.startswith("It") else "Trophy / Other"


def build(payload):
    items = set()
    for m in _DEF.finditer(payload):
        vo = m.end() + 9
        n = struct.unpack_from("<i", payload, vo)[0]
        if 0 < n < 200:
            items.add(payload[vo + 4:vo + 4 + n - 1].decode("utf-8", "replace").split(".")[-1])
    return [{"id": it, "label": label(it), "category": category(it)} for it in sorted(items)]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    payload = open(sys.argv[1], "rb").read()
    db = build(payload)
    out = sys.argv[2] if len(sys.argv) > 2 else "items.json"
    json.dump(db, open(out, "w"), indent=1)
    from collections import Counter
    print(f"{len(db)} items -> {out}")
    for cat, n in Counter(i["category"] for i in db).most_common():
        print(f"  {n:4}  {cat}")
