#!/usr/bin/python3
import argparse
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT_DIR)

from dartvm_fetch_build import DartLibInfo, fetch_and_build
from blutter import (
    BlutterInput,
    cmake_blutter,
    dartvm_package_missing,
    get_entry_value,
    manifest_artifact_key,
    relative_manifest_path,
)


def parse_bool(value: str):
    normalized = value.lower()
    if normalized in ('1', 'true', 'yes', 'on'):
        return True
    if normalized in ('0', 'false', 'no', 'off'):
        return False
    raise argparse.ArgumentTypeError('expected true or false')


def load_manifest(path: str):
    if not os.path.isfile(path):
        return {'entries': []}
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get('entries'), list):
        raise ValueError(f'Invalid manifest: {path}')
    return data


def write_manifest(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.write('\n')


def make_manifest_entry(input: BlutterInput, manifest_file: str, snapshot_hash: str):
    entry = {
        'version': input.dart_info.version,
        'snapshot_hash': snapshot_hash,
        'target_os': input.dart_info.os_name,
        'target_arch': input.dart_info.arch,
        'compressed_pointers': input.dart_info.has_compressed_ptrs,
        'no_analysis': input.no_analysis,
        'host': sys.platform,
        'lib_name': input.dart_info.lib_name,
        'blutter_name': input.blutter_name,
        'generated_by': 'scripts/build_prebuilt_matrix.py',
        'blutter': relative_manifest_path(manifest_file, input.blutter_file),
        'dartvm_package': relative_manifest_path(manifest_file, os.path.join(ROOT_DIR, 'packages')),
    }
    return entry


def upsert_manifest_entry(manifest_file: str, entry):
    data = load_manifest(manifest_file)
    new_key = manifest_artifact_key(entry)
    entries = [
        existing for existing in data.get('entries', [])
        if isinstance(existing, dict) and manifest_artifact_key(existing) != new_key
    ]
    entries.append(entry)
    entries.sort(key=lambda item: (
        str(get_entry_value(item, 'version', 'dart_version')),
        str(get_entry_value(item, 'snapshot_hash', 'snapshot')),
        str(get_entry_value(item, 'target_os', 'os_name', 'os')),
        str(get_entry_value(item, 'target_arch', 'arch')),
        str(get_entry_value(item, 'blutter_name')),
    ))
    data['entries'] = entries
    write_manifest(manifest_file, data)


def build_one(args, version: str):
    build_snapshot_hash = None if args.snapshot_hash in ('', '*') else args.snapshot_hash
    dart_info = DartLibInfo(
        version,
        args.target_os,
        args.target_arch,
        args.compressed_pointers,
        build_snapshot_hash,
    )
    input = BlutterInput(os.devnull, dart_info, os.path.join(ROOT_DIR, 'out'), args.rebuild, False, args.no_analysis)

    if args.dry_run:
        print(f'[dry-run] {input.blutter_name}')
        return

    missing_package = dartvm_package_missing(dart_info)
    if args.rebuild or missing_package:
        print(f'Building Dart VM package: {dart_info.lib_name}')
        fetch_and_build(dart_info)
    else:
        print(f'Dart VM package exists: {dart_info.lib_name}')

    if args.rebuild or not os.path.isfile(input.blutter_file):
        print(f'Building Blutter binary: {input.blutter_name}')
        cmake_blutter(input)
    else:
        print(f'Blutter binary exists: {input.blutter_name}')

    missing_package = dartvm_package_missing(dart_info)
    if missing_package:
        raise RuntimeError('Dart VM package is incomplete after build:\n  ' + '\n  '.join(missing_package))
    if not os.path.isfile(input.blutter_file):
        raise RuntimeError(f'Blutter binary is missing after build: {input.blutter_file}')

    entry = make_manifest_entry(input, args.manifest, args.snapshot_hash)
    upsert_manifest_entry(args.manifest, entry)
    print(f'Updated manifest: {args.manifest}')


def main():
    parser = argparse.ArgumentParser(description='Build a local Blutter prebuilt artifact matrix')
    parser.add_argument('--versions', nargs='+', required=True, help='Dart versions to prebuild, for example 3.10.0 3.11.0 3.12.1')
    parser.add_argument('--target-os', default='android', choices=('android', 'ios'))
    parser.add_argument('--target-arch', default='arm64', choices=('arm64', 'x64'))
    parser.add_argument('--compressed-pointers', type=parse_bool, default=True)
    parser.add_argument('--snapshot-hash', default='*', help='Manifest snapshot hash key. Use "*" for a generic prebuild entry.')
    parser.add_argument('--no-analysis', action='store_true', default=False)
    parser.add_argument('--rebuild', action='store_true', default=False)
    parser.add_argument('--dry-run', action='store_true', default=False)
    parser.add_argument('--manifest', default=os.path.join(ROOT_DIR, 'prebuilt', 'manifest.json'))
    args = parser.parse_args()

    for version in args.versions:
        build_one(args, version)


if __name__ == '__main__':
    main()
