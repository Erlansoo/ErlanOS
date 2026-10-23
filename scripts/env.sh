# Fuente este archivo en Git Bash (Windows) para tener clang, ld.lld, qemu y make en PATH:
#   source scripts/env.sh
_u="${USERNAME:-${USER:-Erlan}}"
export PATH="/c/Program Files/LLVM/bin:/c/Program Files/qemu:/c/Users/$_u/AppData/Local/Microsoft/WinGet/Packages/ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe/bin:$PATH"
unset _u
export OVMF="/c/Program Files/qemu/share/edk2-x86_64-code.fd"
