# EduKindle

GTK+ 2 app for jailbroken Kindles (firmware >= 5.16.3, `kindlehf`), cross-compiled
with [koxtoolchain](https://github.com/koreader/koxtoolchain) and
[kindle-sdk](https://github.com/KindleModding/kindle-sdk).

## One-time setup

Install koxtoolchain `kindlehf` and the kindle-sdk sysroot into `~/x-tools/`.
**[docs/SETUP.md](docs/SETUP.md)** has the full steps. In short:

```sh
cd ~/x-tools
curl -L https://github.com/koreader/koxtoolchain/releases/download/2026.08/kindlehf.tar.zst | tar --zstd -x
git clone --recursive https://github.com/KindleModding/kindle-sdk.git
cd kindle-sdk && ./gen-sdk.sh kindlehf   # run from its own dir; needs sudo
```

## Build

```sh
cmake --preset kindlehf        # or kindlehf-debug
cmake --build --preset kindlehf
ln -sf build/kindlehf/compile_commands.json .   # for clangd
```

clangd needs `--query-driver=$HOME/x-tools/arm-kindlehf-linux-gnueabihf/bin/*-gcc`
to pick up the cross compiler's built-in headers.

## Package / deploy (KUAL)

```sh
cmake --install build/kindlehf --prefix dist --strip
# copy dist/extensions/edukindle to /mnt/us/extensions/ on the Kindle
```

Launch from KUAL → EduKindle. Logs go to `/tmp/edukindle.log` on the device.

## Layout

| Path | Purpose |
|---|---|
| `src/` | App sources |
| `cmake/kindlehf.cmake` | Toolchain file (compilers, sysroot, pkg-config env) |
| `CMakePresets.json` | `kindlehf` / `kindlehf-debug` presets |
| `kual/` | KUAL extension metadata + launcher |
| `docs/SETUP.md` | Toolchain / SDK setup guide |
