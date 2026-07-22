# Philips USB PC Link Activator for Windows

Windows utility for restoring Philips USB PC Link playback on modern Windows
systems.

Target platform: Windows 10 and Windows 11. Windows 8/8.1 may work, but it is
not release-validated yet.

This project was idealized and originally validated by **Rafael Jaeger** on a
Philips FW-M589/FWM589 unit. The first supported device family appears in
Windows as `Philips Audio Set` with USB ID `0471:0111`.

## Current Status

- Restores PC Link activation with the known Philips wake-up sequence.
- Keeps Windows USB Audio on `MI_00`.
- Installs WinUSB only on the control interface `MI_03`.
- Reactivates PC Link after reconnects and mode changes.
- Maps radio buttons to Windows media controls.
- Includes diagnostics for remote-control button capture and new model support.

Source code, driver notes, build instructions and release docs are in
[`public_app`](public_app/README.md).

## Credits

- Rafael Jaeger: original idea, hardware testing and first validated recovery
  path.
- Philips USB PC Link Linux community: public interoperability notes for the
  `0471:0111` activation sequence.
