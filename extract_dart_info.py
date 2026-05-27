import io
import os
import re
import requests
import sys
import zipfile
import zlib
from struct import unpack

from elftools.elf.elffile import ELFFile


class DartInfoError(Exception):
    pass


DART_SDK_REVISION_FILE = b'dart-sdk/revision'
DART_SDK_VERSION_FILE = b'dart-sdk/version'
ZIP_LOCAL_FILE_HEADER = 0x04034b50
ZIP_CENTRAL_DIR_HEADER = 0x02014b50
ZIP_END_CENTRAL_DIR = 0x06054b50
ZIP_TAIL_SIZE = 256 * 1024

# TODO: support both ELF and Mach-O file
def extract_snapshot_hash_flags(libapp_file):
    with open(libapp_file, 'rb') as f:
        elf = ELFFile(f)
        # find "_kDartVmSnapshotData" symbol
        dynsym = elf.get_section_by_name('.dynsym')
        if dynsym is None:
            raise DartInfoError(f"Cannot find .dynsym in {libapp_file}")
        symbols = dynsym.get_symbol_by_name('_kDartVmSnapshotData')
        if not symbols:
            raise DartInfoError(f"Cannot find _kDartVmSnapshotData in {libapp_file}")
        sym = symbols[0]
        #section = elf.get_section(sym['st_shndx'])
        if sym['st_size'] <= 128:
            raise DartInfoError(f"_kDartVmSnapshotData is too small in {libapp_file}")
        f.seek(sym['st_value']+20)
        snapshot_hash = f.read(32).decode()
        data = f.read(256) # should be enough
        zero_pos = data.find(b'\0')
        if zero_pos == -1:
            raise DartInfoError(f"Cannot find snapshot flags terminator in {libapp_file}")
        flags = data[:zero_pos].decode().strip().split(' ')
    
    return snapshot_hash, flags

def extract_libflutter_info(libflutter_file):
    with open(libflutter_file, 'rb') as f:
        elf = ELFFile(f)
        if elf.header.e_machine == 'EM_AARCH64': # 183
            arch = 'arm64'
        elif elf.header.e_machine == 'EM_X86_64': # 62
            arch = 'x64'
        else:
            raise DartInfoError(f"Unsupported architecture: {elf.header.e_machine}")

        section = elf.get_section_by_name('.rodata')
        if section is None:
            raise DartInfoError(f"Cannot find .rodata in {libflutter_file}")
        data = section.data()
        
        sha_hashes = re.findall(b'\x00([a-f\\d]{40})(?=\x00)', data)
        #print(sha_hashes)
        # all possible engine ids
        engine_ids = [ h.decode() for h in sha_hashes ]
        if len(engine_ids) != 2:
            raise DartInfoError(f'Expected 2 engine hashes, found {len(engine_ids)}: {", ".join(engine_ids)}')
        
        # beta/dev version of flutter might not use stable dart version (we can get dart version from sdk with found engine_id)
        # support stable, beta and dev channels
        m = re.search(br'\x00([\d\w\.-]+) \((stable|beta|dev)\)', data)
        if m is None:
            dart_version = None
        else:
            dart_version = m.group(1).decode()
        
    return engine_ids, dart_version, arch, 'android'

def get_dart_sdk_url_size(engine_ids):
    #url = f'https://storage.googleapis.com/dart-archive/channels/stable/release/3.0.3/sdk/dartsdk-windows-x64-release.zip'
    for engine_id in engine_ids:
        url = f'https://storage.googleapis.com/flutter_infra_release/flutter/{engine_id}/dart-sdk-windows-x64.zip'
        resp = requests.head(url, timeout=30)
        if resp.status_code == 200:
           sdk_size = int(resp.headers['Content-Length'])
           return engine_id, url, sdk_size
    
    return None, None, None

def read_http_range(url, start, end):
    with requests.get(url, headers={"Range": f"bytes={start}-{end}"}, stream=True, timeout=30) as r:
        if r.status_code // 100 != 2:
            return None

        chunks = []
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                chunks.append(chunk)
        data = b''.join(chunks)
        if r.status_code == 200 and start > 0 and len(data) > end:
            return data[start:end + 1]
        return data


def decode_zip_member(comp_method, data):
    if comp_method == zipfile.ZIP_STORED:
        return data
    if comp_method == zipfile.ZIP_DEFLATED:
        return zlib.decompress(data, wbits=-zlib.MAX_WBITS)
    raise DartInfoError(f'Unexpected compression method: {comp_method}')


def parse_local_zip_entries(data):
    commit_id = None
    dart_version = None
    fp = io.BytesIO(data)

    while fp.tell() <= len(data) - 30 and (commit_id is None or dart_version is None):
        pos = fp.tell()
        sig_data = fp.read(4)
        if len(sig_data) != 4:
            break
        sig, = unpack('<I', sig_data)
        if sig != ZIP_LOCAL_FILE_HEADER:
            break

        _, _, comp_method, _, _, _, compress_size, _, filename_len, extra_len = unpack('<HHHHHIIIHH', fp.read(26))
        filename = fp.read(filename_len)
        if extra_len > 0:
            fp.seek(extra_len, io.SEEK_CUR)

        if fp.tell() + compress_size > len(data):
            fp.seek(pos)
            break

        compressed = fp.read(compress_size)
        if filename == DART_SDK_REVISION_FILE:
            commit_id = decode_zip_member(comp_method, compressed).decode().strip()
        elif filename == DART_SDK_VERSION_FILE:
            dart_version = decode_zip_member(comp_method, compressed).decode().strip()

    return commit_id, dart_version


def find_zip_eocd(data):
    min_eocd_len = 22
    pos = data.rfind(b'PK\x05\x06')
    if pos == -1 or len(data) - pos < min_eocd_len:
        return None
    return pos


def parse_central_directory(data):
    entries = {}
    fp = io.BytesIO(data)

    while fp.tell() <= len(data) - 46:
        sig, = unpack('<I', fp.read(4))
        if sig != ZIP_CENTRAL_DIR_HEADER:
            break

        fields = unpack('<HHHHHHIIIHHHHHII', fp.read(42))
        comp_method = fields[3]
        compress_size = fields[7]
        filename_len = fields[9]
        extra_len = fields[10]
        comment_len = fields[11]
        local_header_offset = fields[15]
        filename = fp.read(filename_len)
        if extra_len > 0:
            fp.seek(extra_len, io.SEEK_CUR)
        if comment_len > 0:
            fp.seek(comment_len, io.SEEK_CUR)

        if filename in (DART_SDK_REVISION_FILE, DART_SDK_VERSION_FILE):
            entries[filename] = (local_header_offset, comp_method, compress_size)

    return entries


def read_remote_zip_member(url, local_header_offset, comp_method, compress_size):
    header = read_http_range(url, local_header_offset, local_header_offset + 29)
    if header is None or len(header) < 30:
        return None

    sig, = unpack('<I', header[:4])
    if sig != ZIP_LOCAL_FILE_HEADER:
        return None

    _, _, local_comp_method, _, _, _, _, _, filename_len, extra_len = unpack('<HHHHHIIIHH', header[4:30])
    if local_comp_method != comp_method:
        raise DartInfoError('Compression method mismatch between local and central zip headers')

    data_start = local_header_offset + 30 + filename_len + extra_len
    compressed = read_http_range(url, data_start, data_start + compress_size - 1)
    if compressed is None or len(compressed) < compress_size:
        return None

    return decode_zip_member(comp_method, compressed[:compress_size]).decode().strip()


def get_dart_commit_from_central_directory(url, sdk_size):
    tail_start = max(0, sdk_size - ZIP_TAIL_SIZE)
    tail = read_http_range(url, tail_start, sdk_size - 1)
    if tail is None:
        return None, None

    eocd_pos = find_zip_eocd(tail)
    if eocd_pos is None:
        return None, None

    eocd = tail[eocd_pos:eocd_pos + 22]
    _, _, _, _, _, central_size, central_offset, _ = unpack('<IHHHHIIH', eocd)
    central_end = central_offset + central_size

    if central_offset >= tail_start and central_end <= sdk_size:
        start = central_offset - tail_start
        central = tail[start:start + central_size]
    else:
        central = read_http_range(url, central_offset, central_end - 1)
        if central is None:
            return None, None

    entries = parse_central_directory(central)
    commit_id = None
    dart_version = None

    if DART_SDK_REVISION_FILE in entries:
        commit_id = read_remote_zip_member(url, *entries[DART_SDK_REVISION_FILE])
    if DART_SDK_VERSION_FILE in entries:
        dart_version = read_remote_zip_member(url, *entries[DART_SDK_VERSION_FILE])

    return commit_id, dart_version


def get_dart_commit(url, sdk_size=None):
    # in downloaded zip
    # * dart-sdk/revision - the dart commit id of https://github.com/dart-lang/sdk/
    # * dart-sdk/version  - the dart version
    # revision and version zip file records should be in first 4096 bytes
    # using stream in case a server does not support range
    commit_id = None
    dart_version = None
    if url is None:
        return None, None

    head = read_http_range(url, 0, 4095)
    if head is not None:
        commit_id, dart_version = parse_local_zip_entries(head)

    if (commit_id is None or dart_version is None) and sdk_size is not None:
        commit_id, dart_version = get_dart_commit_from_central_directory(url, sdk_size)

    return commit_id, dart_version

def extract_dart_info(libapp_file: str, libflutter_file: str):
    snapshot_hash, flags = extract_snapshot_hash_flags(libapp_file)
    #print('snapshot hash', snapshot_hash)
    #print(flags)

    engine_ids, dart_version, arch, os_name = extract_libflutter_info(libflutter_file)
    # print('possible engine ids', engine_ids)
    # print('dart version', dart_version)

    if dart_version is None:
        engine_id, sdk_url, sdk_size = get_dart_sdk_url_size(engine_ids)
        # print(engine_id)
        # print(sdk_url)
        # print(sdk_size)

        commit_id, dart_version = get_dart_commit(sdk_url, sdk_size)
        if dart_version is None:
            raise DartInfoError(f'Cannot determine Dart version from engine hashes: {", ".join(engine_ids)}')
        # print(commit_id)
        # print(dart_version)
        #assert dart_version == dart_version_sdk
    
    # TODO: os (android or ios) and architecture (arm64 or x64)
    return dart_version, snapshot_hash, flags, arch, os_name


if __name__ == "__main__":
    libdir = sys.argv[1]
    libapp_file = os.path.join(libdir, 'libapp.so')
    libflutter_file = os.path.join(libdir, 'libflutter.so')

    print(extract_dart_info(libapp_file, libflutter_file))
