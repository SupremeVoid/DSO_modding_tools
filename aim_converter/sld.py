"""SLD -- the Ascaron general-purpose compressor used by `.aim` (IMSL* chunks).

Reverse engineered from AIM20.dll, section ASSEG, function at RVA 0x8491c.
It is a byte-oriented LZ77 with an 8-bucket offset code whose bucket widths are
carried in the stream header as nibble deltas. No entropy coding at all -- the
flat byte histogram that suggested Huffman was just densely packed bit fields.

Stream layout (the pointer the engine passes to the decompressor):
    u32 raw_size          decompressed size of this block
    u32 flags             bit31 = stored (not compressed), bit30 = XOR 0x35
    u32 widths            8 nibbles, low nibble first: cumulative bucket widths
    ...                   bit stream, read as 32-bit LE words, LSB first

Bucket tables, i = 0..7:
    bits[i] = bits[i-1] + nibble[i]        (bits[-1] = 0)
    mask[i] = (1 << bits[i]) - 1
    base[i] = base[i-1] + mask[i-1] + 1    (base[0] = 0)

Token stream:
    bit 0  -> literal: next 8 bits are the byte
    bit 1  -> match:   3 bits bucket, then bits[bucket] bits of value;
                       distance = base[bucket] + value + 1
                       length   = 2 + sum of an escalating field: read k bits
                                  for k = 2, 3, 4, ... and keep going while the
                                  value read is all ones
    The copy is byte-by-byte, so distance < length is a legal RLE run.
"""
import struct

MASKS = [(1 << i) - 1 for i in range(33)]


class _Bits:
    """Faithful port of the 32-bit LSB-first reader in ASSEG."""
    __slots__ = ('d', 'p', 'buf', 'n')

    def __init__(self, data, pos):
        self.d = data
        self.p = pos
        self.buf = self._word()
        self.n = 32

    def _word(self):
        if self.p + 4 <= len(self.d):
            v = struct.unpack_from('<I', self.d, self.p)[0]
        else:                                   # tolerate a short final word
            v = int.from_bytes(self.d[self.p:self.p + 4].ljust(4, b'\0'), 'little')
        self.p += 4
        return v

    def flag(self):
        b = self.buf & 1
        self.buf >>= 1
        return b

    def take(self, k):
        """Consume k bits; the caller has already had one bit removed by flag()."""
        self.n -= 1
        if self.n == 0:
            self.buf = self._word()
            self.n = 32
        v = self.buf & MASKS[k]
        self.buf >>= k
        self.n -= k
        if self.n <= 0:                          # refill, splicing the new word in
            cl = k + self.n
            w = self._word()
            self.n += 32
            v |= (w << cl) & MASKS[k]
            self.buf = w >> (32 - self.n)
        return v

    def more(self, k):
        """Consume k more bits without the leading flag adjustment."""
        v = self.buf & MASKS[k]
        self.buf >>= k
        self.n -= k
        if self.n <= 0:
            cl = k + self.n
            w = self._word()
            self.n += 32
            v |= (w << cl) & MASKS[k]
            self.buf = w >> (32 - self.n)
        return v


def decompress(data, pos=0):
    raw_size, flags, widths = struct.unpack_from('<3I', data, pos)
    pos += 12
    if flags & 0x80000000:
        blob = data[pos:pos + raw_size]
        return bytes(b ^ 0x35 for b in blob) if flags & 0x40000000 else bytes(blob)

    bits, mask, base = [], [], []
    acc = 0
    for i in range(8):
        acc += widths & 0xf
        widths >>= 4
        bits.append(acc)
        mask.append((1 << acc) - 1)
        base.append(0 if i == 0 else base[i - 1] + mask[i - 1] + 1)

    br = _Bits(data, pos)
    out = bytearray()
    remaining = raw_size
    while remaining > 0:
        if br.flag() == 0:
            out.append(br.take(8))
            remaining -= 1
        else:
            cls = br.take(3)
            dist = base[cls] + br.more(bits[cls]) + 1
            length, k = 2, 1
            while True:
                k += 1
                v = br.more(k)
                length += v
                if v != MASKS[k]:
                    break
            if dist > len(out):
                raise ValueError('distance %d exceeds output %d' % (dist, len(out)))
            for _ in range(length):
                out.append(out[-dist])
            remaining -= length
    return bytes(out)
