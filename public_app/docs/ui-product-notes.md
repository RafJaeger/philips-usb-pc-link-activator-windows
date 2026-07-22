# UI and product notes

Date: 2026-07-22

Scope: product and UX guidance for `Philips USB PC Link Activator`. These notes
do not require changing `app.py`; they are intended to guide the UI refactor.

## Product promise

The app should be presented as a recovery bridge for old Philips USB PC Link
systems on modern Windows. The clearest promise is:

> Restore USB PC Link playback, keep Windows USB Audio working, and reconnect
> automatically when the stereo returns to PC Link mode.

Avoid promising universal support for every Philips product with a USB port.
"USB PC Link" was a Philips feature family, while many later Philips USB ports
are mass-storage playback and should not be touched.

## First-run flow

The first run should feel like a short setup wizard even if it is one window:

1. Detect stereo.
   Show "Philips USB PC Link stereo detected" only after a supported profile is
   matched. For unknown devices, show "Unsupported Philips USB device" and offer
   diagnostics export instead of installing anything.
2. Prepare control driver.
   Explain that only the control interface changes and audio remains on the
   Windows USB Audio driver. Show the exact target: `MI_03`.
3. Activate PC Link.
   Send the wake-up packets and show a success message that tells the user to
   play audio through `Philips Audio Set`.
4. Turn on daily-use options.
   Offer `Start with Windows`, `Auto-reactivate after reconnect`, and
   `Enable radio buttons`.

Recommended primary button labels:

- `Install Control Driver`
- `Activate PC Link`
- `Reactivate`
- `Open Sound Settings`
- `Export Diagnostics`
- `Restore Windows HID Driver`

## Main status model

Use a single prominent state area with a short status and one next action.

Suggested states:

| State | User-facing status | Primary action |
| --- | --- | --- |
| No device | `Connect a Philips USB PC Link stereo` | `Scan Again` |
| Unknown Philips | `This Philips USB device is not supported yet` | `Export Diagnostics` |
| Audio missing | `USB audio interface is not available` | `Open Device Manager` |
| Driver needed | `Control interface needs the WinUSB driver` | `Install Control Driver` |
| Ready | `Ready to activate PC Link` | `Activate PC Link` |
| Active | `PC Link is active` | `Reactivate` |
| Error | `Action failed` | `Export Diagnostics` |

Keep the log available, but do not make it the main experience. A normal user
should understand the next step without reading raw USB output.

## Daily-use settings

Recommended settings area:

- `Start with Windows`
  Stores the app in the current user's Run key and launches minimized.
- `Auto-reactivate after reconnect`
  Sends the activation sequence when a supported stereo appears or when the
  device returns after a USB reset.
- `Enable radio buttons`
  Reads the control interface and translates radio buttons into Windows media
  keys.
- `Capture remote/control reports`
  Temporarily records raw HID reports so model-specific button and remote
  formats can be added safely.

If a setting needs administrator rights, say so beside the action that needs it.
Do not make the whole app look like it always runs elevated.

## Volume UX

Show volume as two layers:

- `Windows volume`: the digital volume exposed by the Philips Audio Set endpoint.
- `Stereo volume`: the amplifier volume controlled on the physical radio.

For the first public release, the safe behavior is to control Windows endpoint
volume and to map radio volume buttons to Windows media volume commands. True
hardware volume sync should remain a research item unless a model-specific USB
Audio Feature Unit write is verified on real hardware.

Recommended copy:

```text
Windows volume controls the PC audio stream. The stereo knob still controls the
amplifier.
```

## Radio button UX

The button feature should have visible health:

- Capture status: `Listening`, `Waiting for stereo`, `Driver not ready`, or
  `Capture failed`.
- Last command: `Play/Pause`, `Stop`, `Next`, `Previous`, `Mute`, `Volume Up`,
  or `Volume Down`.
- Remote/control capture: save a short report file and label unknown bytes as
  unmapped until a model profile is updated.
- A small retry action if capture fails.

The app should not silently claim buttons are enabled if it cannot read the
interrupt endpoint.

## Reconnect behavior

This is central to the product. The user already observed that PC Link can stop
working after leaving the mode and returning to it.

Required UX behavior:

- Tell the user activation is session-based, not a permanent firmware change.
- Poll or subscribe to device changes.
- Debounce reactivation so it does not spam the USB control endpoint.
- Log the reason for activation: manual click, app startup, USB reconnect, or
  PC Link recovery.
- Keep the manual `Reactivate` button visible at all times when the driver is
  ready.

## Diagnostics

For GitHub/public support, add an export that collects:

- App version.
- Windows version.
- Detected Philips device IDs.
- Driver service for `MI_00` and control interface.
- Whether `Philips Audio Set` render endpoint exists.
- Last activation result.
- Last button capture result.
- Logs without private file paths where practical.

This will reduce support friction when adding new models.

## Commercial polish checklist

- No raw USB jargon in the primary status.
- Advanced details are behind `Show details`.
- Error messages always include a recovery action.
- Driver install page shows exactly which interface changes.
- Restore action is visible from settings/help.
- Start-with-Windows state is loaded from the registry on launch.
- Minimized startup actually opens hidden/minimized and starts auto-reactivation.
- The app never claims support for an unknown VID/PID.
- The log has copy/export/clear actions.
- Build output and local research files are excluded from Git.

## Risks

- Installing the driver on the wrong interface can break audio playback. The UI
  must repeat that only `MI_03` is touched.
- Self-signed driver certificates are a trust barrier for public release.
- Unknown Philips models may share marketing language but use a different USB
  protocol.
- Button report bytes may vary between models.
- Remote-control IR commands may not be forwarded over USB on every stereo.
- Windows endpoint volume and stereo hardware volume are not necessarily the
  same control.
- Song title and playlist display from the old Philips/MusicMatch stack is not
  implemented and needs USB capture from Windows XP before it can be promised.
