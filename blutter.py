#!/usr/bin/python3
import argparse
import glob
import json
import mmap
import os
import shutil
import subprocess
import sys
import zipfile
import tempfile
from dartvm_fetch_build import DartLibInfo
from build_env import macos_brew_llvm_env

CMAKE_CMD = "cmake"
NINJA_CMD = "ninja"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
BIN_DIR = os.path.join(SCRIPT_DIR, 'bin')
PKG_INC_DIR = os.path.join(SCRIPT_DIR, 'packages', 'include')
PKG_LIB_DIR = os.path.join(SCRIPT_DIR, 'packages', 'lib')
BUILD_DIR = os.path.join(SCRIPT_DIR, 'build')
PREBUILT_MANIFEST_FILES = [
    os.path.join(SCRIPT_DIR, 'prebuilt', 'manifest.json'),
    os.path.join(BIN_DIR, 'manifest.json'),
]
LOCAL_PREBUILT_MANIFEST_FILE = os.path.join(BIN_DIR, 'manifest.json')


class BlutterInput:
    def __init__(self, libapp_path: str, dart_info: DartLibInfo, outdir: str, rebuild_blutter: bool, create_vs_sln: bool, no_analysis: bool):
        self.libapp_path = libapp_path
        self.dart_info = dart_info
        self.outdir = outdir
        self.rebuild_blutter = rebuild_blutter
        self.create_vs_sln = create_vs_sln

        vers = dart_info.version.split('.', 2)
        if int(vers[0]) == 2 and int(vers[1]) < 15:
            if not no_analysis:
                print('Dart version <2.15, force "no-analysis" option')
            no_analysis = True
        self.no_analysis = no_analysis

        # Note: null-safety is detected in blutter application, so no need another build of blutter for null-safety
        self.name_suffix = ''
        if not dart_info.has_compressed_ptrs:
            self.name_suffix += '_no-compressed-ptrs'
        if no_analysis:
            self.name_suffix += '_no-analysis'
        # derive blutter executable filename
        self.blutter_name = f'blutter_{dart_info.lib_name}{self.name_suffix}'
        self.blutter_file = os.path.join(BIN_DIR, self.blutter_name) + ('.exe' if os.name == 'nt' else '')


def dartvm_static_lib_file(dart_info: DartLibInfo):
    if os.name == 'nt':
        return os.path.join(PKG_LIB_DIR, dart_info.lib_name + '.lib')
    return os.path.join(PKG_LIB_DIR, 'lib' + dart_info.lib_name + '.a')


def dartvm_package_missing(dart_info: DartLibInfo):
    cmake_dir = os.path.join(PKG_LIB_DIR, 'cmake', dart_info.lib_name)
    required = [
        dartvm_static_lib_file(dart_info),
        os.path.join(PKG_INC_DIR, f'dartvm{dart_info.version}', 'vm', 'class_id.h'),
        os.path.join(cmake_dir, f'{dart_info.lib_name}Config.cmake'),
    ]
    return [path for path in required if not os.path.exists(path)]


def get_entry_value(entry, *names):
    for name in names:
        if name in entry:
            return entry[name]
    return None


def bool_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def prebuilt_entry_matches(entry, input: BlutterInput):
    version = get_entry_value(entry, 'version', 'dart_version')
    target_os = get_entry_value(entry, 'target_os', 'os_name', 'os')
    target_arch = get_entry_value(entry, 'target_arch', 'arch')
    if version != input.dart_info.version:
        return False
    if target_os != input.dart_info.os_name or target_arch != input.dart_info.arch:
        return False

    lib_name = get_entry_value(entry, 'lib_name', 'dartvm')
    if lib_name is not None and lib_name != input.dart_info.lib_name:
        return False

    blutter_name = get_entry_value(entry, 'blutter_name')
    if blutter_name is not None and blutter_name != input.blutter_name:
        return False

    snapshot_hash = get_entry_value(entry, 'snapshot_hash', 'snapshot')
    if input.dart_info.snapshot_hash is None:
        if snapshot_hash not in (None, '', '*'):
            return False
    elif snapshot_hash not in (input.dart_info.snapshot_hash, '*'):
        return False

    compressed = get_entry_value(entry, 'compressed_pointers', 'has_compressed_ptrs')
    if compressed is not None and bool_value(compressed) != input.dart_info.has_compressed_ptrs:
        return False

    no_analysis = get_entry_value(entry, 'no_analysis')
    if no_analysis is not None and bool_value(no_analysis) != input.no_analysis:
        return False

    host = get_entry_value(entry, 'host', 'platform')
    if host is not None and host not in (sys.platform, os.name):
        return False

    return True


def relative_manifest_path(manifest_file: str, path: str):
    return os.path.relpath(path, os.path.dirname(manifest_file))


def local_manifest_entry_key(entry):
    return (
        get_entry_value(entry, 'version', 'dart_version'),
        get_entry_value(entry, 'snapshot_hash', 'snapshot'),
        get_entry_value(entry, 'target_os', 'os_name', 'os'),
        get_entry_value(entry, 'target_arch', 'arch'),
        bool_value(get_entry_value(entry, 'compressed_pointers', 'has_compressed_ptrs')),
        bool_value(get_entry_value(entry, 'no_analysis')),
        get_entry_value(entry, 'host', 'platform'),
        get_entry_value(entry, 'blutter_name'),
    )


def manifest_artifact_key(entry):
    return (
        get_entry_value(entry, 'version', 'dart_version'),
        get_entry_value(entry, 'target_os', 'os_name', 'os'),
        get_entry_value(entry, 'target_arch', 'arch'),
        bool_value(get_entry_value(entry, 'compressed_pointers', 'has_compressed_ptrs')),
        bool_value(get_entry_value(entry, 'no_analysis')),
        get_entry_value(entry, 'host', 'platform'),
        get_entry_value(entry, 'blutter_name'),
        get_entry_value(entry, 'lib_name', 'dartvm'),
    )


def make_local_manifest_entry(input: BlutterInput):
    entry = {
        'version': input.dart_info.version,
        'target_os': input.dart_info.os_name,
        'target_arch': input.dart_info.arch,
        'compressed_pointers': input.dart_info.has_compressed_ptrs,
        'no_analysis': input.no_analysis,
        'host': sys.platform,
        'lib_name': input.dart_info.lib_name,
        'blutter_name': input.blutter_name,
        'generated_by': 'blutter.py',
    }
    if input.dart_info.snapshot_hash is not None:
        entry['snapshot_hash'] = input.dart_info.snapshot_hash
    else:
        entry['snapshot_hash'] = ''

    if os.path.isfile(input.blutter_file):
        entry['blutter'] = relative_manifest_path(LOCAL_PREBUILT_MANIFEST_FILE, input.blutter_file)
    if not dartvm_package_missing(input.dart_info):
        entry['dartvm_package'] = relative_manifest_path(LOCAL_PREBUILT_MANIFEST_FILE, os.path.join(SCRIPT_DIR, 'packages'))
    return entry


def write_local_prebuilt_manifest(input: BlutterInput):
    if os.path.isfile(input.blutter_file) or not dartvm_package_missing(input.dart_info):
        os.makedirs(os.path.dirname(LOCAL_PREBUILT_MANIFEST_FILE), exist_ok=True)
        data = {'entries': []}
        if os.path.isfile(LOCAL_PREBUILT_MANIFEST_FILE):
            try:
                with open(LOCAL_PREBUILT_MANIFEST_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get('entries'), list):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                pass

        new_entry = make_local_manifest_entry(input)
        new_key = manifest_artifact_key(new_entry)
        entries = [
            entry for entry in data.get('entries', [])
            if isinstance(entry, dict) and manifest_artifact_key(entry) != new_key
        ]
        entries.append(new_entry)
        entries.sort(key=lambda entry: (
            str(get_entry_value(entry, 'version', 'dart_version')),
            str(get_entry_value(entry, 'snapshot_hash', 'snapshot')),
            str(get_entry_value(entry, 'target_os', 'os_name', 'os')),
            str(get_entry_value(entry, 'target_arch', 'arch')),
            str(get_entry_value(entry, 'blutter_name')),
        ))
        data['entries'] = entries

        with open(LOCAL_PREBUILT_MANIFEST_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.write('\n')


def load_prebuilt_entries():
    entries = []
    checked = []
    for manifest_file in PREBUILT_MANIFEST_FILES:
        if not os.path.isfile(manifest_file):
            checked.append(f'missing manifest: {manifest_file}')
            continue

        try:
            with open(manifest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            checked.append(f'invalid manifest: {manifest_file} ({e})')
            continue

        manifest_entries = data
        if isinstance(data, dict):
            manifest_entries = data.get('entries', data.get('artifacts', []))
        if not isinstance(manifest_entries, list):
            checked.append(f'invalid manifest entries: {manifest_file}')
            continue

        for entry in manifest_entries:
            if isinstance(entry, dict):
                entries.append((manifest_file, entry))
        checked.append(f'loaded manifest: {manifest_file}')

    return entries, checked


def resolve_manifest_path(manifest_file: str, path: str):
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(os.path.dirname(manifest_file), path))


def copy_file_artifact(src: str, dst: str, executable=False):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    if executable and os.name != 'nt':
        os.chmod(dst, os.stat(dst).st_mode | 0o755)


def copy_dir_artifact(src: str, dst: str):
    shutil.copytree(src, dst, dirs_exist_ok=True)


def copy_prebuilt_path(manifest_file: str, entry, key_names, dst: str, is_dir=False, executable=False):
    src_path = get_entry_value(entry, *key_names)
    if src_path is None:
        return False

    src = resolve_manifest_path(manifest_file, src_path)
    if is_dir:
        if not os.path.isdir(src):
            return False
        copy_dir_artifact(src, dst)
    else:
        if not os.path.isfile(src):
            return False
        copy_file_artifact(src, dst, executable=executable)
    return True


def install_prebuilt_artifacts(input: BlutterInput, need_blutter: bool, need_dartvm_package: bool):
    installed = []
    entries, checked = load_prebuilt_entries()
    matched = False

    for manifest_file, entry in entries:
        if not prebuilt_entry_matches(entry, input):
            continue
        matched = True

        if need_blutter and not os.path.isfile(input.blutter_file):
            if copy_prebuilt_path(manifest_file, entry, ('blutter', 'blutter_file', 'binary'), input.blutter_file, executable=True):
                installed.append(f'installed blutter binary from {manifest_file}')

        if need_dartvm_package and dartvm_package_missing(input.dart_info):
            if copy_prebuilt_path(manifest_file, entry, ('dartvm_package', 'package_dir'), os.path.join(SCRIPT_DIR, 'packages'), is_dir=True):
                installed.append(f'installed Dart VM package from {manifest_file}')
            copy_prebuilt_path(manifest_file, entry, ('dartvm_lib', 'static_lib'), dartvm_static_lib_file(input.dart_info))
            include_dst = os.path.join(PKG_INC_DIR, f'dartvm{input.dart_info.version}')
            copy_prebuilt_path(manifest_file, entry, ('dartvm_include', 'include_dir'), include_dst, is_dir=True)
            cmake_dst = os.path.join(PKG_LIB_DIR, 'cmake', input.dart_info.lib_name)
            copy_prebuilt_path(manifest_file, entry, ('dartvm_cmake', 'cmake_dir'), cmake_dst, is_dir=True)
            if not dartvm_package_missing(input.dart_info):
                installed.append(f'installed Dart VM package files from {manifest_file}')

        if (not need_blutter or os.path.isfile(input.blutter_file)) and (not need_dartvm_package or not dartvm_package_missing(input.dart_info)):
            break

    if not matched:
        checked.append('no matching prebuilt entry')
    return installed, checked


def format_missing_artifacts(input: BlutterInput, prebuilt_checked):
    missing = []
    if not os.path.isfile(input.blutter_file):
        missing.append(f'blutter binary: {input.blutter_file}')
    missing.extend(f'Dart VM package file: {path}' for path in dartvm_package_missing(input.dart_info))

    lines = [
        'Cannot continue without fetching Dart source because required artifacts are missing.',
        '',
        'Target:',
        f'  Dart VM: {input.dart_info.lib_name}',
        f'  Blutter binary: {input.blutter_name}',
        f'  snapshot hash: {input.dart_info.snapshot_hash or "(not specified)"}',
        f'  compressed-pointers: {input.dart_info.has_compressed_ptrs}',
        f'  no-analysis: {input.no_analysis}',
        '',
        'Missing:',
    ]
    lines.extend(f'  - {item}' for item in missing)
    lines.extend(['', 'Prebuilt cache checks:'])
    lines.extend(f'  - {item}' for item in prebuilt_checked)
    lines.extend([
        '',
        'Run without --offline to allow Dart source checkout/build, or add a matching prebuilt manifest entry.',
    ])
    return '\n'.join(lines)


def find_lib_files(indir: str):
    app_file = os.path.join(indir, 'libapp.so')
    if not os.path.isfile(app_file):
        app_file = os.path.join(indir, 'App')
        if not os.path.isfile(app_file):
            sys.exit("Cannot find libapp file")
    
    flutter_file = os.path.join(indir, 'libflutter.so')
    if not os.path.isfile(flutter_file):
        flutter_file = os.path.join(indir, 'Flutter')
        if not os.path.isfile(flutter_file):
            sys.exit("Cannot find libflutter file")
    
    return os.path.abspath(app_file), os.path.abspath(flutter_file)

def extract_libs_from_apk(apk_file: str, out_dir: str):
    with zipfile.ZipFile(apk_file, "r") as zf:
        try:
            app_info = zf.getinfo('lib/arm64-v8a/libapp.so')
            flutter_info = zf.getinfo('lib/arm64-v8a/libflutter.so')
        except:
            sys.exit("Cannot find libapp.so or libflutter.so in the APK")

        zf.extract(app_info, out_dir)
        zf.extract(flutter_info, out_dir)

        app_file = os.path.join(out_dir, app_info.filename)
        flutter_file = os.path.join(out_dir, flutter_info.filename)
        return app_file, flutter_file

def find_compat_macro(dart_version: str, no_analysis: bool):
    macros = []
    include_path = os.path.join(PKG_INC_DIR, f'dartvm{dart_version}')
    vm_path = os.path.join(include_path, 'vm')
    with open(os.path.join(vm_path, 'class_id.h'), 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access = mmap.ACCESS_READ)
        # Rename the default implementation classes of Map and Set https://github.com/dart-lang/sdk/commit/a2de36e708b8a8e15d3bd49eef2cede57e649436
        if mm.find(b'V(LinkedHashMap)') != -1:
            macros.append('-DOLD_MAP_SET_NAME=1')
            # Add immutable maps and sets https://github.com/dart-lang/sdk/commit/e8e9e1d15216788d4112e40f4408c52455d11113
            if mm.find(b'V(ImmutableLinkedHashMap)') == -1:
                macros.append('-DOLD_MAP_NO_IMMUTABLE=1')
        if mm.find(b' kLastInternalOnlyCid ') == -1:
            macros.append('-DNO_LAST_INTERNAL_ONLY_CID=1')
        # Remove TypeRef https://github.com/dart-lang/sdk/commit/2ee6fcf5148c34906c04c2ac518077c23891cd1b
        # in this commit also added RecordType as sub class of AbstractType
        #   so assume Dart Records implementation is completed in this commit (before this commit is inconplete RecordType)
        if mm.find(b'V(TypeRef)') != -1:
            macros.append('-DHAS_TYPE_REF=1')
        # in main branch, RecordType is added in Dart 3.0 while TypeRef is removed in Dart 3.1
        # in Dart 2.19, RecordType might be added to a source code but incomplete
        if dart_version.startswith('3.') and mm.find(b'V(RecordType)') != -1:
            macros.append('-DHAS_RECORD_TYPE=1')
    
    with open(os.path.join(vm_path, 'class_table.h'), 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access = mmap.ACCESS_READ)
        # Clean up ClassTable (Merge ClassTable and SharedClassTable back together)
        # https://github.com/dart-lang/sdk/commit/4a4eedd860a8af2b1cb27e68d9feae5550d0f511
        # the commit moved GetUnboxedFieldsMapAt() from SharedClassTable to ClassTable
        if mm.find(b'class SharedClassTable {') != -1:
            macros.append('-DHAS_SHARED_CLASS_TABLE=1')
    
    with open(os.path.join(vm_path, 'stub_code_list.h'), 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access = mmap.ACCESS_READ)
        # Add InitLateStaticField and InitLateFinalStaticField stub
        # https://github.com/dart-lang/sdk/commit/37d45743e11970f0eacc0ec864e97891347185f5
        if mm.find(b'V(InitLateStaticField)') == -1:
            macros.append('-DNO_INIT_LATE_STATIC_FIELD=1')
    
    with open(os.path.join(vm_path, 'object_store.h'), 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access = mmap.ACCESS_READ)
        # [vm] Simplify and optimize method extractors
        # https://github.com/dart-lang/sdk/commit/b9b341f4a71b3ac8c9810eb24e318287798457ae#diff-545efb05c0f9e7191a855bca5e463f8f7f68079f74056f0040196c666b3bb8f0
        if mm.find(b'build_generic_method_extractor_code)') == -1:
            macros.append('-DNO_METHOD_EXTRACTOR_STUB=1')

    with open(os.path.join(vm_path, 'object.h'), 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access = mmap.ACCESS_READ)
        # [vm] Refactor access to Integer value
        # https://github.com/dart-lang/sdk/commit/84fd647969f0d74ab63f0994d95b5fc26cac006a
        if mm.find(b'AsTruncatedInt64Value()') == -1:
            macros.append('-DUNIFORM_INTEGER_ACCESS=1')
    
    if no_analysis:
        macros.append('-DNO_CODE_ANALYSIS=1')
    
    return macros

def cmake_blutter(input: BlutterInput):
    blutter_dir = os.path.join(SCRIPT_DIR, 'blutter')
    builddir = os.path.join(BUILD_DIR, input.blutter_name)
    
    macros = find_compat_macro(input.dart_info.version, input.no_analysis)
    my_env = macos_brew_llvm_env()
    # cmake -GNinja -Bbuild -DCMAKE_BUILD_TYPE=Release
    subprocess.run([CMAKE_CMD, '-GNinja', '-B', builddir, f'-DDARTLIB={input.dart_info.lib_name}', f'-DNAME_SUFFIX={input.name_suffix}', '-DCMAKE_BUILD_TYPE=Release', '--log-level=NOTICE'] + macros, cwd=blutter_dir, check=True, env=my_env)

    # build and install blutter
    subprocess.run([NINJA_CMD], cwd=builddir, check=True)
    subprocess.run([CMAKE_CMD, '--install', '.'], cwd=builddir, check=True)

def get_dart_lib_info(libapp_path: str, libflutter_path: str) -> DartLibInfo:
    # getting dart version
    try:
        from extract_dart_info import DartInfoError, extract_dart_info
    except ModuleNotFoundError as e:
        missing_module = e.name
        sys.exit(f'Missing Python module "{missing_module}". Install dependencies with: pip3 install pyelftools requests')

    try:
        dart_version, snapshot_hash, flags, arch, os_name = extract_dart_info(libapp_path, libflutter_path)
    except DartInfoError as e:
        sys.exit(str(e))
    print(f'Dart version: {dart_version}, Snapshot: {snapshot_hash}, Target: {os_name} {arch}')
    print('flags: ' + ' '.join(flags))

    has_compressed_ptrs = 'compressed-pointers' in flags
    return DartLibInfo(dart_version, os_name, arch, has_compressed_ptrs, snapshot_hash)

def build_and_run(input: BlutterInput, offline: bool):
    needs_blutter_binary = not input.rebuild_blutter and not os.path.isfile(input.blutter_file)
    needs_dartvm_package = input.rebuild_blutter or input.create_vs_sln or not os.path.isfile(input.blutter_file)
    needs_dartvm_package = needs_dartvm_package and bool(dartvm_package_missing(input.dart_info))

    prebuilt_checked = []
    if needs_blutter_binary or needs_dartvm_package:
        installed, prebuilt_checked = install_prebuilt_artifacts(input, needs_blutter_binary, needs_dartvm_package)
        for message in installed:
            print(message)

    if not os.path.isfile(input.blutter_file) or input.rebuild_blutter or (input.create_vs_sln and dartvm_package_missing(input.dart_info)):
        if dartvm_package_missing(input.dart_info):
            if offline:
                sys.exit(format_missing_artifacts(input, prebuilt_checked))
            print('Dart VM package is missing from local/prebuilt cache; fetching and building Dart source.')
            from dartvm_fetch_build import fetch_and_build
            fetch_and_build(input.dart_info)
            write_local_prebuilt_manifest(input)
        
        input.rebuild_blutter = True

    # creating Visual Studio solution overrides building
    if input.create_vs_sln:
        macros = find_compat_macro(input.dart_info.version, input.no_analysis)
        blutter_dir = os.path.join(SCRIPT_DIR, 'blutter')
        dbg_output_path = os.path.abspath(os.path.join(input.outdir, 'out'))
        dbg_cmd_args = f'-i {input.libapp_path} -o {dbg_output_path}'

        vscmd_ver = os.getenv('VSCMD_VER')
        assert vscmd_ver is not None, "Need run blutter in Visual Studio Develeper console"
        if vscmd_ver.startswith('18.'):
            generator = 'Visual Studio 18 2026'
        elif vscmd_ver.startswith('17.'):
            generator = 'Visual Studio 17 2022'
        else:
            assert False, "Unknown Visual Studio version"

        subprocess.run([CMAKE_CMD, '-G', generator, '-A', 'x64', '-B', input.outdir, f'-DDARTLIB={input.dart_info.lib_name}', 
                        f'-DNAME_SUFFIX={input.name_suffix}', f'-DDBG_CMD:STRING={dbg_cmd_args}'] + macros + [blutter_dir], check=True)
        dbg_exe_dir = os.path.join(input.outdir, 'Debug')
        os.makedirs(dbg_exe_dir, exist_ok=True)
        for filename in glob.glob(os.path.join(BIN_DIR, '*.dll')):
            shutil.copy(filename, dbg_exe_dir)
    else:
        if input.rebuild_blutter:
            # do not use SDK path for checking source code because Blutter does not depended on it and SDK might be removed
            cmake_blutter(input)
            assert os.path.isfile(input.blutter_file), "Build complete but cannot find Blutter binary: " + input.blutter_file
            write_local_prebuilt_manifest(input)

        # execute blutter
        result = subprocess.run([input.blutter_file, '-i', input.libapp_path, '-o', input.outdir])
        sys.exit(result.returncode)

def main_no_flutter(libapp_path: str, dart_version: str, outdir: str, rebuild_blutter: bool, create_vs_sln: bool, no_analysis: bool, offline: bool):
    try:
        version, os_name, arch = dart_version.split('_')
    except ValueError:
        sys.exit('Invalid --dart-version format. Expected "<version>_<os>_<arch>", for example "3.4.2_android_arm64"')
    dart_info = DartLibInfo(version, os_name, arch)
    input = BlutterInput(libapp_path, dart_info, outdir, rebuild_blutter, create_vs_sln, no_analysis)
    build_and_run(input, offline)
    
def main2(libapp_path: str, libflutter_path: str, outdir: str, rebuild_blutter: bool, create_vs_sln: bool, no_analysis: bool, offline: bool):
    dart_info = get_dart_lib_info(libapp_path, libflutter_path)
    input = BlutterInput(libapp_path, dart_info, outdir, rebuild_blutter, create_vs_sln, no_analysis)
    build_and_run(input, offline)

def main(indir: str, outdir: str, rebuild_blutter: bool, create_vs_sln: bool, no_analysis: bool, offline: bool):
    if indir.endswith(".apk"):
        with tempfile.TemporaryDirectory() as tmp_dir:
            libapp_file, libflutter_file = extract_libs_from_apk(indir, tmp_dir)
            main2(libapp_file, libflutter_file, outdir, rebuild_blutter, create_vs_sln, no_analysis, offline)
    else:
        libapp_file, libflutter_file = find_lib_files(indir)
        main2(libapp_file, libflutter_file, outdir, rebuild_blutter, create_vs_sln, no_analysis, offline)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog='B(l)utter',
        description='Reversing a flutter application tool')
    # TODO: accept ipa
    parser.add_argument('indir', help='An apk or a directory that contains both libapp.so and libflutter.so')
    parser.add_argument('outdir', help='An output directory')
    parser.add_argument('--rebuild', action='store_true', default=False, help='Force rebuild the Blutter executable')
    parser.add_argument('--vs-sln', action='store_true', default=False, help='Generate Visual Studio solution at <outdir>')
    parser.add_argument('--no-analysis', action='store_true', default=False, help='Do not build with code analysis')
    parser.add_argument('--offline', action='store_true', default=False, help='Do not checkout/build Dart source; use only local files or prebuilt manifests')
    # rare usage scenario
    parser.add_argument('--dart-version', help='Run without libflutter (indir become libapp.so) by specify dart version such as "3.4.2_android_arm64"')
    args = parser.parse_args()

    if args.dart_version is None:
        main(args.indir, args.outdir, args.rebuild, args.vs_sln, args.no_analysis, args.offline)
    else:
        main_no_flutter(args.indir, args.dart_version, args.outdir, args.rebuild, args.vs_sln, args.no_analysis, args.offline)
