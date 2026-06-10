"""
Core logic for the Gothic 1 Remake quest fixer.

Parses the custom GSAV/Oodle container, decompresses with real Oodle, lists every
quest objective and its EQuestState, applies state changes (updating every
enclosing container size field), and recompresses with real Oodle Kraken.

Reverse-engineering credit: the container format + the recompress trick come
from wealth's gist (gist.github.com/wealth/de5a461e02ab49060d5f418a520ee1e8).
"""
import ctypes
import struct

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
        return body, body + 2, size
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
    if not de.chain or de.chain[-1]["kind"] != "EnumProperty":
        raise RuntimeError("could not resolve the quest's container chain")
    return [c["size_off"] for c in de.chain]


def _fstring_bytes(s):
    b = s.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(b)) + b


def apply_edits(payload, edits):
    """edits: list of {val_off, new_state}. Returns the new payload bytes.
    Raises ValueError if the result wouldn't be structurally valid."""
    d = bytearray(payload)
    size_delta = {}
    value_patches = []
    for e in edits:
        val_off = e["val_off"]
        cur, _ = _fstr(payload, val_off)
        if not cur.startswith(ENUM_PREFIX):
            raise ValueError("target is not a quest state")
        new_full = ENUM_PREFIX + e["new_state"]
        old_fb = _fstring_bytes(cur)
        new_fb = _fstring_bytes(new_full)
        if bytes(payload[val_off:val_off + len(old_fb)]) != old_fb:
            raise ValueError("save changed underneath; reload it")
        delta = len(new_fb) - len(old_fb)
        for so in _chain_size_offs(payload, val_off):
            size_delta[so] = size_delta.get(so, 0) + delta
        value_patches.append((val_off, len(old_fb), new_fb))

    for so, dl in size_delta.items():
        struct.pack_into("<i", d, so, struct.unpack_from("<i", d, so)[0] + dl)
    for val_off, old_len, new_fb in sorted(value_patches, key=lambda x: -x[0]):
        d[val_off:val_off + old_len] = new_fb

    out = bytes(d)
    if not validate(out):
        raise ValueError("edit produced an inconsistent structure; refused")
    return out


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
