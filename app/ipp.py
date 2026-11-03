"""IPP (Internet Printing Protocol, RFC 8010/8011) - just enough to be an AirPrint / IPP Everywhere printer.

A request or response is: version (2 bytes), operation/status (2), request id (4), then groups of attributes, then (for
Print-Job) the document.  An attribute is  value-tag, name, value;  extra values of the same attribute repeat the tag with an
empty name; a *collection* (e.g. media-col) is a begCollection value followed by member name/value pairs and an endCollection.

Values are written as ``(tag, python_value)``:  ``kw("none")``, ``integer(3)``, ``uri("ipp://...")``, ``coll(media_size=...)`` ...
An attribute is ``(name, [values])``;  a group is ``(group_tag, [attributes])``.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NamedTuple

# --- group (delimiter) tags
G_OPERATION, G_JOB, G_END, G_PRINTER, G_UNSUPPORTED = 0x01, 0x02, 0x03, 0x04, 0x05

# --- value tags
UNSUPPORTED, UNKNOWN, NOVALUE = 0x10, 0x12, 0x13
INTEGER, BOOLEAN, ENUM = 0x21, 0x22, 0x23
OCTETS, DATETIME, RESOLUTION, RANGE, BEGCOLL, TEXTLANG, NAMELANG, ENDCOLL = 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37
TEXT, NAME, KEYWORD, URI, URISCHEME, CHARSET, LANG, MIME = 0x41, 0x42, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49
MEMBER = 0x4A

# --- operations
PRINT_JOB, VALIDATE_JOB, CREATE_JOB, SEND_DOCUMENT = 0x0002, 0x0004, 0x0005, 0x0006
CANCEL_JOB, GET_JOB_ATTRIBUTES, GET_JOBS, GET_PRINTER_ATTRIBUTES = 0x0008, 0x0009, 0x000A, 0x000B

# --- status codes
OK, OK_IGNORED = 0x0000, 0x0001
BAD_REQUEST, FORBIDDEN, NOT_POSSIBLE, NOT_FOUND = 0x0400, 0x0401, 0x0404, 0x0406
FORMAT_NOT_SUPPORTED, ATTRIBUTES_OR_VALUES = 0x040A, 0x040B
ENTITY_TOO_LARGE = 0x0408
INTERNAL_ERROR, OPERATION_NOT_SUPPORTED, VERSION_NOT_SUPPORTED, BUSY = 0x0500, 0x0501, 0x0503, 0x0507

MAX_COLLECTION_DEPTH = 6


class Res(NamedTuple):
    x: int
    y: int
    units: int = 3      # 3 = dots per inch, 4 = dots per centimetre


class Rng(NamedTuple):
    lo: int
    hi: int


# --------------------------------------------------------------------------- building values
def kw(*v: str) -> list: return [(KEYWORD, x) for x in v]
def integer(*v: int) -> list: return [(INTEGER, int(x)) for x in v]
def enum(*v: int) -> list: return [(ENUM, int(x)) for x in v]
def boolean(v: bool) -> list: return [(BOOLEAN, bool(v))]
def uri(*v: str) -> list: return [(URI, x) for x in v]
def text(v: str) -> list: return [(TEXT, v)]
def name(*v: str) -> list: return [(NAME, x) for x in v]
def mime(*v: str) -> list: return [(MIME, x) for x in v]
def charset(v: str) -> list: return [(CHARSET, v)]
def lang(v: str) -> list: return [(LANG, v)]
def resolution(x: int, y: int, units: int = 3) -> list: return [(RESOLUTION, Res(x, y, units))]
def rng(lo: int, hi: int) -> list: return [(RANGE, Rng(lo, hi))]
def when(dt: datetime) -> list: return [(DATETIME, dt)]


def coll(**members: list) -> list:
    """A collection value.  Member names use underscores for readability: ``media_size`` -> ``media-size``."""
    return [(BEGCOLL, [(k.replace("_", "-"), v) for k, v in members.items()])]


def merge(*lists: list) -> list:
    return [v for lst in lists for v in lst]


# --------------------------------------------------------------------------- encoding
def _enc_value(tag: int, val: Any) -> bytes:
    if tag in (INTEGER, ENUM):
        return struct.pack(">i", val)
    if tag == BOOLEAN:
        return b"\x01" if val else b"\x00"
    if tag == RANGE:
        return struct.pack(">ii", val.lo, val.hi)
    if tag == RESOLUTION:
        return struct.pack(">iiB", val.x, val.y, val.units)
    if tag == DATETIME:
        d = val.astimezone(timezone.utc)
        return struct.pack(">HBBBBBBcBB", d.year, d.month, d.day, d.hour, d.minute, d.second, d.microsecond // 100000, b"+", 0, 0)
    if tag == OCTETS:
        return bytes(val)
    if tag in (UNSUPPORTED, UNKNOWN, NOVALUE):
        return b""
    return str(val).encode("utf-8")


def _enc_attr(attr_name: str, values: list, in_member: bool = False) -> bytes:
    out = bytearray()
    for i, (tag, val) in enumerate(values):
        n = b"" if (i or in_member) else attr_name.encode("utf-8")
        if tag == BEGCOLL:
            out += bytes([BEGCOLL]) + struct.pack(">H", len(n)) + n + b"\x00\x00"
            for member_name, member_values in val:
                mn = member_name.encode("utf-8")
                out += bytes([MEMBER]) + b"\x00\x00" + struct.pack(">H", len(mn)) + mn
                for mtag, mval in member_values:
                    out += _enc_attr("", [(mtag, mval)], in_member=True)
            out += bytes([ENDCOLL]) + b"\x00\x00\x00\x00"
        else:
            v = _enc_value(tag, val)
            out += bytes([tag]) + struct.pack(">H", len(n)) + n + struct.pack(">H", len(v)) + v
    return bytes(out)


def build_response(status: int, request_id: int, groups: list, version: tuple[int, int] = (2, 0), document: bytes = b"") -> bytes:
    out = bytearray(struct.pack(">BBHI", version[0], version[1], status, request_id))
    for gtag, attrs in groups:
        out.append(gtag)
        for attr_name, values in attrs:
            out += _enc_attr(attr_name, values)
    out.append(G_END)
    return bytes(out) + document


# --------------------------------------------------------------------------- decoding
class IPPError(ValueError):
    pass


@dataclass
class Request:
    version: tuple[int, int]
    operation: int
    request_id: int
    groups: list = field(default_factory=list)       # [(group_tag, {name: [(tag, value), ...]})]
    document: bytes = b""

    def attrs(self, group: int) -> dict:
        out: dict = {}
        for gtag, d in self.groups:
            if gtag == group:
                out.update(d)
        return out

    def get(self, group: int, attr_name: str, default: Any = None) -> Any:
        v = self.attrs(group).get(attr_name)
        return v[0][1] if v else default

    def get_all(self, group: int, attr_name: str) -> list:
        return [x[1] for x in self.attrs(group).get(attr_name, [])]


def _read_value(data: bytes, pos: int, tag: int, length: int):
    raw = data[pos:pos + length]
    if len(raw) != length:
        raise IPPError("truncated value")
    if tag in (INTEGER, ENUM):
        if length != 4:
            raise IPPError("bad integer")
        return struct.unpack(">i", raw)[0]
    if tag == BOOLEAN:
        return bool(raw[0]) if raw else False
    if tag == RANGE and length == 8:
        return Rng(*struct.unpack(">ii", raw))
    if tag == RESOLUTION and length == 9:
        x, y, u = struct.unpack(">iiB", raw)
        return Res(x, y, u)
    if tag == DATETIME:
        return raw
    if tag == OCTETS:
        return raw
    if tag in (TEXTLANG, NAMELANG) and length >= 4:
        l1 = struct.unpack(">H", raw[:2])[0]
        return raw[2 + l1 + 2:].decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def _read_collection(data: bytes, pos: int, depth: int):
    """After a begCollection value: members up to the endCollection.  Returns ([(member_name, [values])], new_pos)."""
    if depth > MAX_COLLECTION_DEPTH:
        raise IPPError("collection too deep")
    members: list = []
    current: list | None = None
    while True:
        if pos + 5 > len(data):
            raise IPPError("truncated collection")
        tag = data[pos]
        nlen = struct.unpack(">H", data[pos + 1:pos + 3])[0]
        pos += 3 + nlen
        vlen = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if tag == ENDCOLL:
            pos += vlen
            return members, pos
        if tag == MEMBER:
            mname = data[pos:pos + vlen].decode("utf-8", "replace")
            pos += vlen
            current = []
            members.append((mname, current))
            continue
        if current is None:
            raise IPPError("collection value without a member name")
        if tag == BEGCOLL:
            pos += vlen
            sub, pos = _read_collection(data, pos, depth + 1)
            current.append((BEGCOLL, sub))
        else:
            current.append((tag, _read_value(data, pos, tag, vlen)))
            pos += vlen


def parse_request(data: bytes) -> Request:
    if len(data) < 9:
        raise IPPError("too short")
    major, minor, op, rid = struct.unpack(">BBHI", data[:8])
    req = Request((major, minor), op, rid)
    pos = 8
    group: dict | None = None
    last: list | None = None
    while True:
        if pos >= len(data):
            raise IPPError("no end-of-attributes")
        tag = data[pos]
        if tag == G_END:
            pos += 1
            break
        if tag <= 0x0F:                                     # a group delimiter
            group = {}
            req.groups.append((tag, group))
            last = None
            pos += 1
            continue
        if group is None or pos + 5 > len(data):
            raise IPPError("attribute outside a group")
        nlen = struct.unpack(">H", data[pos + 1:pos + 3])[0]
        attr_name = data[pos + 3:pos + 3 + nlen].decode("utf-8", "replace")
        pos += 3 + nlen
        vlen = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if nlen:
            last = group.setdefault(attr_name, [])
        elif last is None:
            raise IPPError("value without an attribute name")
        if tag == BEGCOLL:
            pos += vlen
            members, pos = _read_collection(data, pos, 1)
            last.append((BEGCOLL, members))
        elif tag in (UNSUPPORTED, UNKNOWN, NOVALUE) or tag > 0x4F and tag != MEMBER:
            last.append((tag, None))
            pos += vlen
        else:
            last.append((tag, _read_value(data, pos, tag, vlen)))
            pos += vlen
    req.document = data[pos:]
    return req


def collection_to_dict(members: list) -> dict:
    """A parsed collection -> {member-name: first value (collections become dicts too)}."""
    out = {}
    for mname, values in members:
        if not values:
            continue
        tag, val = values[0]
        out[mname] = collection_to_dict(val) if tag == BEGCOLL else val
    return out
