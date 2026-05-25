import os
import platform
import subprocess


def macos_brew_llvm_env():
    if platform.system() != 'Darwin':
        return None

    mac_ver = int(platform.mac_ver()[0].split('.', 1)[0])
    if mac_ver >= 15:
        return None

    try:
        llvm_path = subprocess.run(
            ['brew', '--prefix', 'llvm@19'],
            capture_output=True,
            check=True,
            text=True).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise RuntimeError('macOS before 15 requires llvm@19. Install it with: brew install llvm@19') from e

    clang_file = os.path.join(llvm_path, 'bin', 'clang')
    return {**os.environ, 'CC': clang_file, 'CXX': clang_file + '++'}
