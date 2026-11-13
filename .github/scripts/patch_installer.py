"""Patch the exact archived x64 installer, guarded by its original SHA256."""
import hashlib
from pathlib import Path
import struct
import sys

ORIGINAL_SHA256 = 'c1545743f1f62c0aad892b3ad0a7b2fb40b263a841353748d303fec461a501f2'
OLD_URL = 'https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip'
NEW_URL = 'https://github.com/mlpoki/NapCatQQ/releases/latest/download/NapCat.Shell.zip'
OBSOLETE_MIRRORS = [
    'https://ghfast.top/', 'https://ghproxy.net/', 'https://github.dpik.top/',
    'https://ghm.078465.xyz/', 'https://gh.monlor.com/', 'https://ghproxy.imciel.com/',
    'https://git.669966.xyz/', 'https://gh.acmsz.top/', 'https://gitproxy.mrhjx.cn/',
    'https://gh-proxy.com/', 'https://gh.llkk.cc/',
]


def patch_installer(source, destination):
    original = Path(source).read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise ValueError('Unexpected installer version or content')
    data = bytearray(original)
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0' or struct.unpack_from('<H', data, pe + 4)[0] != 0x8664:
        raise ValueError('Expected Windows x64 PE')
    optional = pe + 24
    sections = optional + struct.unpack_from('<H', data, pe + 20)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]

    def file_offset(rva):
        for index in range(count):
            entry = sections + index * 40
            virtual_size, address, raw_size, raw_offset = struct.unpack_from('<IIII', data, entry + 8)
            if address <= rva < address + min(virtual_size, raw_size):
                return raw_offset + rva - address
        raise ValueError(f'Unmapped RVA {rva:#x}')

    def replace(rva, expected, replacement):
        offset = file_offset(rva)
        if len(expected) != len(replacement) or data[offset:offset + len(expected)] != expected:
            raise ValueError(f'Patch context mismatch at RVA {rva:#x}')
        data[offset:offset + len(expected)] = replacement

    # Construct the first temporary wstring as the empty/direct prefix.
    replace(0x1033, bytes.fromhex('41b813000000'), bytes.fromhex('4533c0909090'))
    # The vector constructor receives [rsp+0x40, rsp+0x60), one wstring.
    # All twelve original temporaries are still constructed and destroyed normally.
    replace(0x1209, bytes.fromhex('488d85c0000000'), bytes.fromhex('488d4424609090'))
    # Update both checked concatenation length and the supplied wstring length.
    replace(0x1464e, bytes.fromhex('4883f84d'), bytes.fromhex('4883f84c'))
    replace(0x14667, bytes.fromhex('48c74424304d000000'), bytes.fromhex('48c74424304c000000'))
    old = OLD_URL.encode('utf-16-le')
    new = NEW_URL.encode('utf-16-le')
    replace(0x1ac10, old, new + bytes(len(old) - len(new)))
    for url in OBSOLETE_MIRRORS:
        encoded = url.encode('utf-16-le')
        if original.count(encoded) != 1:
            raise ValueError(f'Unexpected mirror occurrence count for {url}')
        offset = original.index(encoded)
        data[offset:offset + len(encoded)] = bytes(len(encoded))
    if OLD_URL.encode('utf-16-le') in data or data.count(new) != 1:
        raise ValueError('Download source replacement failed')
    Path(destination).write_bytes(data)
    print(f'Patched installer SHA256 {hashlib.sha256(data).hexdigest()}')


if __name__ == '__main__':
    patch_installer(sys.argv[1], sys.argv[2])
