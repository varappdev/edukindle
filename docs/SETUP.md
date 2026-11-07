# Toolchain setup

How to get from a fresh Linux machine to a working `cmake --preset kindlehf`.
This only needs to be done once per machine.

The build uses two pieces, both installed under `~/x-tools/`:

| Piece | What it provides |
|---|---|
| [koxtoolchain](https://github.com/koreader/koxtoolchain) `kindlehf` | GCC 14 cross compiler + glibc sysroot for `arm-kindlehf-linux-gnueabihf` |
| [kindle-sdk](https://github.com/KindleModding/kindle-sdk) | Adds GTK2/GLib headers, `.pc` files and the Kindle firmware's runtime libraries to that sysroot |

## 0. Check your Kindle

`kindlehf` is for firmware **5.16.3 or newer** (hard-float userland). Check
*Settings → Device Options → Device Info* on the Kindle. Older firmware needs
the soft-float `kindlepw2` toolchain instead, which this project isn't set up for.

The device must be jailbroken and have [KUAL](https://kindlemodding.org/) installed
to launch the app.

## 1. Host packages

Arch / CachyOS:

```sh
sudo pacman -S --needed base-devel git curl cmake ninja pkgconf zstd libarchive nettle zlib
```

Debian / Ubuntu:

```sh
sudo apt install build-essential git curl cmake ninja-build pkg-config zstd \
                 libarchive-dev nettle-dev zlib1g-dev
```

CMake must be at least 3.21. `libarchive`, `nettle` and `zlib` are only needed to build
KindleTool, which `gen-sdk.sh` uses to unpack the firmware.
`aria2` is optional and speeds up the firmware download.

## 2. koxtoolchain (`kindlehf`)

Use the prebuilt release. Building it from source takes about an hour and isn't needed.

```sh
mkdir -p ~/x-tools
cd ~/x-tools
curl -LO https://github.com/koreader/koxtoolchain/releases/download/2026.08/kindlehf.tar.zst
tar --zstd -xf kindlehf.tar.zst      # -> ~/x-tools/arm-kindlehf-linux-gnueabihf/
rm kindlehf.tar.zst
```

Check that it runs:

```sh
~/x-tools/arm-kindlehf-linux-gnueabihf/bin/arm-kindlehf-linux-gnueabihf-gcc --version
```

If the tarball unpacks somewhere other than `~/x-tools/arm-kindlehf-linux-gnueabihf`,
either move it there or set `KOX_TC_DIR` (see [Custom locations](#custom-locations)).

## 3. kindle-sdk

```sh
cd ~/x-tools
git clone --recursive https://github.com/KindleModding/kindle-sdk.git
cd kindle-sdk
./gen-sdk.sh kindlehf
```

What `gen-sdk.sh` does:

1. Builds KindleTool.
2. Downloads the official 5.16.3 firmware images, which are several hundred MB and cached in `kindle-sdk/cache/`.
3. Loop-mounts the firmware's rootfs, which needs **sudo**, and copies its libraries into the toolchain sysroot.
4. Installs GTK2 2.24, GLib, Cairo and other headers and `.pc` files.
5. Writes `~/x-tools/arm-kindlehf-linux-gnueabihf/meson-crosscompile.txt`, which this project doesn't use.

It finishes with `Kindle (unofficial) SDK Installed`. Afterwards the toolchain
directory is made **read-only** on purpose. Don't install things into the sysroot by hand.
To redo the setup, run `gen-sdk.sh` again.

Check that it worked:

```sh
PKG_CONFIG_LIBDIR=~/x-tools/arm-kindlehf-linux-gnueabihf/arm-kindlehf-linux-gnueabihf/sysroot/usr/lib/pkgconfig \
  pkg-config --modversion gtk+-2.0     # -> 2.24.33
```

## 4. Build

From the project root:

```sh
cmake --preset kindlehf
cmake --build --preset kindlehf
file build/kindlehf/edukindle
# ELF 32-bit LSB executable, ARM, EABI5 ... interpreter /lib/ld-linux-armhf.so.3
```

## 5. Deploy and run

```sh
cmake --install build/kindlehf --prefix dist --strip
```

1. Plug in the Kindle over USB. It mounts as a drive called `Kindle`, which is `/mnt/us` on the device.
2. Copy `dist/extensions/edukindle/` into the drive's `extensions/` folder:

   ```sh
   cp -r dist/extensions/edukindle /run/media/$USER/Kindle/extensions/
   sync
   ```

3. Eject the drive, for example with `udisksctl unmount -b /dev/sdX` or from your file manager.
4. On the Kindle, open **KUAL** and tap **EduKindle**.

stdout and stderr go to `/tmp/edukindle.log` on the device. That file is in RAM, not on the USB drive,
so reading it needs SSH via USBNetwork.

## Editor support (clangd)

```sh
ln -sf build/kindlehf/compile_commands.json .
```

Start clangd with
`--query-driver=$HOME/x-tools/arm-kindlehf-linux-gnueabihf/bin/*-gcc` so it uses the
cross compiler's built-in include paths and target defines.

## Custom locations

The toolchain file looks for the toolchain in this order:

1. `-DKOX_TC_DIR=/path/to/arm-kindlehf-linux-gnueabihf` on the configure command line
2. the `KOX_TC_DIR` environment variable
3. `~/x-tools/arm-kindlehf-linux-gnueabihf`

`gen-sdk.sh` also honours a `tc_dir` environment variable if the toolchain isn't in
`~/x-tools`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `koxtoolchain not found at ...` | Step 2 is missing or in a different location. Set `KOX_TC_DIR`. |
| `Package 'gtk+-2.0' not found` | Step 3 hasn't been run, or it failed partway. Re-run `gen-sdk.sh kindlehf`. |
| `includes non-existent path .../usr/include/libmount` | This is a quirk in kindle-sdk's `.pc` files. `CMakeLists.txt` already filters it out. If you add more pkg-config deps, link them the same way. |
| Linker complains about missing `lib*.so.*` | The sysroot is incomplete. Re-run `gen-sdk.sh`. |
| Nothing happens when tapped in KUAL | Check that the device firmware is 5.16.3 or newer, and check `/tmp/edukindle.log` over SSH. |
