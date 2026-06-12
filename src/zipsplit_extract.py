"""Partial extractor for 7z-style split zip archives (bins.zip.001 ... bins.zip.NNN).

The concatenation of all volumes is one valid (Zip64) zip. We only have a subset
of volumes locally, so:
  1. Parse the central directory from the final volume(s).
  2. Extract entries whose [local_header_offset, offset + header + compressed_size)
     lies entirely within locally available volumes.

Usage:
  python zipsplit_extract.py list   --vols DIR --sizes bins_volume_sizes.json [--limit N]
  python zipsplit_extract.py extract --vols DIR --sizes bins_volume_sizes.json \
         --out OUTDIR (--names name1 name2 ... | --available [--limit N] [--filter SUBSTR])
"""

import argparse
import json
import os
import struct
import sys
import zlib

EOCD_SIG = b"PK\x05\x06"
EOCD64_SIG = b"PK\x06\x06"
EOCD64_LOC_SIG = b"PK\x06\x07"
CDH_SIG = b"PK\x01\x02"
LFH_SIG = b"PK\x03\x04"


class SplitVolumes:
    """Byte-addressable view over the concatenated archive, backed by whichever
    volumes exist locally."""

    def __init__(self, vol_dir, sizes_json):
        sizes = json.load(open(sizes_json))
        self.names = sorted(sizes)
        self.sizes = [sizes[n] for n in self.names]
        self.starts = []
        off = 0
        for s in self.sizes:
            self.starts.append(off)
            off += s
        self.total = off
        self.vol_dir = vol_dir
        self.local = {
            n: os.path.join(vol_dir, n)
            for n in self.names
            if os.path.exists(os.path.join(vol_dir, n))
        }
        # a volume still being downloaded is not usable yet — drop it
        for n in list(self.local):
            actual = os.path.getsize(self.local[n])
            if actual != sizes[n]:
                print(f"note: {n} incomplete ({actual}/{sizes[n]}), skipping", file=sys.stderr)
                del self.local[n]

    def vol_index(self, goff):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= goff:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def has_range(self, goff, length):
        end = goff + length
        i = self.vol_index(goff)
        while True:
            if self.names[i] not in self.local:
                return False
            vol_end = self.starts[i] + self.sizes[i]
            if end <= vol_end:
                return True
            i += 1
            if i >= len(self.names):
                return False

    def read(self, goff, length):
        out = bytearray()
        remaining = length
        pos = goff
        while remaining > 0:
            i = self.vol_index(pos)
            name = self.names[i]
            if name not in self.local:
                raise IOError(f"volume {name} not available locally (need offset {pos})")
            local_off = pos - self.starts[i]
            with open(self.local[name], "rb") as f:
                f.seek(local_off)
                chunk = f.read(min(remaining, self.sizes[i] - local_off))
            if not chunk:
                raise IOError(f"short read in {name}")
            out += chunk
            pos += len(chunk)
            remaining -= len(chunk)
        return bytes(out)


def parse_central_directory(vols):
    """Locate EOCD (+Zip64) at the end of the archive and yield CD entries."""
    tail_len = min(70_000, vols.sizes[-1])
    tail_goff = vols.total - tail_len
    tail = vols.read(tail_goff, tail_len)
    eocd_pos = tail.rfind(EOCD_SIG)
    if eocd_pos < 0:
        raise RuntimeError("EOCD not found in last volume tail")
    (cd_count, cd_size, cd_offset) = struct.unpack(
        "<HIi", tail[eocd_pos + 10 : eocd_pos + 20][:2] + tail[eocd_pos + 12 : eocd_pos + 20]
    )[0:3] if False else (
        struct.unpack("<H", tail[eocd_pos + 10 : eocd_pos + 12])[0],
        struct.unpack("<I", tail[eocd_pos + 12 : eocd_pos + 16])[0],
        struct.unpack("<I", tail[eocd_pos + 16 : eocd_pos + 20])[0],
    )
    if cd_offset == 0xFFFFFFFF or cd_count == 0xFFFF or cd_size == 0xFFFFFFFF:
        loc_pos = tail.rfind(EOCD64_LOC_SIG, 0, eocd_pos)
        if loc_pos < 0:
            raise RuntimeError("Zip64 EOCD locator not found")
        eocd64_goff = struct.unpack("<Q", tail[loc_pos + 8 : loc_pos + 16])[0]
        rec = vols.read(eocd64_goff, 56)
        if rec[:4] != EOCD64_SIG:
            raise RuntimeError("bad Zip64 EOCD signature")
        cd_count = struct.unpack("<Q", rec[32:40])[0]
        cd_size = struct.unpack("<Q", rec[40:48])[0]
        cd_offset = struct.unpack("<Q", rec[48:56])[0]

    cd = vols.read(cd_offset, cd_size)
    entries = []
    p = 0
    while p + 4 <= len(cd) and cd[p : p + 4] == CDH_SIG:
        (method,) = struct.unpack("<H", cd[p + 10 : p + 12])
        comp, uncomp = struct.unpack("<II", cd[p + 20 : p + 28])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28 : p + 34])
        (lho,) = struct.unpack("<I", cd[p + 42 : p + 46])
        name = cd[p + 46 : p + 46 + nlen].decode("utf-8", "replace")
        extra = cd[p + 46 + nlen : p + 46 + nlen + elen]
        # Zip64 extra field overrides 0xFFFFFFFF placeholders, in fixed order:
        # uncomp, comp, lho (only those that were maxed out)
        q = 0
        while q + 4 <= len(extra):
            hid, hsize = struct.unpack("<HH", extra[q : q + 4])
            if hid == 0x0001:
                body = extra[q + 4 : q + 4 + hsize]
                r = 0
                if uncomp == 0xFFFFFFFF:
                    uncomp = struct.unpack("<Q", body[r : r + 8])[0]; r += 8
                if comp == 0xFFFFFFFF:
                    comp = struct.unpack("<Q", body[r : r + 8])[0]; r += 8
                if lho == 0xFFFFFFFF:
                    lho = struct.unpack("<Q", body[r : r + 8])[0]; r += 8
            q += 4 + hsize
        entries.append(
            {"name": name, "method": method, "comp": comp, "uncomp": uncomp, "offset": lho}
        )
        p += 46 + nlen + elen + clen
    if len(entries) != cd_count:
        print(f"warning: parsed {len(entries)} entries, EOCD says {cd_count}", file=sys.stderr)
    return entries


def entry_available(vols, e):
    # local header (30 bytes + name + extra; extra can differ from CD copy, so
    # over-budget 1KB for the header) + compressed payload
    return vols.has_range(e["offset"], 30 + 1024 + e["comp"])


def extract_entry(vols, e, out_dir):
    hdr = vols.read(e["offset"], 30)
    if hdr[:4] != LFH_SIG:
        raise RuntimeError(f"{e['name']}: bad local header signature")
    nlen, elen = struct.unpack("<HH", hdr[26:30])
    data_off = e["offset"] + 30 + nlen + elen
    payload = vols.read(data_off, e["comp"])
    if e["method"] == 0:
        raw = payload
    elif e["method"] == 8:
        raw = zlib.decompress(payload, -15)
    else:
        raise RuntimeError(f"{e['name']}: unsupported method {e['method']}")
    if len(raw) != e["uncomp"]:
        raise RuntimeError(f"{e['name']}: size mismatch {len(raw)} != {e['uncomp']}")
    dest = os.path.join(out_dir, e["name"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(raw)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "extract"])
    ap.add_argument("--vols", required=True)
    ap.add_argument("--sizes", required=True)
    ap.add_argument("--out", default="extracted")
    ap.add_argument("--names", nargs="*")
    ap.add_argument("--available", action="store_true")
    ap.add_argument("--filter", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    vols = SplitVolumes(args.vols, args.sizes)
    print(f"volumes local: {len(vols.local)}/{len(vols.names)}, archive total {vols.total/2**30:.2f} GiB", file=sys.stderr)
    entries = parse_central_directory(vols)
    print(f"central directory: {len(entries)} entries", file=sys.stderr)

    if args.cmd == "list":
        n = 0
        for e in entries:
            avail = entry_available(vols, e)
            if args.filter and args.filter not in e["name"]:
                continue
            if args.available and not avail:
                continue
            print(f"{'+' if avail else '-'} {e['offset']:>13} {e['comp']:>10} {e['name']}")
            n += 1
            if args.limit and n >= args.limit:
                break
        return

    targets = []
    if args.names:
        want = set(args.names)
        targets = [e for e in entries if e["name"] in want]
    elif args.available:
        for e in entries:
            if e["uncomp"] == 0 or e["name"].endswith("/"):
                continue
            if args.filter and args.filter not in e["name"]:
                continue
            if entry_available(vols, e):
                targets.append(e)
                if args.limit and len(targets) >= args.limit:
                    break
    ok = fail = 0
    for e in targets:
        try:
            dest = extract_entry(vols, e, args.out)
            ok += 1
            print(dest)
        except Exception as ex:
            fail += 1
            print(f"FAIL {e['name']}: {ex}", file=sys.stderr)
    print(f"extracted {ok}, failed {fail}", file=sys.stderr)


if __name__ == "__main__":
    main()
