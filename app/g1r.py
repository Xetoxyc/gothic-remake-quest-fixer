"""
Core logic for the Gothic 1 Remake savegame editor.

Parses the custom GSAV/Oodle container, decompresses with real Oodle, lists every
quest objective and its EQuestState, applies state changes (updating every
enclosing container size field), and recompresses with real Oodle Kraken.

Reverse-engineering credit: the container format + the recompress trick come
from wealth's gist (gist.github.com/wealth/de5a461e02ab49060d5f418a520ee1e8).
"""
import ctypes
import struct

import catalog

CHUNK = 0x20000
MAGIC = b"\xc1\x83\x2a\x9e"
KRAKEN = 8
LEVEL_NORMAL = 4
EQUEST_STATES = ["None", "Available", "Running", "Succeeded", "Failed"]
ENUM_PREFIX = "EQuestState::"


# --------------------------------------------------------------------------- Oodle
class Oodle:
    def __init__(self, path):
        self.lib = ctypes.CDLL(path)
        d = self.lib.OodleLZ_Decompress
        d.restype = ctypes.c_ssize_t
        d.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p, ctypes.c_ssize_t,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
        c = self.lib.OodleLZ_Compress
        c.restype = ctypes.c_ssize_t
        c.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p, ctypes.c_int,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]

    def decompress(self, comp, raw_len):
        out = ctypes.create_string_buffer(raw_len + 64)
        n = self.lib.OodleLZ_Decompress(bytes(comp), len(comp), out, raw_len,
                                        1, 0, 0, None, 0, None, None, None, 0, 3)
        if n != raw_len:
            raise RuntimeError(f"OodleLZ_Decompress returned {n}, expected {raw_len}")
        return out.raw[:raw_len]

    def compress(self, raw):
        cap = len(raw) + 0x10000
        out = ctypes.create_string_buffer(cap)
        n = self.lib.OodleLZ_Compress(KRAKEN, bytes(raw), len(raw), out, LEVEL_NORMAL,
                                      None, None, None, None, 0)
        if n <= 0:
            raise RuntimeError("OodleLZ_Compress failed")
        return out.raw[:n]


# ----------------------------------------------------------------------- container
class Container:
    def __init__(self, data):
        self.data = data
        d = data
        self.oo_str = d.find(b"Oodle")
        self.oo_magic = d.find(MAGIC)
        if self.oo_str < 0 or self.oo_magic != self.oo_str + 6:
            raise ValueError("not a recognized Gothic 1 Remake save (GSAV/Oodle header missing)")
        self.total = struct.unpack_from("<q", d, self.oo_str - 12)[0]
        copy2 = d.find(struct.pack("<q", self.total), self.oo_magic)
        if copy2 < 0:
            raise ValueError("save header inconsistent")
        self.rec0 = copy2 + 8
        n_full, rem = divmod(self.total, CHUNK)
        self.n_chunks = n_full + (1 if rem else 0)
        self.recs = []
        p = self.rec0
        for _ in range(self.n_chunks):
            self.recs.append((struct.unpack_from("<q", d, p)[0],
                              struct.unpack_from("<q", d, p + 8)[0]))
            p += 16
        self.data_start = p
        self.data_end = self.data_start + sum(c for c, _ in self.recs)
        self.trailer = d[self.data_end:]


def decompress_payload(container, oodle):
    out = bytearray()
    o = container.data_start
    for clen, ulen in container.recs:
        out += oodle.decompress(container.data[o:o + clen], ulen)
        o += clen
    if len(out) != container.total:
        raise RuntimeError("decompressed size mismatch")
    return bytes(out)


def rebuild(container, oodle, new_payload):
    """Recompress every chunk (real Kraken) and rewrite the 4 header size fields."""
    d = container.data
    new_total = len(new_payload)
    out_data = bytearray()
    new_recs = []
    off = 0
    while off < new_total:
        u = min(CHUNK, new_total - off)
        comp = oodle.compress(new_payload[off:off + u])
        if len(comp) >= u:
            raise RuntimeError("chunk failed to compress")
        new_recs.append((len(comp), u))
        out_data += comp
        off += u
    sumcomp = len(out_data)

    head = bytearray(d[:container.rec0])
    table = b"".join(struct.pack("<qq", c, u) for c, u in new_recs)
    out_data_off = container.rec0 + len(table)
    struct.pack_into("<I", head, 5, out_data_off + sumcomp)          # data-end offset
    struct.pack_into("<q", head, container.oo_str - 12, new_total)   # total_unc copy 1
    struct.pack_into("<q", head, container.rec0 - 8, new_total)      # total_unc copy 2
    struct.pack_into("<q", head, container.rec0 - 16, sumcomp)       # total compressed
    return bytes(head) + table + bytes(out_data) + bytes(container.trailer)


# ------------------------------------------------------------- structural reader
def _i32(b, o):
    return struct.unpack_from("<i", b, o)[0]


def _fstr_end(b, o):
    n = _i32(b, o)
    if n == 0:
        return o + 4
    if n > 0:
        return o + 4 + n
    return o + 4 + (-n) * 2


def _fstr(b, o):
    n = _i32(b, o)
    if n == 0:
        return "", o + 4
    if n > 0:
        return b[o + 4:o + 4 + n].rstrip(b"\x00").decode("utf-8", "replace"), o + 4 + n
    n = -n
    return b[o + 4:o + 4 + 2 * n][:-2].decode("utf-16-le", "replace"), o + 4 + 2 * n


def _typename(b, o):
    name, o = _fstr(b, o)
    nparam = _i32(b, o); o += 4
    for _ in range(nparam):
        _, o = _typename(b, o)
    return name, o


SCALARS = {"IntProperty", "Int64Property", "Int32Property", "UInt32Property",
           "UInt64Property", "Int16Property", "UInt16Property", "Int8Property",
           "ByteProperty", "FloatProperty", "DoubleProperty", "StrProperty",
           "NameProperty", "ObjectProperty", "SoftObjectProperty", "EnumProperty",
           "TextProperty"}
WIDTH = {"IntProperty": 4, "Int32Property": 4, "UInt32Property": 4, "FloatProperty": 4,
         "Int64Property": 8, "UInt64Property": 8, "DoubleProperty": 8,
         "Int16Property": 2, "UInt16Property": 2, "Int8Property": 1, "ByteProperty": 1,
         "BoolProperty": 1}


def _value_end(b, o, root):
    """Given offset o at the i32 size field, return (vstart, vend, size)."""
    size = _i32(b, o)
    body = o + 4
    if root == "BoolProperty":
        return body, body + 1, size      # size(i32)=0 then a single value byte (no hasGuid)
    if root in SCALARS:
        return body + 1, body + 1 + size, size
    if root == "StructProperty":
        return body + 1, body + 1 + size, size
    if root in ("MapProperty", "ArrayProperty", "SetProperty"):
        return body + 1, body + 1 + size, size
    raise ValueError(f"unknown root {root!r}")


def walk_body(b, o, end):
    """Skip-parse a tagged-property body until None or end; return offset past None."""
    while o < end:
        name, o2 = _fstr(b, o)
        if name == "None" or name == "":
            return o2
        root, o3 = _typename(b, o2)
        _, vend, _sz = _value_end(b, o3, root)
        o = vend
    return o


def validate(payload):
    """The structural oracle: a valid payload parses to exactly its end - footer."""
    _, o = _fstr(payload, 0)
    o += 1
    end = walk_body(payload, o, len(payload))
    return len(payload) - end == 4


# -------------------------------------------------------------- quest listing
def list_quests(payload):
    """Find every objective CurrentState whose value is an EQuestState. Returns a
    list of dicts: {key, name, state, val_off, size_off}."""
    needle = b"\x0d\x00\x00\x00CurrentState\x00"   # i32 len 13 + 'CurrentState\0'
    quests = []
    pos = 0
    while True:
        m = payload.find(needle, pos)
        if m < 0:
            break
        pos = m + 1
        # parse the CurrentState EnumProperty tag starting at the name length-prefix m
        try:
            size_off, val_off = _parse_prop(payload, m)
            val, _ = _fstr(payload, val_off)
        except Exception:
            continue
        if not val.startswith(ENUM_PREFIX):
            continue
        state = val[len(ENUM_PREFIX):]
        key = _key_before(payload, m)
        quests.append({"key": key or "(unknown)", "name": _pretty(key),
                       "state": state, "val_off": val_off, "size_off": size_off})
    return quests


def _parse_prop(b, p0):
    """For tagged EnumProperty/MapProperty etc.; returns (size_off, val_off)."""
    p = _fstr_end(b, p0)              # name
    tn = _i32(b, p)
    tt = b[p + 4:p + 4 + max(tn - 1, 0)]
    p = _fstr_end(b, p)              # type
    if tt == b"EnumProperty":
        p += 4; p = _fstr_end(b, p); p += 4; p = _fstr_end(b, p); p += 4; p = _fstr_end(b, p)
    elif tt == b"MapProperty":
        p += 4; p = _skip_typedesc(b, p); p += 4; p = _skip_typedesc(b, p)
    elif tt == b"StructProperty":
        p += 4; p = _fstr_end(b, p); p += 4; p = _fstr_end(b, p)
    elif tt == b"ArrayProperty":
        p += 4; p = _skip_typedesc(b, p)
    return p + 4, p + 9


def _skip_typedesc(b, p):
    after = _fstr_end(b, p)
    n = _i32(b, p)
    t = b[p + 4:p + 4 + max(n - 1, 0)]
    if t == b"StructProperty":
        p = after; p += 4; p = _fstr_end(b, p); p += 4; p = _fstr_end(b, p); return p
    if t == b"EnumProperty":
        p = after; p += 4; p = _fstr_end(b, p); p += 4; p = _fstr_end(b, p); p += 4; p = _fstr_end(b, p); return p
    return after


def _key_before(b, m):
    """The map key FString ends exactly at m (just before CurrentState)."""
    for kp in range(m - 5, max(0, m - 400), -1):
        n = _i32(b, kp)
        if n > 0 and kp + 4 + n == m:
            try:
                s = b[kp + 4:m - 1].decode("utf-8")
            except UnicodeDecodeError:
                return None
            if s and all(c.isprintable() for c in s):
                return s
    return None


def _pretty(key):
    if not key:
        return "(unknown)"
    s = key.rsplit(".", 1)[-1]
    return s


# -------------------------------------------------------------- size-field chain
class _Descender:
    """Follow the single path enclosing target, collecting every size field."""
    def __init__(self, b, target):
        self.b = b
        self.target = target
        self.chain = []

    def fstr(self, o):
        return _fstr(self.b, o)

    def raw_end(self, o, root, params):
        if root in WIDTH:
            return o + WIDTH[root]
        if root in ("StrProperty", "NameProperty", "ObjectProperty",
                    "SoftObjectProperty", "EnumProperty", "TextProperty"):
            return _fstr_end(self.b, o)
        if root == "StructProperty":
            st = params[0][0] if params else None
            if st == "InstancedStruct":
                return self.inst_end(o)[0]
            return walk_body(self.b, o, len(self.b))
        raise ValueError(root)

    def inst_end(self, o, record=False, name="?"):
        _, o2 = _fstr(self.b, o)            # path
        size = _i32(self.b, o2)
        data = o2 + 4
        end = data + size
        if record and data <= self.target < end:
            self.chain.append({"size_off": o2, "kind": "InstancedStruct"})
        return end, data

    def descend_body(self, o, end):
        while o < end:
            name, o2 = _fstr(self.b, o)
            if name == "None" or name == "":
                return
            root, params, o3 = self._typename_p(o2)
            vstart, vend, size = self._ve(o3, root)
            if vstart <= self.target < vend:
                self.chain.append({"size_off": o3, "kind": root})
                self._recurse(o3, root, params, vstart, vend)
                return
            o = vend

    def _typename_p(self, o):
        name, oo = _fstr(self.b, o)
        nparam = _i32(self.b, oo); oo += 4
        params = []
        for _ in range(nparam):
            pn, pp, oo = self._typename_p(oo)
            params.append((pn, pp))
        return name, params, oo

    def _ve(self, o, root):
        return _value_end(self.b, o, root)

    def _recurse(self, o3, root, params, vstart, vend):
        if root == "StructProperty":
            self.descend_body(vstart, vend)
        elif root == "MapProperty":
            self.descend_map(vstart, vend, params)
        elif root in ("ArrayProperty", "SetProperty"):
            self.descend_array(vstart, vend, params)

    def descend_map(self, vstart, vend, params):
        keyTN, valTN = params[0], params[1]
        o = vstart
        o += 4            # numToRemove
        count = _i32(self.b, o); o += 4
        for _ in range(count):
            if o >= vend:
                break
            o = self.raw_end(o, keyTN[0], keyTN[1])      # key
            vs = o
            vroot = valTN[0]
            if vroot == "StructProperty" and valTN[1] and valTN[1][0][0] == "InstancedStruct":
                end, data = self.inst_end(o, record=True)
                if data <= self.target < end:
                    self.descend_body(data, end)
                    return
                o = end
            else:
                o = self.raw_end(o, vroot, valTN[1])
                if vs <= self.target < o:
                    if vroot == "StructProperty":
                        self.descend_body(vs, o)
                    return

    def descend_array(self, vstart, vend, params):
        elemTN = params[0]
        o = vstart
        count = _i32(self.b, o); o += 4
        for _ in range(count):
            if o >= vend:
                break
            es = o
            o = self.raw_end(o, elemTN[0], elemTN[1])
            if es <= self.target < o:
                if elemTN[0] == "StructProperty":
                    self.descend_body(es, o)
                return


def _chain_size_offs(payload, val_off):
    de = _Descender(payload, val_off)
    _, o = _fstr(payload, 0)
    o += 1
    de.descend_body(o, len(payload))
    if not de.chain:
        raise RuntimeError("could not resolve the container chain for this value")
    return [c["size_off"] for c in de.chain]


def _fstring_bytes(s):
    b = s.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(b)) + b


def _chain(payload, val_off):
    de = _Descender(payload, val_off)
    _, o = _fstr(payload, 0)
    o += 1
    de.descend_body(o, len(payload))
    if not de.chain:
        raise RuntimeError("could not resolve the container chain for this value")
    return de.chain


def _find_array_element(payload, val_off):
    """For a value at val_off inside an array-of-struct element, return
    (elem_start, elem_end, count_off, ancestor_size_offs_above_array)."""
    ch = _chain(payload, val_off)
    arr = [c for c in ch if c["kind"] == "ArrayProperty"]
    if not arr:
        raise ValueError("value is not inside an array")
    arr_so = arr[-1]["size_off"]
    count_off = arr_so + 5                      # i32 size, u8 hasGuid, then i32 count
    count = _i32(payload, count_off)
    o = count_off + 4
    for _ in range(count):
        s = o
        o = walk_body(payload, o, len(payload))
        if s <= val_off < o:
            above = [c["size_off"] for c in ch if c["size_off"] < s]
            return s, o, count_off, above
    raise ValueError("could not locate the array element")


def apply_ops(payload, replaces=(), deletes=()):
    """Unified length-changing editor.
      replaces: [(val_off, new_text)]   -- rewrite an FString value
      deletes:  [val_off]               -- remove the array element holding val_off
    All offsets are resolved on the ORIGINAL payload; ancestor size fields and
    array counts are adjusted, region edits applied high-offset-first, and the
    whole payload must re-validate or the edit is refused."""
    size_delta = {}
    count_delta = {}
    regions = []          # (start, old_len, new_bytes)

    for val_off, new_text in replaces:
        cur, _ = _fstr(payload, val_off)
        old_fb = _fstring_bytes(cur)
        new_fb = _fstring_bytes(new_text)
        if bytes(payload[val_off:val_off + len(old_fb)]) != old_fb:
            raise ValueError("save changed underneath; reload it")
        delta = len(new_fb) - len(old_fb)
        for c in _chain(payload, val_off):
            size_delta[c["size_off"]] = size_delta.get(c["size_off"], 0) + delta
        regions.append((val_off, len(old_fb), new_fb))

    for val_off in deletes:
        estart, eend, count_off, above = _find_array_element(payload, val_off)
        esz = eend - estart
        for so in above:
            size_delta[so] = size_delta.get(so, 0) - esz
        count_delta[count_off] = count_delta.get(count_off, 0) - 1
        regions.append((estart, esz, b""))

    d = bytearray(payload)
    for so, dl in size_delta.items():
        struct.pack_into("<i", d, so, struct.unpack_from("<i", d, so)[0] + dl)
    for co, dl in count_delta.items():
        struct.pack_into("<i", d, co, struct.unpack_from("<i", d, co)[0] + dl)
    for start, old_len, new in sorted(regions, key=lambda x: -x[0]):
        d[start:start + old_len] = new

    out = bytes(d)
    if not validate(out):
        raise ValueError("edit produced an inconsistent structure; refused")
    return out


def apply_value_edits(payload, items):
    """Back-compat wrapper: rewrite FString values."""
    return apply_ops(payload, replaces=list(items))


def apply_edits(payload, edits):
    """Quest state edits. edits: [{val_off, new_state}]."""
    items = []
    for e in edits:
        cur, _ = _fstr(payload, e["val_off"])
        if not cur.startswith(ENUM_PREFIX):
            raise ValueError("target is not a quest state")
        items.append((e["val_off"], ENUM_PREFIX + e["new_state"]))
    return apply_value_edits(payload, items)


# ----------------------------------------------------------- player attributes
import re as _re

# The protagonist's CharacterState is keyed "Hero"; its AttributeSetsByClass map
# holds the player's GAS attribute sets (Strength, Health, Level, skills, ...).
PLAYER_ANCHOR = b"\x05\x00\x00\x00Hero\x00\x15\x00\x00\x00AttributeSetsByClass\x00"
_FLOAT = b"\x0e\x00\x00\x00FloatProperty\x00\x00\x00\x00\x00\x04\x00\x00\x00\x00"
_BASE_PAT = _re.compile(b"\x0a\x00\x00\x00BaseValue\x00" + _re.escape(_FLOAT))
_CUR_PAT = _re.compile(b"\x0d\x00\x00\x00CurrentValue\x00" + _re.escape(_FLOAT))
_SET_PAT = _re.compile(rb"/Script/G1R\.AttributeSet_([A-Za-z]+)\x00")

# which tab each attribute belongs to; anything not listed -> "character"
# (weapon criticals live in the Strength set; keep them out of the Skills tab)
_SKILL_ATTRS = set()         # the Skills tab has no editable attribute values
# stats the user never wants to edit -> hidden from both tabs
_HIDE_ATTRS = {
    "MagicianLevel",
    "Resistance_Blunt", "Resistance_Edge", "Resistance_Point", "Resistance_Fire",
    "Resistance_Energy", "Resistance_Ice", "Resistance_Wind", "Resistance_Falling",
    "Critical_Fists", "Critical_OneHand", "Critical_TwoHand", "Critical_Orc",
    "CriticalLevelPercent", "Fatigue", "MaxFatigue",
    "LockpickDurability", "LockpickPrecision", "PickPocketing",
    # survival/consumption stats — not worth editing
    "Oxygen", "MaxOxygen", "OxygenDepletionRate", "OxygenRecoveryRate",
    "SleepTime", "MaxSleepTime", "MaxRestTime", "SleepTimeRecoveryAmount",
    "SleepTimeRecoveryPeriod", "Alcohol", "MaxAlcohol", "AlcoholDepletionRate",
    "Swampweed", "MaxSwampweed", "SwampweedDepletionRate",
    "FillRatio", "FillRatioPeriod", "MaxThresholdIndex",
}
# nicer labels for the common ones (fallback: raw name)
_ATTR_LABELS = {
    "SkillPoints": "Learning Points", "MaxHealth": "Max Health", "MaxMana": "Max Mana",
    "Critical_OneHand": "1H critical", "Critical_TwoHand": "2H critical",
    "Critical_Fists": "Fist critical", "Critical_Orc": "Orc critical",
    "PickPocketing": "Pickpocketing", "MaxOxygen": "Max Oxygen",
}


def _name_before(b, o):
    for kp in range(o - 5, max(0, o - 64), -1):
        n = _i32(b, kp)
        if n > 0 and kp + 4 + n == o:
            try:
                return b[kp + 4:o - 1].decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


def find_player_block(payload):
    i = payload.find(PLAYER_ANCHOR)
    return None if i < 0 else i + 9          # offset of the AttributeSetsByClass marker


def list_player_attributes(payload):
    """Returns [{set, name, label, value, base_off, current_off, tab}] for the Hero."""
    start = find_player_block(payload)
    if start is None:
        return []
    end = min(start + 0x20000, len(payload))
    sets = [(m.start(), m.group(1).decode()) for m in _SET_PAT.finditer(payload, start, end)]
    curs = [(m.start(), m.end()) for m in _CUR_PAT.finditer(payload, start, end)]

    attrs = []
    prev = start
    for m in _BASE_PAT.finditer(payload, start, end):
        if attrs and m.start() - prev > 0x1000:      # gap => left the Hero block
            break
        prev = m.end()
        base_off = m.end()
        name = _name_before(payload, m.start())
        if not name or name in _HIDE_ATTRS:
            continue
        st = None
        for so, sn in sets:
            if so < m.start():
                st = sn
            else:
                break
        current_off = next((ce for cs, ce in curs if cs > base_off), None)
        if current_off is None:
            continue
        attrs.append({
            "set": st or "?",
            "name": name,
            "label": _ATTR_LABELS.get(name, name),
            "value": round(struct.unpack_from("<f", payload, base_off)[0], 4),
            "base_off": base_off,
            "current_off": current_off,
            "tab": "skills" if name in _SKILL_ATTRS else "character",
            "advanced": _is_advanced(name),
        })
    return attrs


def _is_advanced(name):
    if name in ("ToughnessA", "ToughnessB", "ToughnessC"):
        return True
    if name.startswith("Critical_"):
        return True
    return any(w in name for w in ("Rate", "Ratio", "Period", "Threshold",
                                   "Multiplier", "Depletion", "Recovery", "Fill",
                                   "Percent", "Modifier", "Bounty"))


def apply_attribute_edits(payload, edits):
    """edits: [{base_off, current_off, value}]. Patches both floats in place
    (length-neutral). Only offsets that match a real player attribute are allowed."""
    valid = {a["base_off"]: a for a in list_player_attributes(payload)}
    d = bytearray(payload)
    for e in edits:
        a = valid.get(int(e["base_off"]))
        if not a:
            raise ValueError("unknown attribute offset")
        v = float(e["value"])
        struct.pack_into("<f", d, a["base_off"], v)
        struct.pack_into("<f", d, a["current_off"], v)
    return bytes(d)


# ------------------------------------------------------------- player skills
# Learned skills are GameplayEffectSpecs in the hero's ability system; the tier
# (Untrained/Trained/Master = I/II/III) is in the GE class name. Hunting skills
# are player-only, so we anchor the hero's skill cluster on them.
_SKILL_REF = _re.compile(rb"/Script/Angelscript\.Default__GE_Skill_([A-Za-z0-9_]+)")
_HUNT_REF = _re.compile(rb"GE_Skill_Hunting_[A-Za-z]+_Trained")
_KNOWN_TIERS = {"Untrained", "Trained", "Master", "Amateur", "Apprentice",
                "Skilled", "Journeyman", "Adept", "Expert"}
# The skill catalog (labels, ladders, learnable roster, GE class paths) lives in
# catalog.py so a fresh hero can be offered every skill, not just the ones already
# in the save. These are derived views consumed by the logic below.
_TIER_LADDER = catalog.TIER_LADDER        # base -> ordered tiers above Untrained
_HAS_UNTRAINED = catalog.HAS_UNTRAINED    # an _Untrained GE class exists (rename, not delete)
_SKILL_LABELS = catalog.LABELS
_LEARNABLE = catalog.LEARNABLE            # every learnable base (offered on a fresh hero)
_TIER_DISPLAY = {"Untrained": "Untrained", "Trained": "Trained", "Master": "Master",
                 "Skilled": "Novice", "Amateur": "Amateur (Circle 0)",
                 "1": "Circle 1", "2": "Circle 2", "3": "Circle 3",
                 "4": "Circle 4", "5": "Circle 5", "6": "Circle 6",
                 "Learned": "Learned"}
_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII"]


def _skill_split(raw):
    parts = raw.rsplit("_", 1)
    if len(parts) == 2 and (parts[1] in _KNOWN_TIERS or parts[0] == "Mage_Circle"):
        return parts[0], parts[1]              # Mage_Circle_<Amateur|1..6>
    return raw, None


def _skill_category(base):
    if base in catalog.CATEGORY:                 # catalog is authoritative
        return catalog.CATEGORY[base]
    if base.startswith("Hunting_"):              # heuristics for anything not catalogued yet
        return "Hunting"
    if base.startswith("Crafting_"):
        return "Crafting"
    if base.startswith("Mage") or "Circle" in base:
        return "Magic"
    return "Other"


def _tier_options(base, current):
    """Selectable tiers for a learned skill. Ranked weapon/thievery skills show
    I/II/III by position (Untrained=I, ...); circles show 0-6; binary skills just
    show their state. 'Untrained' always means unlearn (remove the effect)."""
    ladder = _TIER_LADDER.get(base)
    if base == "Mage_Circle":
        opts = [{"value": t, "label": _TIER_DISPLAY.get(t, t)} for t in ladder]
        opts.append({"value": "Untrained", "label": "Untrained (unlearn)"})
        return opts
    if ladder:                                   # ranked: full ladder = Untrained + tiers
        hints = catalog.SKILLS.get(base, {}).get("tier_labels", {})
        opts = []
        for i, t in enumerate(["Untrained"] + ladder):
            lbl = f"{_TIER_DISPLAY.get(t, t)} ({_ROMAN[i]})"
            if t in hints:
                lbl += f" — {hints[t]}"
            if t == "Untrained" and base not in _HAS_UNTRAINED:
                lbl += " · unlearn"
            opts.append({"value": t, "label": lbl})
        return opts
    opts = [{"value": current, "label": _TIER_DISPLAY.get(current, current)}]
    if current != "Untrained":            # binary skill -> offer unlearn (skip if it'd dup)
        opts.append({"value": "Untrained", "label": "Untrained (unlearn)"})
    return opts


def _learn_tier_options(base):
    """Tier dropdown for a skill the hero has NOT learned yet (the learnable roster).
    Ranked skills offer Untrained + each tier; circles offer Untrained + 0..6; the
    rest are on/off with a single 'Learn' state."""
    kind = catalog.SKILLS[base]["kind"]
    if kind == "ladder":
        hints = catalog.SKILLS[base].get("tier_labels", {})
        opts = []
        for i, t in enumerate(["Untrained"] + _TIER_LADDER[base]):
            lbl = f"{_TIER_DISPLAY.get(t, t)} ({_ROMAN[i]})"
            if t in hints:
                lbl += f" — {hints[t]}"
            if t != "Untrained":
                lbl += " · learn"
            opts.append({"value": t, "label": lbl})
        return opts
    if kind == "circle":
        opts = [{"value": "Untrained", "label": "Untrained"}]
        for t in _TIER_LADDER[base]:
            opts.append({"value": t, "label": _TIER_DISPLAY.get(t, t) + " · learn"})
        return opts
    return [{"value": "Untrained", "label": "Not learned"},
            {"value": catalog.learn_value(base), "label": "Learn"}]


def _player_skill_span(payload):
    """Bounds of the hero's effect-spec array (holds weapons, hunting, thievery,
    magic …), found from a player-only hunting anchor. Falls back to a window."""
    hunts = [m.start() for m in _HUNT_REF.finditer(payload)]
    if not hunts:
        return None
    try:
        ch = _chain(payload, hunts[0] - 4)
        arr = [c for c in ch if c["kind"] == "ArrayProperty"][-1]
        lo, hi, _ = _value_end(payload, arr["size_off"], "ArrayProperty")
        if hi - lo < 0x200000:                       # sanity: one array, not the world
            return lo, hi
    except Exception:
        pass
    return min(hunts) - 0x8000, max(hunts) + 0x1000


def list_player_skills(payload):
    """The hero's skills. Learned ones carry editable tier options; known weapons
    that aren't learned are listed as Untrained (display only)."""
    span = _player_skill_span(payload)
    learned = []
    seen_base = set()
    if span:
        lo, hi = span
        seen = set()
        for m in _SKILL_REF.finditer(payload, max(0, lo), min(len(payload), hi)):
            val_off = m.start() - 4
            full = m.group().decode("ascii")
            if val_off < 0 or _i32(payload, val_off) != len(full) + 1 or val_off in seen:
                continue
            seen.add(val_off)
            base, tier = _skill_split(m.group(1).decode("ascii"))
            seen_base.add(base)
            tname = tier or "Learned"
            learned.append({
                "id": val_off, "fid": str(val_off), "base": base,
                "label": _SKILL_LABELS.get(base, base.replace("_", " ")),
                "category": _skill_category(base), "tier": tname,
                "tiers": _tier_options(base, tname), "learned": True,
                "_base_path": "/Script/Angelscript.Default__GE_Skill_" + base,
            })
    # roster: every catalogued skill the hero hasn't learned -> can be *learned*
    # (this is what lets a fresh hero be given hunting/utility/crafting/magic skills).
    for base in _LEARNABLE:
        if base not in seen_base:
            learned.append({"id": None, "fid": "new:" + base, "base": base,
                            "label": _SKILL_LABELS.get(base, base.replace("_", " ")),
                            "category": _skill_category(base), "tier": "Untrained",
                            "tiers": _learn_tier_options(base),
                            "learned": False, "_base_path": None})
    learned.sort(key=lambda s: (s["category"], not s["learned"], s["label"]))
    return learned


def build_skill_ops(payload, edits):
    """edits: [{id (fid), new_tier}] -> (replaces, deletes, learns).
    learns: [(base, tier)] for skills the hero doesn't have yet."""
    by_fid = {s["fid"]: s for s in list_player_skills(payload)}
    replaces, deletes, learns = [], [], []
    for e in edits:
        s = by_fid.get(str(e["id"]))
        if not s:
            raise ValueError("unknown skill")
        nt = e["new_tier"]
        if nt not in {o["value"] for o in (s["tiers"] or [])}:
            raise ValueError("invalid tier")
        if s["learned"]:
            if nt == s["tier"]:
                continue
            if nt == "Untrained" and s["base"] not in _HAS_UNTRAINED:
                deletes.append(s["id"])                   # no Untrained class -> unlearn
            else:
                replaces.append((s["id"], s["_base_path"] + "_" + nt))   # tier rename (incl. ->Untrained)
        elif nt != "Untrained":
            learns.append((s["base"], nt))                # learn -> clone + retarget
    return replaces, deletes, learns


# The hero's effect-spec array is the FIRST property ("ActiveEffects") of the
# protagonist's "Hero"-keyed CharacterState. This anchor is unique in the save and
# is skill-independent, so it locates the array even when it is EMPTY (a fresh hero
# with nothing learned yet) -- where the contents-based _player_skill_span fails.
_HERO_AE = _re.compile(
    rb"\x05\x00\x00\x00Hero\x00\x0e\x00\x00\x00ActiveEffects\x00"
    rb"\x0e\x00\x00\x00ArrayProperty\x00")
# A real ActiveGameplayEffect array element captured from a known-good save (see
# skill_donor.py), used as a donor template when the current save has no learned
# skill to clone. The game re-derives the effect from the GE class we retarget to.
def _donor_template():
    from skill_donor import DONOR
    return DONOR


def _hero_ae_array(payload):
    """Locate the hero's ActiveEffects ArrayProperty by name (works even when the
    array is empty). Returns (arr_size_off, count_off, vstart, vend, count,
    ancestor_size_offs) or None."""
    m = _HERO_AE.search(payload)
    if not m:
        return None
    name_off = m.start() + 9                      # the 'ActiveEffects' name fstring
    type_start = _fstr_end(payload, name_off)     # skip name -> type descriptor
    root, arr_so = _typename(payload, type_start)
    if root != "ArrayProperty":
        return None
    count_off = arr_so + 5
    vstart, vend, _ = _value_end(payload, arr_so, "ArrayProperty")
    count = _i32(payload, count_off)
    size_offs = [c["size_off"] for c in _chain(payload, count_off)]
    return arr_so, count_off, vstart, vend, count, size_offs


def _learn_from_template(payload, base, tier):
    """Synthesize a learned skill with NO live donor: append a captured effect-spec
    element to the hero's ActiveEffects array, then retarget its GE reference."""
    loc = _hero_ae_array(payload)
    if not loc:
        raise ValueError("could not locate the hero's effect array to learn into")
    _arr_so, count_off, _vstart, vend, count, size_offs = loc
    tmpl = _donor_template()

    # append the template element at the end of the array value: count++, every
    # enclosing container size field grows by the element's size.
    d = bytearray(payload)
    for so in size_offs:
        struct.pack_into("<i", d, so, _i32(d, so) + len(tmpl))
    struct.pack_into("<i", d, count_off, count + 1)
    d[vend:vend] = tmpl
    p2 = bytes(d)
    if not validate(p2):
        raise ValueError("skill insert (template) produced an invalid structure")

    # retarget the appended element's single GE reference to the new skill class.
    m = _SKILL_REF.search(p2, vend, vend + len(tmpl))
    if not m:
        raise ValueError("donor template is missing its GE reference")
    return apply_ops(p2, replaces=[(m.start() - 4, catalog.skill_class(base, tier))])


def learn_skill(payload, base, tier):
    """EXPERIMENTAL: add a skill the hero doesn't have, by cloning a same-family
    learned effect-spec and retargeting its GE reference. Structurally safe
    (re-validated); gameplay correctness depends on the game re-deriving the
    effect from its GE class on load. With no learned skill to clone from (a fresh
    hero), falls back to a captured donor template appended to the hero's array."""
    skills = [s for s in list_player_skills(payload) if s["learned"]]
    if not skills:
        return _learn_from_template(payload, base, tier)
    cat = _skill_category(base)
    donor = (next((s for s in skills if s["category"] == cat), None)
             or next((s for s in skills if s["category"] == "Combat"), None)
             or skills[0])
    val_off = donor["id"]
    estart, eend, count_off, above = _find_array_element(payload, val_off)
    donor_size = eend - estart

    # step A: duplicate the donor element verbatim (valid: count++, ancestors grow)
    d = bytearray(payload)
    for so in above:
        struct.pack_into("<i", d, so, struct.unpack_from("<i", d, so)[0] + donor_size)
    struct.pack_into("<i", d, count_off, struct.unpack_from("<i", d, count_off)[0] + 1)
    d[eend:eend] = payload[estart:eend]
    p2 = bytes(d)
    if not validate(p2):
        raise ValueError("skill insert (clone) produced an invalid structure")

    # step B: retarget the clone's GE reference to the new skill class
    dup_ref_off = val_off + donor_size
    return apply_ops(p2, replaces=[(dup_ref_off, catalog.skill_class(base, tier))])


def apply_skill_edits(payload, edits):
    r, d, learns = build_skill_ops(payload, edits)
    out = apply_ops(payload, replaces=r, deletes=d)
    for base, tier in learns:
        out = learn_skill(out, base, tier)
    return out


# ------------------------------------------------------------- player inventory
# Items are ItemSlot structs: m_ItemDefinition (ObjectProperty path) + m_ItemCount
# (IntProperty). The hero's inventory is the slot-cluster holding the biggest
# stack (coins/ore dwarf any NPC), located by gap-clustering all item slots.
_ITEM_DEF = _re.compile(rb"m_ItemDefinition\x00\x0f\x00\x00\x00ObjectProperty\x00")
_ITEM_CNT = b"m_ItemCount\x00\x0c\x00\x00\x00IntProperty\x00"
_INV_DROP = ("NoWeapon", "WatchFight", "_Climbing", "_Swimming", "_WaterWalking")
_ITEM_LABELS = {
    "ItMi_Oldcoin_01": "Coins", "ItMi_Orenugget": "Ore", "ItKe_Lockpick": "Lockpick",
    "ItAm_Arrow": "Arrows", "ItAm_Bolt": "Bolts", "ItMw_1H_Torch": "Torch",
    "ItFo_Apple": "Apple", "ItFo_Cheese": "Cheese", "ItFo_Loaf": "Bread",
    "ItFo_Muttonraw": "Raw Mutton", "ItFo_Mutton_01": "Mutton", "ItFo_Whitemeat": "White Meat",
    "ItFo_Rice": "Rice", "ItFo_Soup": "Stew", "ItFo_Potion_Beer": "Beer",
    "ItFo_Potion_Booze": "Schnapps", "ItFo_Potion_Wine": "Wine", "ItFo_Potion_Water_01": "Water",
    "ItFo_Potion_Health_01": "Healing Potion (S)", "ItFo_Potion_Health_02": "Healing Potion (M)",
    "ItFo_Potion_Health_03": "Healing Potion (L)", "ItFo_Plants_Mushroom_01": "Mushroom",
    "ItFo_Plants_Berrys_01": "Berries", "ItFo_Plants_Lobelia": "Lobelia",
    "ItFo_Plants_Seraphis_01": "Seraphis", "ItMi_Joint_01": "Joint",
}


_ITEM_CATS = {
    "ItMi": "Misc", "ItFo": "Food / Potion", "ItMw": "Melee Weapon",
    "ItRw": "Ranged Weapon", "ItAm": "Ammo", "ItAr": "Armor",
    "ItAt": "Amulet / Trophy", "ItKe": "Key / Lockpick", "ItWr": "Written",
    "ItMs": "Misc Stack", "ItAI": "AI / Special", "ItRu": "Rune / Scroll",
}


def _item_category(item):
    return _ITEM_CATS.get(item[:4], "Other") if item.startswith("It") else "Trophy / Other"


def list_item_db(payload):
    """Every distinct item class that appears anywhere in the save (valid ids)."""
    items = set()
    for m in _ITEM_DEF.finditer(payload):
        vo = m.end() + 9
        n = _i32(payload, vo)
        if 0 < n < 200:
            items.add(payload[vo + 4:vo + 4 + n - 1].decode("utf-8", "replace").split(".")[-1])
    return [{"id": it, "label": _item_label(it), "category": _item_category(it)}
            for it in sorted(items)]


def _item_label(item):
    if item in _ITEM_LABELS:
        return _ITEM_LABELS[item]
    s = _re.sub(r"^It[A-Z][a-z]_", "", item)     # drop ItMi_/ItFo_/ItMw_/…
    s = _re.sub(r"^(1H|2H)_", "", s)             # drop weapon hand prefix
    s = _re.sub(r"_\d+$", "", s)                 # drop trailing _01 variant
    return s.replace("_", " ").strip() or item


def _all_item_slots(payload):
    slots = []
    for m in _ITEM_DEF.finditer(payload):
        o = m.end(); vo = o + 9          # arrayIndex i32, size i32, hasGuid u8, then FString
        n = _i32(payload, vo)
        if not (0 < n < 200):
            continue
        item = payload[vo + 4:vo + 4 + n - 1].decode("utf-8", "replace").split(".")[-1]
        ci = payload.find(_ITEM_CNT, o, o + 240)
        if ci < 0:
            continue
        cvo = ci + len(_ITEM_CNT) + 9
        slots.append((m.start(), item, struct.unpack_from("<i", payload, cvo)[0], cvo))
    return slots


def find_player_inventory(payload):
    """Returns [{id (count offset), item, label, count}] for the hero's items."""
    slots = sorted(_all_item_slots(payload), key=lambda s: s[0])
    if not slots:
        return []
    clusters = [[slots[0]]]
    for s in slots[1:]:
        (clusters[-1] if s[0] - clusters[-1][-1][0] <= 0x4000 else clusters.append([s]) or clusters[-1]).append(s)
    mx = max(slots, key=lambda s: s[2])                       # biggest stack = player's
    player = next((c for c in clusters if c[0][0] <= mx[0] <= c[-1][0]), None)
    if not player:
        return []
    out = []
    for _off, item, cnt, cvo in player:
        if any(x in item for x in _INV_DROP):
            continue
        out.append({"id": cvo, "item": item, "label": _item_label(item), "count": cnt})
    return out


_ITEM_ID = b"m_Id\x00\x0c\x00\x00\x00IntProperty\x00"


def add_item(payload, item_key, count=1):
    """EXPERIMENTAL: add an item by cloning the hero's coin slot and retargeting it.
    The clone is APPENDED to the container's slot array (not inserted mid-array) and
    given m_Id = the container's item count — because m_Id is the per-container slot
    index the game selects by; inserting mid-array or using a global id makes the UI
    select the wrong item. Re-validated structurally."""
    item_key = item_key.strip().split(".")[-1]
    if not _re.fullmatch(r"[A-Za-z0-9_]{2,80}", item_key):
        raise ValueError("invalid item key")
    inv = find_player_inventory(payload)
    if not inv:
        raise ValueError("no player inventory found")

    donor = next((s for s in inv if s["item"] == "ItMi_Oldcoin_01"), inv[0])
    cnt_off = donor["id"]
    dm = None
    for m in _ITEM_DEF.finditer(payload, max(0, cnt_off - 400), cnt_off):
        dm = m
    if not dm:
        raise ValueError("donor item-definition not found")
    def_voff = dm.end() + 9
    estart, eend, count_off, above = _find_array_element(payload, def_voff)
    donor_size = eend - estart
    # append point = end of the container's slot array; m_Id = next per-container index
    arr = [c for c in _chain(payload, def_voff) if c["kind"] == "ArrayProperty"][-1]
    _, arr_end, _ = _value_end(payload, arr["size_off"], "ArrayProperty")
    new_id = struct.unpack_from("<i", payload, count_off)[0]
    idm = _re.search(_ITEM_ID, payload[estart:eend])
    id_rel = (idm.end() + 9) if idm else None        # m_Id value offset within the element

    # step A: append the donor slot at the array end (no existing slot moves)
    d = bytearray(payload)
    for so in above:
        struct.pack_into("<i", d, so, struct.unpack_from("<i", d, so)[0] + donor_size)
    struct.pack_into("<i", d, count_off, struct.unpack_from("<i", d, count_off)[0] + 1)
    d[arr_end:arr_end] = payload[estart:eend]
    # step B: set the clone's count, fresh m_Id, then retarget the class
    struct.pack_into("<i", d, arr_end + (cnt_off - estart), int(count))
    if id_rel is not None:
        struct.pack_into("<i", d, arr_end + id_rel, new_id)
    p2 = bytes(d)
    if not validate(p2):
        raise ValueError("item insert (clone) produced an invalid structure")
    return apply_ops(p2, replaces=[(arr_end + (def_voff - estart), "/Script/Angelscript." + item_key)])


def apply_inventory_edits(payload, edits):
    """edits: [{id, value}] -> set m_ItemCount in place (length-neutral)."""
    valid = {s["id"] for s in find_player_inventory(payload)}
    d = bytearray(payload)
    for e in edits:
        off = int(e["id"])
        if off not in valid:
            raise ValueError("unknown item slot")
        v = int(e["value"])
        if not (0 <= v <= 2_000_000_000):
            raise ValueError("count out of range")
        struct.pack_into("<i", d, off, v)
    return bytes(d)


def slot_name(container):
    """Best-effort human label from the plaintext header (m_PlayerSaveName)."""
    h = container.data[:container.data_start]
    i = h.find(b"m_SlotName")
    if i >= 0:
        j = h.find(b"StrProperty", i)
        if j >= 0:
            try:
                s, _ = _fstr(h, j + 16)
                if s:
                    return s
            except Exception:
                pass
    return None


# ------------------------------------------------------- world / story flags
# The game's script variables live in area-bound memory containers as
# Name -> Int maps (a name FString immediately followed by an i32 value, packed
# back to back). These are the "checks" dialogues/quests test, e.g.
# GuardPassageWarning_SC, SwampCampTemple_Permision. Editing one is a single
# in-place i32 write (length-neutral), so it's as safe as item-count edits.
# script flags live ONLY in StoryPropertyValues maps (MapProperty<Name,Int>);
# scoping to them avoids catching unrelated name->int maps (e.g. item maps).
_STORY_MAP = _re.compile(rb"StoryPropertyValues\x00")
_FLAG_ID = _re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,62}$")


def _read_flag_name(payload, o):
    n = struct.unpack_from("<i", payload, o)[0]
    if 1 <= n <= 64 and o + 4 + n <= len(payload) and payload[o + 4 + n - 1] == 0:
        s = payload[o + 4:o + 4 + n - 1].decode("ascii", "replace")
        if _FLAG_ID.match(s):
            return s, o + 4 + n
    return None, o


def _scan_flags(payload):
    """Returns [(value_offset, name, value)] for every StoryPropertyValues entry."""
    out = []
    for hm in _STORY_MAP.finditer(payload):
        np = hm.start() - 4                               # FString length prefix
        if np < 0 or struct.unpack_from("<i", payload, np)[0] != 20:
            continue
        try:
            _, o2 = _fstr(payload, np)                    # "StoryPropertyValues"
            root, o3 = _typename(payload, o2)             # "MapProperty"(Name, Int)
            if root != "MapProperty":
                continue
            vstart, vend, _ = _value_end(payload, o3, root)
        except Exception:
            continue
        p = next((c for c in (vstart + 8, vstart + 4, vstart)
                  if _read_flag_name(payload, c)[0]), None)   # skip the count header
        while p is not None and p < vend:
            name, e = _read_flag_name(payload, p)
            if not name:
                break
            out.append((e, name, struct.unpack_from("<i", payload, e)[0]))
            p = e + 4
    return out


def list_passages(payload):
    """Distinct script flags (name -> int). A name may occur in several memory
    containers; we keep every value offset so an edit updates them all."""
    seen = {}
    for vo, name, val in _scan_flags(payload):
        e = seen.get(name)
        if e is None:
            seen[name] = {"name": name, "value": val, "offs": [vo]}
        else:
            e["offs"].append(vo)
    return sorted(seen.values(), key=lambda x: x["name"].lower())


def apply_passage_edits(payload, edits):
    """edits: [{name, value}] -> set the i32 at every offset of that flag (neutral)."""
    by_name = {f["name"]: f for f in list_passages(payload)}
    d = bytearray(payload)
    for e in edits:
        f = by_name.get(e["name"])
        if not f:
            raise ValueError(f"unknown flag {e.get('name')!r}")
        v = int(e["value"])
        if not (-2_000_000_000 <= v <= 2_000_000_000):
            raise ValueError("flag value out of range")
        for off in f["offs"]:
            struct.pack_into("<i", d, off, v)
    return bytes(d)


# ------------------------------------------------------- NPC behaviour (read-only)
# Each character with a set relationship carries an ActivePersonalRelationshipModifiers
# array of modifier objects (Story_Permanent[Friend/Angry/Enemy/Neutral], Enemy,
# ExecuteCharacter, LootCharacter, FriendlyToPlayer). Friend/Angry (no target) are
# toward the player. This is read-only for now.
_REL_ARRAY = _re.escape(b"ActivePersonalRelationshipModifiers\x00\x0e\x00\x00\x00ArrayProperty\x00")
_REL_MOD = _re.compile(rb"ActivePersonalRelationshipModifier_([A-Za-z_]+)")
_REL_TGT = _re.compile(rb"TargetCharacterGlobalID\x00\x0c\x00\x00\x00NameProperty\x00.{9}", _re.S)
_NPC_ROLE = {"KDW": "Water Mage", "GRD": "Guard", "NOV": "Novice", "VLK": "Townsfolk",
             "ORG": "Digger", "TPL": "Templar", "SLD": "Mercenary", "GUR": "Guru",
             "BAN": "Bandit", "STT": "Companion", "SLD": "Mercenary", "KDF": "Fire Mage"}


def _npc_name_role(key):
    m = _re.match(r"[A-Z]+_([A-Z]+)_([A-Za-z0-9]+?)_?\d*[-_]", key)
    if m:
        return m.group(2), _NPC_ROLE.get(m.group(1), m.group(1))
    return (key.split("-")[0] or key), ""


def _behaviour_status(mods):
    types = {m["type"] for m in mods}
    rels = {m["relationship"] for m in mods if m["relationship"]}
    if "FriendlyToPlayer" in types or "Friend" in rels:
        return "Friendly"
    if "Angry" in rels:
        return "Angry"
    if "Enemy" in rels or "Enemy" in types:
        return "Hostile"
    if "ExecuteCharacter" in types:
        return "Execute"
    if "LootCharacter" in types:
        return "Downed"
    if "Neutral" in rels:
        return "Neutral"
    return "Other"


def list_behaviours(payload):
    """Read-only: NPCs that have a relationship modifier, and what it means."""
    arrs = [m.start() for m in _re.finditer(_REL_ARRAY, payload)]
    out = []
    for i, o in enumerate(arrs):
        end = arrs[i + 1] - 200 if i + 1 < len(arrs) else o + 2000
        seg = payload[o:end]
        mods = []
        for mm in _REL_MOD.finditer(seg):
            rest = seg[mm.end():mm.end() + 320]
            rel = _re.search(rb"ERelationship::([A-Za-z]+)", rest)
            tg = _REL_TGT.search(rest)
            target = None
            if tg:
                to = o + mm.end() + tg.end()
                n = _i32(payload, to)
                if 0 < n < 200:
                    target = payload[to + 4:to + 4 + n - 1].decode("ascii", "replace").split("-")[0]
            mods.append({"type": mm.group(1).decode(),
                         "relationship": rel.group(1).decode() if rel else None,
                         "target": target})
        if not mods:
            continue
        ks = _re.findall(rb"[A-Z][A-Za-z]{1,4}_[A-Za-z0-9_]+-[A-Za-z0-9_]+", payload[max(0, o - 160):o])
        key = ks[-1].decode() if ks else "?"
        name, role = _npc_name_role(key)
        detail = ", ".join(m["type"].replace("_", " ")
                           + (f" [{m['relationship']}]" if m["relationship"] else "")
                           + (f" → {m['target']}" if m["target"] else "") for m in mods)
        out.append({"npc": name, "role": role, "key": key,
                    "status": _behaviour_status(mods), "detail": detail})
    out.sort(key=lambda x: (x["status"], x["npc"]))
    return out


# ------------------------------------------------------------------- crimes
# Each crime is a struct with a bIsForgiven BoolProperty (0x00 active, 0x10
# forgiven), a CriminalGlobalID, a Crime.* type tag, and victim Guild.* tags.
# A guild attacks a person with enough active crimes against it. Forgiving =
# setting bIsForgiven to 0x10 (length-neutral byte write).
_CR_FORGIVEN = _re.compile(rb"bIsForgiven\x00\x0d\x00\x00\x00BoolProperty\x00")
_CR_CRIMINAL = _re.compile(rb"CriminalGlobalID\x00\x0d\x00\x00\x00NameProperty\x00")
_CR_TYPE = _re.compile(rb"Crime\.[A-Za-z._]+")
_CR_GUILD = _re.compile(rb"Guild\.(?:Human|Orc)\.[A-Za-z.]+")
_FORGIVEN_TRUE = 0x10


def _clean_guild(tag):
    return tag.replace("Guild.Human.", "").replace("Guild.Orc.", "Orc.").replace(".", " · ")


def _scan_crimes(payload):
    ms = [m for m in _CR_FORGIVEN.finditer(payload)]
    starts = [m.start() for m in ms]
    ends = starts[1:] + [len(payload)]
    out = []
    for i, m in enumerate(ms):
        o = m.start()
        seg = payload[o:ends[i]]
        cm = _CR_CRIMINAL.search(seg)
        criminal = _fstr(payload, o + cm.end() + 9)[0].split("-")[0] if cm else "?"
        ct = _CR_TYPE.search(seg)
        ctype = ct.group().decode().replace("Crime.", "") if ct else "?"
        guilds = sorted(set(g.group().decode() for g in _CR_GUILD.finditer(seg)))
        out.append({"off": m.end() + 8, "criminal": criminal or "(none)",
                    "type": ctype, "guilds": guilds, "forgiven": payload[m.end() + 8] != 0})
    return out


def list_crimes(payload):
    """Crimes grouped by (criminal, victim guild): count + how many still active."""
    groups = {}
    for c in _scan_crimes(payload):
        for g in (c["guilds"] or ["(area)"]):
            e = groups.setdefault((c["criminal"], g),
                                  {"criminal": c["criminal"], "guild": g,
                                   "guild_label": _clean_guild(g), "count": 0, "active": 0})
            e["count"] += 1
            e["active"] += 0 if c["forgiven"] else 1
    return sorted(groups.values(), key=lambda x: (-x["active"], x["criminal"], x["guild"]))


def apply_crime_edits(payload, forgive):
    """forgive: [{criminal, guild}] -> mark every matching active crime forgiven."""
    want = {(f["criminal"], f["guild"]) for f in forgive}
    d = bytearray(payload)
    for c in _scan_crimes(payload):
        if c["forgiven"]:
            continue
        if any((c["criminal"], g) in want for g in c["guilds"]):
            d[c["off"]] = _FORGIVEN_TRUE
    return bytes(d)


def add_passage(payload, name, value=1):
    """EXPERIMENTAL: add a brand-new flag (name -> int) to the StoryPropertyValues
    map (append a key/value pair, bump count + enclosing sizes). Use to *grant* a
    permission the save doesn't have yet. Re-validated structurally."""
    name = name.strip()
    if not _FLAG_ID.match(name):
        raise ValueError("invalid flag name (letters/digits/underscore, 3-63 chars)")
    if any(n == name for _, n, _v in _scan_flags(payload)):
        raise ValueError("flag already exists — edit it instead")
    for hm in _STORY_MAP.finditer(payload):
        np = hm.start() - 4
        if np < 0 or struct.unpack_from("<i", payload, np)[0] != 20:
            continue
        try:
            _, o2 = _fstr(payload, np)
            root, o3 = _typename(payload, o2)
            if root != "MapProperty":
                continue
            vstart, vend, _ = _value_end(payload, o3, root)
        except Exception:
            continue
        first = next((c for c in (vstart + 8, vstart + 4, vstart)
                      if _read_flag_name(payload, c)[0]), None)
        if first is None:
            continue
        count_off = first - 4
        p = first
        while p < vend:                                   # walk to end of the pairs
            nm, e = _read_flag_name(payload, p)
            if not nm:
                break
            p = e + 4
        _, anchor = _read_flag_name(payload, first)        # value offset of first pair
        try:
            above = [c["size_off"] for c in _chain(payload, anchor)]
        except Exception:
            raise ValueError("could not resolve the story-map container chain")
        pair = (struct.pack("<i", len(name) + 1) + name.encode("ascii") + b"\x00"
                + struct.pack("<i", int(value)))
        d = bytearray(payload)
        for so in above:
            struct.pack_into("<i", d, so, struct.unpack_from("<i", d, so)[0] + len(pair))
        struct.pack_into("<i", d, count_off, struct.unpack_from("<i", d, count_off)[0] + 1)
        d[p:p] = pair                                      # append the new pair
        out = bytes(d)
        if not validate(out):
            raise ValueError("adding the flag produced an invalid structure")
        return out
    raise ValueError("no StoryPropertyValues map found")
