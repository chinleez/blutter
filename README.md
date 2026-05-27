# B(l)utter
Flutter Mobile Application Reverse Engineering Tool by Compiling Dart AOT Runtime

Currently the application supports only Android libapp.so (arm64 only).
Also the application is currently work only against recent Dart versions.

For high priority missing features, see [TODO](#todo)


## Environment Setup
This application uses C++20 Formatting library. It requires very recent C++ compiler such as g++>=13 or Clang/libc++>=19 for Dart 3.12+.

I recommend using Linux OS (only tested on Deiban sid/trixie) because it is easy to setup.

### Debian Unstable (gcc 13)
**_NOTE:_**
Use ONLY Debian/Ubuntu version that provides gcc>=13 from its own main repository.
Using ported gcc to old Debian/Ubuntu version does not work.

- Install build tools and depenencies
```
apt install python3-pyelftools python3-requests git cmake ninja-build \
    build-essential pkg-config libicu-dev libcapstone-dev
```

### Windows
- Install git and python 3
- Install latest Visual Studio with "Desktop development with C++" and "C++ CMake tools"
- Install required libraries (libcapstone and libicu4c)
```
python scripts\init_env_win.py
```
- Start "x64 Native Tools Command Prompt"

### macOS Sequoia
- Install XCode
- Install required tools
```
brew install cmake ninja pkg-config icu4c capstone
pip3 install pyelftools requests
```

### macOS Ventura and Sonoma (clang 19)
- Install XCode
- Install clang 19 and required tools
```
brew install llvm@19 cmake ninja pkg-config icu4c capstone
pip3 install pyelftools requests
```

## Usage
Extract "lib" directory from apk file
```
python3 blutter.py path/to/app/lib/arm64-v8a out_dir
```
The blutter.py will automatically detect the Dart version from the flutter engine and call executable of blutter to get the information from libapp.so.

If the blutter executable for required Dart version does not exists, the script will automatically checkout Dart source code and compiling it.

To prevent automatic Dart source checkout/build, use ```--offline```. In offline mode Blutter only uses existing ```bin/``` and ```packages/``` files or matching entries from a local prebuilt manifest.

```
python3 blutter.py path/to/app/lib/arm64-v8a out_dir --offline
```

Local prebuilt manifests are loaded from ```prebuilt/manifest.json``` and ```bin/manifest.json```. See ```prebuilt/manifest.example.json``` for the schema. A manifest entry can provide a prebuilt Blutter binary and/or a complete installed Dart VM package directory. When a target app exposes a snapshot hash, prebuilt entries must use the same ```snapshot_hash``` or explicitly use ```"*"```.

After a successful source build, Blutter writes a local cache entry to ```bin/manifest.json``` so the same Dart VM target can be reused without rebuilding.

You can prebuild a common Dart version matrix and write ```prebuilt/manifest.json```:

```
python3 scripts/build_prebuilt_matrix.py \
  --versions 3.3.0 3.4.0 3.5.0 3.6.0 3.7.0 3.8.0 3.9.0 3.10.0 3.11.0 3.11.4 3.12.1 \
  --target-os android \
  --target-arch arm64 \
  --compressed-pointers true
```

The matrix script writes wildcard ```snapshot_hash``` entries by default. If a target requires an exact snapshot hash, rebuild that entry with ```--snapshot-hash <hash>```. The local artifact paths are keyed by Dart VM version, target, pointer mode, and analysis mode, so rebuilding the same target with a different snapshot hash replaces the previous manifest entry for that target.

## Update
You can use ```git pull``` to update and run blutter.py with ```--rebuild``` option to force rebuild the executable
```
python3 blutter.py path/to/app/lib/arm64-v8a out_dir --rebuild
```

## Output files
- **asm/\*** libapp assemblies with symbols
- **blutter_frida.js** the frida script template for the target application
- **objs.txt** complete (nested) dump of Object from Object Pool
- **pp.txt** all Dart objects in Object Pool


## Directories
- **bin** contains blutter executables for each Dart version in "blutter_dartvm\<ver\>\_\<os\>\_\<arch\>" format
- **blutter** contains source code. need building against Dart VM library
- **build** contains building projects which can be deleted after finishing the build process
- **dartsdk** contains checkout of Dart Runtime which can be deleted after finishing the build process
- **external** contains 3rd party libraries for Windows only
- **packages** contains the static libraries of Dart Runtime
- **scripts** contains python scripts for getting/building Dart


## Generating Visual Studio Solution for Development
I use Visual Studio to delevlop Blutter on Windows. ```--vs-sln``` options can be used to generate a Visual Studio solution.
```
python blutter.py path\to\lib\arm64-v8a build\vs --vs-sln
```

## TODO
- More code analysis
  - Function arguments and return type
  - Some psuedo code for code pattern
- Generate better Frida script
  - More internal classes
  - Object modification
- Obfuscated app (still missing many functions)
- Reading iOS binary
- Input as apk or ipa
