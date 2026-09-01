#!/bin/sh
set -eu

frequency_hz=${RAGCHEW_TEST_FREQUENCY_HZ:-858000000}
sample_rate_hz=${RAGCHEW_TEST_SAMPLE_RATE_HZ:-8016000}
duration_seconds=${RAGCHEW_TEST_DURATION_SECONDS:-30}
samples=$((sample_rate_hz * duration_seconds))

command -v hackrf_info >/dev/null
command -v hackrf_transfer >/dev/null

echo "== HackRF identity =="
hackrf_info

echo "== Host state =="
uname -a
if command -v vcgencmd >/dev/null 2>&1; then
    vcgencmd measure_temp
    vcgencmd get_throttled
elif [ -r /sys/class/thermal/thermal_zone0/temp ]; then
    awk '{printf "temperature=%.1f C\n", $1/1000}' /sys/class/thermal/thermal_zone0/temp
fi

echo "== Receive-only sustained transfer =="
echo "frequency=${frequency_hz} sample_rate=${sample_rate_hz} duration=${duration_seconds}"
hackrf_transfer \
    -r /dev/null \
    -f "$frequency_hz" \
    -s "$sample_rate_hz" \
    -a 1 \
    -l 24 \
    -g 20 \
    -n "$samples"

echo "PASS: receive transfer completed without a reported USB sample loss"
