# Receiver baseline — 2026-08-27

## Scope

This is a partial task 10.1 baseline from the development laptop. It does not replace validation on the Raspberry Pi at the final antenna location.

## Hardware and transport

- HackRF One-compatible hardware revision r9
- Firmware v1.8.0, API 1.07
- Host enumeration: `1d50:6089`
- A receive-only 8 Msps transfer sustained 16 MB/s for 30 seconds without a transfer failure.
- The device initially remained in the PortaPack UI/off state and did not enumerate. Entering HackRF host mode resolved enumeration.

## Configuration findings

Trunk Recorder 5.2 exposed two configuration defects, both corrected in the repository:

1. `Group` is not a recognized talkgroup CSV column; the supported column is `Category`.
2. 8 MHz is not divisible by Trunk Recorder's 24 kHz channel rate. The source now uses 8.016 MHz.
3. HackRF's RF gain stage accepts 0 or 14 dB. The requested 24 dB was silently clamped, so the profile now declares 14 dB explicitly.

The corrected profile loaded 14 allowed talkgroups, initialized four digital recorders, tuned the HackRF at 858 MHz, and reported a usable window of approximately 854.024–861.976 MHz.

## RF findings

The configured control channels are 855.2375, 855.4625, 857.9875, and 858.9875 MHz. The trial location was Leesburg, Virginia, roughly 35 miles west of central Washington. During indoor trials with multiple antenna configurations:

- all four channels produced zero decoded P25 control messages;
- a fine sweep showed no persistent narrowband peak at the configured controls;
- known FM-band activity was visible in a broad sweep, indicating that the receive path is functioning generally.

This is a failed receiver-location trial, not evidence that the DC system is inactive. DC public-safety sites are engineered for District coverage, unlike the high-power FM stations received at this location. Do not tune PPM from this sample, count quiet periods from this placement as receiver uptime, or use Leesburg as the MVP receiver site without a separate successful directional-antenna study.

## Still required on the Pi

- place an 800 MHz-suitable antenna near a window or other final location;
- obtain a stable P25 control decode and record message rate/error rate;
- calibrate PPM against that stable control channel;
- test gain with RF amp off/on and choose the lowest reliable setting;
- run four simultaneous digital recorder paths during active traffic;
- sustain the 8.016 Msps source for at least one hour while recording temperature, throttling, USB loss, and CPU load;
- quantify in-window and out-of-range grants.
