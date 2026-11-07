#!/bin/sh
# Launched by KUAL from /mnt/us/extensions/edukindle
cd "$(dirname "$0")" || exit 1
export DISPLAY=:0
exec ./bin/edukindle >/tmp/edukindle.log 2>&1
