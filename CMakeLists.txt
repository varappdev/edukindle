cmake_minimum_required(VERSION 3.21)
project(edukindle VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(PkgConfig REQUIRED)
# GTK 2.24 is what ships in Kindle firmware (X11 backend).
pkg_check_modules(GTK2 REQUIRED IMPORTED_TARGET gtk+-2.0)

# kindle-sdk's .pc files reference some include dirs it doesn't ship (e.g.
# usr/include/libmount via gio -> mount). Meson tolerates that, CMake doesn't,
# so drop missing paths from the imported target.
get_target_property(_gtk2_incs PkgConfig::GTK2 INTERFACE_INCLUDE_DIRECTORIES)
set(_gtk2_existing_incs "")
foreach(_dir IN LISTS _gtk2_incs)
  if(IS_DIRECTORY "${_dir}")
    list(APPEND _gtk2_existing_incs "${_dir}")
  endif()
endforeach()
set_target_properties(PkgConfig::GTK2 PROPERTIES
  INTERFACE_INCLUDE_DIRECTORIES "${_gtk2_existing_incs}")

# GLib < 2.32 needs libgthread for g_thread_init(). The Kindle firmware ships
# GLib 2.29 (despite kindle-sdk's .pc files claiming 2.82) but no gthread-2.0.pc,
# so fall back to finding the library directly.
pkg_check_modules(GTHREAD IMPORTED_TARGET gthread-2.0)
if(NOT GTHREAD_FOUND)
  find_library(GTHREAD_LIBRARY gthread-2.0)
endif()

add_library(ekcore STATIC src/json.c src/net.c)
target_compile_options(ekcore PRIVATE -Wall -Wextra)
target_link_libraries(ekcore PUBLIC PkgConfig::GTK2)

add_executable(edukindle
  src/main.c
  src/ui.c
  src/screen_connect.c
  src/screen_library.c
  src/screen_reader.c
  src/screen_quiz.c
  src/screen_progress.c
)
target_compile_options(edukindle PRIVATE -Wall -Wextra)
target_link_libraries(edukindle PRIVATE ekcore m)
if(GTHREAD_FOUND)
  target_link_libraries(edukindle PRIVATE PkgConfig::GTHREAD)
elseif(GTHREAD_LIBRARY)
  target_link_libraries(edukindle PRIVATE ${GTHREAD_LIBRARY})
endif()

# Host-only unit tests for the JSON parser and HTTP client.
if(NOT CMAKE_CROSSCOMPILING)
  enable_testing()
  add_executable(test_core tests/test_core.c)
  target_link_libraries(test_core PRIVATE ekcore)
  add_test(NAME core COMMAND test_core)
endif()

# ---- Packaging: KUAL extension layout -------------------------------------
# `cmake --install build/<preset> --prefix dist` produces
#   dist/extensions/edukindle/{config.xml,menu.json,run.sh,bin/edukindle}
# Copy dist/extensions/ to the Kindle's USB root (/mnt/us/extensions/).
set(EXT_DIR extensions/edukindle)
install(TARGETS edukindle RUNTIME DESTINATION ${EXT_DIR}/bin)
install(FILES kual/config.xml kual/menu.json DESTINATION ${EXT_DIR})
install(FILES kual/edukindle.conf.example DESTINATION ${EXT_DIR})
install(PROGRAMS kual/run.sh DESTINATION ${EXT_DIR})
