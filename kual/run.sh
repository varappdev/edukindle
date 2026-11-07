#!/bin/sh
# Launched by KUAL from /mnt/us/extensions/edukindle
cd "$(dirname "$0")" || exit 1
# First run: create the editable config next to the binary.
[ -f edukindle.conf ] || cp edukindle.conf.example edukindle.conf
export DISPLAY=:0
exec ./bin/edukindle >/tmp/edukindle.log 2>&1
