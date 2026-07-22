# Philips USB PC Link Activator for Windows

Windows utility for bringing back Philips **USB PC Link** playback on modern
Windows systems.

Target platform: Windows 10 and Windows 11. Windows 8/8.1 may work, but it is
not release-validated yet.

Idealized and originally tested by **Rafael Jaeger**, after validating the
recovery path on a Philips FW-M589/FWM589 unit.

The first validated target is the Philips composite USB audio family that
appears as `Philips Audio Set` with USB ID `0471:0111`, including the FW-M589 /
FWM589 generation.

## What It Does

- Keeps the normal Windows USB Audio output active.
- Installs WinUSB only on the Philips control interface:

```text
USB\VID_0471&PID_0111&MI_03
```

- Sends the PC Link wake-up sequence used by the original Philips stack:

```text
40 04 00 00 a4 ef 01 00 01
40 04 00 00 a4 f0 01 00 ff
```

- Re-activates PC Link after USB reconnects or after the stereo is switched
  away from PC Link and back.
- Keeps only one app instance running, so a tray copy and a second opened copy
  do not fight for the USB control interface.
- Reads the radio buttons through the control interface and maps them to Windows
  media controls.
- Captures raw control reports so new front-panel or remote-control formats can
  be mapped safely for additional models.
- Provides startup and auto-reactivation options for daily use.

## Supported Hardware

Current stable profile:

| Profile | Models | USB ID | Audio interface | Control interface |
| --- | --- | --- | --- | --- |
| `philips-uac3553b-0471-0111` | FW-M589/FWM589, FW-M779/FWM779, MCM530, MCM590 and related Philips Audio Set devices | `0471:0111` | `MI_00` | `MI_03` |

Other Philips systems with "USB PC Link" should be added through explicit
profiles after descriptor captures and real playback tests. The app must not
target generic Philips USB devices or front-panel USB mass-storage players.

See `docs/compatibility-research.md` for model notes.

Strong first-wave candidates with the same family/protocol include FWM589,
MCM530, MCM590, FWM779, FWM922, MCM570 and MCM595. Other USB PC Link models can
be added once users export diagnostics from the app.

## User Flow

1. Connect the Philips stereo by USB.
2. Select `PC Link` on the stereo.
3. Open the app.
4. If prompted, install the WinUSB control driver. This requires administrator
   approval and must apply only to `MI_03`.
5. Click `Activate PC Link`.
6. Set `Philips Audio Set` as the Windows output device.
7. Enable `Auto-reactivate` and `Start with Windows` for normal day-to-day use.

Activation is not permanent. If the USB cable is unplugged, Windows restarts, or
the stereo leaves PC Link mode, the app should send the wake-up sequence again.
If Windows no longer lists the stereo as a present USB device, the app waits for
the stereo to enumerate again instead of repeatedly asking to reinstall a driver
that is already installed.

## Radio Buttons

When `MI_03` is controlled by this app, Windows no longer receives the old HID
button events directly. The app is responsible for reading the radio and sending
media commands to Windows:

- Play/Pause
- Stop
- Next Track
- Previous Track
- Mute
- Volume Up
- Volume Down

The UI should show whether button capture is active and display the last
received command for diagnostics.

Some stereos may forward remote-control IR buttons differently from the
front-panel buttons, or may not forward them over USB at all. Use `Capturar
Controle`, press the remote buttons for 15 seconds, and attach the generated
Desktop capture file when requesting support for a new mapping. Unknown reports
should be mapped only after a real capture; the app intentionally avoids turning
random bytes into media keys.

Known status packets such as `80 4a` are logged as unknown diagnostics and must
not be treated as media buttons.

## Volume Behavior

There are two volume layers:

- Windows endpoint/software volume for `Philips Audio Set`.
- The stereo amplifier volume controlled by the physical radio knob.

The app maps the radio's volume buttons to Windows media volume behavior when
button capture is active. The main UI intentionally does not expose a software
volume slider until endpoint/hardware synchronization is reliable per model.
True hardware volume synchronization should be treated as experimental until
verified per model.

The app does not re-encode, resample or route audio through its own mixer. Audio
playback stays on the Philips USB Audio interface exposed to Windows, so quality
is limited by the stereo's USB audio format, the Windows audio engine settings
and the source material. For the cleanest output, avoid clipping in Windows/app
volume and use the Philips endpoint directly.

## Development

```powershell
python -m pip install PySide6 libusb1 libusb-package pycaw comtypes
python .\app.py
```

Build:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Clean
```

Build artifacts should not be committed. Keep `build/`, `dist/`, caches, logs,
local driver experiments, and extracted vendor installers out of Git.

## Driver Installation

The app includes a WinUSB package for the validated control interface:

```text
driver\winusb-philips-mi03
```

The installer imports the bundled certificate for the driver package and runs
`pnputil /add-driver`. This is acceptable for testing/community builds, but a
public commercial release should use a trusted code-signing certificate and a
production-signed driver package.

Driver safety rules:

- Do not install WinUSB on `MI_00`.
- Do not replace the whole composite USB device.
- Do not wildcard all Philips USB devices.
- Always show the exact interface that will be changed.
- Always provide a restore path for returning `MI_03` to the Windows HID/input
  driver.

See `docs/driver-installation.md` for the installation and restore strategy.

## Product Notes

See `docs/ui-product-notes.md` for the recommended commercial UI flow, state
model, copy, QA checklist, and risks.

## Credits

- Rafael Jaeger: original idea, hardware testing, and validation on Philips
  FW-M589/FWM589.
- Philips USB PC Link Linux community: public interoperability notes for the
  `0471:0111` activation sequence.
