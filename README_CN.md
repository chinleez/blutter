# B(l)utter

通过编译 Dart AOT Runtime 来辅助逆向 Flutter 移动应用的工具。

当前仅支持 Android `libapp.so`，且只支持 `arm64`。目前也主要适配较新的 Dart 版本。

高优先级的缺失功能见 [TODO](#todo)。

## 环境准备

本项目使用 C++20 Formatting library。Dart 3.12+ 需要较新的 C++ 编译器，例如 `g++ >= 13` 或 `Clang/libc++ >= 19`。

推荐使用 Linux，项目只在 Debian sid/trixie 上测试过，环境配置相对简单。

### Debian Unstable (gcc 13)

**注意：**
只建议使用官方主仓库自带 `gcc >= 13` 的 Debian/Ubuntu 版本。把新版 gcc 移植到旧 Debian/Ubuntu 上通常不可用。

- 安装构建工具和依赖：

```bash
apt install python3-pyelftools python3-requests git cmake ninja-build \
    build-essential pkg-config libicu-dev libcapstone-dev
```

### Windows

- 安装 git 和 Python 3
- 安装最新版 Visual Studio，并勾选 "Desktop development with C++" 和 "C++ CMake tools"
- 安装依赖库 `libcapstone` 和 `libicu4c`

```bat
python scripts\init_env_win.py
```

- 启动 "x64 Native Tools Command Prompt"

### macOS Sequoia

- 安装 Xcode
- 安装所需工具：

```bash
brew install cmake ninja pkg-config icu4c capstone
pip3 install pyelftools requests
```

### macOS Ventura 和 Sonoma (clang 19)

- 安装 Xcode
- 安装 clang 19 和所需工具：

```bash
brew install llvm@19 cmake ninja pkg-config icu4c capstone
pip3 install pyelftools requests
```

## 使用方法

可以直接传入 APK：

```bash
python3 blutter.py path/to/app.apk out_dir
```

也可以传入同时包含 `libapp.so` 和 `libflutter.so` 的目录：

```bash
python3 blutter.py path/to/app/lib/arm64-v8a out_dir
```

`blutter.py` 会从 Flutter engine 自动识别 Dart 版本，并调用对应版本的 `blutter` 可执行文件分析 `libapp.so`。

如果当前缺少目标 Dart 版本对应的 `blutter` 可执行文件，脚本会自动 checkout Dart 源码并编译。

如果不希望自动拉取或编译 Dart 源码，可以使用 `--offline`。离线模式只会使用已有的 `bin/`、`packages/` 文件，或本地预编译 manifest 中匹配的条目。

```bash
python3 blutter.py path/to/app/lib/arm64-v8a out_dir --offline
```

本地预编译 manifest 会从 `prebuilt/manifest.json` 和 `bin/manifest.json` 加载。schema 可参考 `prebuilt/manifest.example.json`。一个 manifest 条目可以提供预编译的 Blutter 二进制文件，也可以提供完整安装后的 Dart VM package 目录。当目标应用暴露 snapshot hash 时，预编译条目必须使用相同的 `snapshot_hash`，或显式写成 `"*"`。

源码构建成功后，Blutter 会把本地缓存条目写入 `bin/manifest.json`，这样同一个 Dart VM 目标后续可以复用，不需要再次编译。

可以预编译一组常用 Dart 版本矩阵，并生成 `prebuilt/manifest.json`：

```bash
python3 scripts/build_prebuilt_matrix.py \
  --versions 3.3.0 3.4.0 3.5.0 3.6.0 3.7.0 3.8.0 3.9.0 3.10.0 3.11.0 3.11.4 3.12.1 \
  --target-os android \
  --target-arch arm64 \
  --compressed-pointers true
```

矩阵脚本默认写入通配符 `snapshot_hash` 条目。如果目标需要精确 snapshot hash，可以用 `--snapshot-hash <hash>` 重新构建对应条目。本地产物路径会按 Dart VM 版本、目标平台、指针压缩模式和 analysis 模式区分；同一目标用不同 snapshot hash 重新构建时，会替换该目标已有的 manifest 条目。

## 更新

可以使用 `git pull` 更新项目，并在运行 `blutter.py` 时加 `--rebuild` 强制重新编译可执行文件：

```bash
python3 blutter.py path/to/app/lib/arm64-v8a out_dir --rebuild
```

## 输出文件

- `asm/*`：带符号的 `libapp` 汇编
- `blutter_frida.js`：目标应用的 Frida 脚本模板
- `objs.txt`：Object Pool 中 Object 的完整嵌套 dump
- `pp.txt`：Object Pool 中的所有 Dart 对象

## 目录说明

- `bin`：保存各 Dart 版本对应的 Blutter 可执行文件，格式为 `blutter_dartvm<ver>_<os>_<arch>`
- `blutter`：Blutter 源码，需要链接 Dart VM library 构建
- `build`：构建工程目录，构建完成后可以删除
- `dartsdk`：Dart Runtime checkout 目录，构建完成后可以删除
- `external`：仅 Windows 使用的第三方库
- `packages`：Dart Runtime 静态库
- `scripts`：用于获取和构建 Dart 的 Python 脚本

## 生成 Visual Studio 开发方案

作者在 Windows 上使用 Visual Studio 开发 Blutter。可以使用 `--vs-sln` 生成 Visual Studio solution：

```bat
python blutter.py path\to\lib\arm64-v8a build\vs --vs-sln
```

## TODO

- 更多代码分析能力
  - 函数参数和返回类型
  - 针对代码模式生成部分伪代码
- 生成更好的 Frida 脚本
  - 更多内部类
  - 对象修改
- 混淆应用支持，目前仍缺少很多函数识别
- 读取 iOS binary
- 在 iOS binary 支持完成后，支持直接输入 ipa
