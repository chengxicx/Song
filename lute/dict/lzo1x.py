"""
Pure-Python LZO1X decompression.

Ported from the Dart implementation in LuteForMobile
(``lib/core/dict/lzo1x.dart``), which is itself a port of the Linux
kernel's ``lib/lzo/lzo1x_decompress_safe.c`` minus the kernel-specific
bitstream-version prefix and the zero-run special case: the LZO blocks
inside an MDX file are plain lzo1x output.

``python-lzo``'s calling convention is a 5-byte synthetic header
(0xf0 + big-endian uint32 of the original length) followed by the
compressed data; its C layer strips that header and calls
``lzo1x_decompress``.  This function takes the already-stripped
``compressed`` plus the decompressed size from the MDX block info, which
is equivalent -- and it keeps Lute's MDX import free of a C extension
that is awkward to build on Windows.
"""


def lzo1x_decompress(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    compressed, decompressed_size
):
    """
    Decompress one lzo1x block, returning ``decompressed_size`` bytes.

    Raises ValueError on any malformed input (overruns, a short result),
    which the MDX reader surfaces as a broken file.
    """
    ip_end = len(compressed)
    out = bytearray(decompressed_size)
    ip = 0
    op = 0
    state = 0

    def copy_literal(t):
        nonlocal ip, op
        if ip + t > ip_end:
            raise ValueError("LZO: input overrun")
        if op + t > decompressed_size:
            raise ValueError("LZO: output overrun")
        out[op : op + t] = compressed[ip : ip + t]
        ip += t
        op += t

    def copy_match(t, m_pos):
        nonlocal op
        if m_pos < 0:
            raise ValueError("LZO: match offset before start of output")
        if op + t > decompressed_size:
            raise ValueError("LZO: output overrun")
        # LZO allows overlapping copies (the length 2 and 3 near
        # matches), so this has to be byte-by-byte.
        for i in range(t):
            out[op] = out[m_pos + i]
            op += 1

    def match_next(n):
        nonlocal ip, op, state
        state = n
        if ip + n > ip_end:
            raise ValueError("LZO: input overrun")
        out[op : op + n] = compressed[ip : ip + n]
        ip += n
        op += n

    if ip_end < 3:
        raise ValueError("LZO: input too short")

    first = True
    eof = False
    while not eof:
        t = 0
        if first:
            first = False
            t0 = compressed[ip]
            if t0 > 17:
                ip += 1
                lit = t0 - 17
                if lit < 4:
                    # The kernel's "goto match_next": copy the literals,
                    # then carry on in the main loop.
                    match_next(lit)
                    continue
                copy_literal(lit)
                state = 4
                continue
            # First byte <= 17: don't advance, treat it as an ordinary
            # control byte in the main loop.
            t = t0
        else:
            if ip >= ip_end:
                break
            t = compressed[ip]
            ip += 1

        if t < 16:
            if state == 0:
                if t == 0:
                    zeros = 0
                    while ip < ip_end and compressed[ip] == 0:
                        ip += 1
                        zeros += 1
                    if ip >= ip_end:
                        raise ValueError("LZO: input overrun")
                    t += ((zeros << 8) - zeros) + 15 + compressed[ip]
                    ip += 1
                t += 3
                copy_literal(t)
                state = 4
                continue
            if state != 4:
                nxt = t & 3
                m_pos = op - 1
                m_pos -= t >> 2
                if ip + 1 > ip_end:
                    raise ValueError("LZO: input overrun")
                m_pos -= compressed[ip] << 2
                ip += 1
                copy_match(2, m_pos)
                match_next(nxt)
                continue
            nxt = t & 3
            m_pos = op - (1 + 0x0800)
            m_pos -= t >> 2
            if ip + 1 > ip_end:
                raise ValueError("LZO: input overrun")
            m_pos -= compressed[ip] << 2
            ip += 1
            copy_match(3, m_pos)
            match_next(nxt)
            continue

        if t >= 64:
            nxt = t & 3
            m_pos = op - 1
            m_pos -= (t >> 2) & 7
            if ip + 1 > ip_end:
                raise ValueError("LZO: input overrun")
            m_pos -= compressed[ip] << 3
            ip += 1
            t = (t >> 5) - 1 + 2
            copy_match(t, m_pos)
            match_next(nxt)
            continue

        if t >= 32:
            t = (t & 31) + 2
            if t == 2:
                zeros = 0
                while ip < ip_end and compressed[ip] == 0:
                    ip += 1
                    zeros += 1
                if ip >= ip_end:
                    raise ValueError("LZO: input overrun")
                t += ((zeros << 8) - zeros) + 31 + compressed[ip]
                ip += 1
            if ip + 2 > ip_end:
                raise ValueError("LZO: input overrun")
            m_pos = op - 1
            nxt = compressed[ip] | (compressed[ip + 1] << 8)
            ip += 2
            m_pos -= nxt >> 2
            nxt &= 3
            copy_match(t, m_pos)
            match_next(nxt)
            continue

        # 16 <= t < 32
        if ip + 2 > ip_end:
            raise ValueError("LZO: input overrun")
        nxt = compressed[ip] | (compressed[ip + 1] << 8)
        m_pos = op
        m_pos -= (t & 8) << 11
        t = (t & 7) + 2
        if t == 2:
            zeros = 0
            while ip < ip_end and compressed[ip] == 0:
                ip += 1
                zeros += 1
            if ip >= ip_end:
                raise ValueError("LZO: input overrun")
            t += ((zeros << 8) - zeros) + 7 + compressed[ip]
            ip += 1
            if ip + 2 > ip_end:
                raise ValueError("LZO: input overrun")
            nxt = compressed[ip] | (compressed[ip + 1] << 8)
        ip += 2
        m_pos -= nxt >> 2
        nxt &= 3
        if m_pos == op:
            eof = True
            break
        m_pos -= 0x4000
        copy_match(t, m_pos)
        match_next(nxt)

    if op != decompressed_size:
        msg = f"LZO: decompressed length mismatch ({op} != {decompressed_size})"
        raise ValueError(msg)
    return bytes(out)
