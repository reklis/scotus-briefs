# Raspberry Pi edge setup

The edge is an outbound-only RF appliance. Use a Pi 4 or Pi 5 with active cooling, reliable storage, NTP, and a suitable 700/800 MHz antenna. Do not connect a transmit path or attempt to process encrypted calls.

## Layout

- Trunk Recorder config: `/etc/ragchew/trunk-recorder.json`
- Talkgroups: `/etc/ragchew/talkgroups.csv`
- Non-secret defaults: `/etc/ragchew/mvp.yaml`
- Root-readable secrets: `/etc/ragchew/edge.env`
- Finalized calls: `/var/lib/ragchew/capture`
- Durable spool: `/var/lib/ragchew/spool`

Create an unprivileged `ragchew` user with only the SDR device group and ownership of `/var/lib/ragchew`. Install the systemd units from `edge/systemd/`, then enable Trunk Recorder before the edge forwarder. The forwarder requires `RAGCHEW_INGESTION_URL` using HTTPS and `RAGCHEW_RECEIVER_TOKEN`.

## RF profile

The initial profile covers approximately 854–862 MHz with an 8.016 MHz OsmoSDR sample rate and monitors DCFD Dispatch, Main, and clear incident talkgroups. It cannot capture the separated 700 MHz system channels. Validate center frequency, PPM correction, gain, QPSK control decoding, simultaneous recorder capacity, and heat at the final physical location. Reduce gain or add a band-specific filter if strong local signals overload the HackRF.

## Failure behavior

Finalized calls are copied and fsynced into a SQLite-backed spool before upload. Network errors retry; matching duplicates are acknowledged; ID/content conflicts stop in `conflicted` state. Unacknowledged calls are never silently evicted. A full spool raises a visible error and leaves source captures in place.
