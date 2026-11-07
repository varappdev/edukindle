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

add_executable(edukindle
  src/main.c
)
target_compile_options(edukindle PRIVATE -Wall -Wextra)
target_link_libraries(edukindle PRIVATE PkgConfig::GTK2)

# ---- Packaging: KUAL extension layout -------------------------------------
# `cmake --install build/<preset> --prefix dist` produces
#   dist/extensions/edukindle/{config.xml,menu.json,run.sh,bin/edukindle}
# Copy dist/extensions/ to the Kindle's USB root (/mnt/us/extensions/).
set(EXT_DIR extensions/edukindle)
install(TARGETS edukindle RUNTIME DESTINATION ${EXT_DIR}/bin)
install(FILES kual/config.xml kual/menu.json DESTINATION ${EXT_DIR})
install(PROGRAMS kual/run.sh DESTINATION ${EXT_DIR})
