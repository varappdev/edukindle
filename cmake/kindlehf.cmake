# CMake toolchain file: Kindle (FW >= 5.16.3, hard-float) via koxtoolchain.
#
# Expects koxtoolchain's "kindlehf" target in ~/x-tools (override with
# -DKOX_TC_DIR=... or the KOX_TC_DIR env var), with the sysroot populated by
# KindleModding/kindle-sdk (`gen-sdk.sh kindlehf`) so GTK2 headers, .pc files
# and the firmware's runtime libraries are available.

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR armv7l)

set(KOX_TARGET arm-kindlehf-linux-gnueabihf)

if(NOT KOX_TC_DIR)
  if(DEFINED ENV{KOX_TC_DIR})
    set(KOX_TC_DIR "$ENV{KOX_TC_DIR}")
  else()
    set(KOX_TC_DIR "$ENV{HOME}/x-tools/${KOX_TARGET}")
  endif()
endif()
# Propagate to try_compile() projects, which re-read this file.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES KOX_TC_DIR)

if(NOT EXISTS "${KOX_TC_DIR}/bin/${KOX_TARGET}-gcc")
  message(FATAL_ERROR "koxtoolchain not found at ${KOX_TC_DIR}")
endif()

set(CMAKE_C_COMPILER   "${KOX_TC_DIR}/bin/${KOX_TARGET}-gcc")
set(CMAKE_CXX_COMPILER "${KOX_TC_DIR}/bin/${KOX_TARGET}-g++")
set(CMAKE_AR           "${KOX_TC_DIR}/bin/${KOX_TARGET}-ar" CACHE FILEPATH "")
set(CMAKE_RANLIB       "${KOX_TC_DIR}/bin/${KOX_TARGET}-ranlib" CACHE FILEPATH "")
set(CMAKE_STRIP        "${KOX_TC_DIR}/bin/${KOX_TARGET}-strip" CACHE FILEPATH "")

set(CMAKE_SYSROOT "${KOX_TC_DIR}/${KOX_TARGET}/sysroot")

# Only search the sysroot for headers/libs/packages; host for programs.
set(CMAKE_FIND_ROOT_PATH "${CMAKE_SYSROOT}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# Make host pkg-config resolve the kindle-sdk .pc files (prefix=/usr) into the sysroot.
set(ENV{PKG_CONFIG_DIR} "")
set(ENV{PKG_CONFIG_PATH} "")
set(ENV{PKG_CONFIG_LIBDIR} "${CMAKE_SYSROOT}/usr/lib/pkgconfig:${CMAKE_SYSROOT}/usr/share/pkgconfig")
set(ENV{PKG_CONFIG_SYSROOT_DIR} "${CMAKE_SYSROOT}")

# Firmware libs pull in indirect deps; let the linker find them in the sysroot.
set(CMAKE_EXE_LINKER_FLAGS_INIT
    "-Wl,--as-needed -Wl,-rpath-link,${CMAKE_SYSROOT}/usr/lib -Wl,-rpath-link,${CMAKE_SYSROOT}/lib")
