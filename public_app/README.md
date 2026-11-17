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

- Sends the standard FWM/FWC PC Link wake-up sequence used by the original
  Philips stack:

```text
40 04 00 00 a4 ef 01 00 01
40 04 00 00 a4 f0 01 00 ff
```

- In MCM mode, reproduces the Philips Sound Agent 2 startup negotiation and
  keeps its runtime request/response channel active.

- Re-activates PC Link after USB reconnects or after the stereo is switched
  away from PC Link and back.
- Keeps only one app instance running, so a tray copy and a second opened copy
  do not fight for the USB control interface.
- Reads the radio buttons through the control interface and maps them to Windows
  media controls.
- Captures raw control reports so new front-panel or remote-control formats can
  be mapped safely for additional models.
- Provides startup and auto-reactivation options for daily use.
- Saves the selected device family (`FWM/FWC` or `MCM`) between app launches.
- Shows the current song on the stereo display: it answers the stereo's
  title/artist/album requests with the track playing in Windows (the same data
  as the Windows media flyout: Spotify, browsers, Media Player, VLC...) and
  tells the stereo when the song or play state changes. This reproduces
  PhilipsRemote.exe, the Philips component the original CD installed with
  MusicMatch Jukebox. See `docs/PC_LINK_REMOTE_PROTOCOL.md`.
- Executes the stereo's PC commands (play, pause, stop, next, previous, search,
  shuffle, repeat) on the current Windows player, and its PC volume commands on
  the Windows volume.

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
2. Open the app and select the correct family: `FWM/FWC` or `MCM`.
3. Select `PC Link` on the stereo.
4. If prompted, install the WinUSB control driver. This requires administrator
   approval and must apply only to `MI_03`.
5. Click `Activate PC Link`.
6. Set `Philips Audio Set` as the Windows output device.
7. Enable `Auto-reactivate` and `Start with Windows` for normal day-to-day use.

Activation is not permanent. If the USB cable is unplugged, Windows restarts, or
the stereo leaves PC Link mode, the app should activate it again. If Windows no
longer lists the stereo as a present USB device, the app waits for the stereo to
enumerate again instead of repeatedly asking to reinstall a driver that is
already installed.

## Device Family Selection and MCM Behavior

FWM/FWC and MCM models can expose the same USB ID and revision, so the app cannot
distinguish them reliably from descriptors alone. The selected family is saved.
It matters: MCM mode intentionally runs the Sound Agent negotiation instead of
the two-write FWM/FWC sequence, while FWM/FWC mode does not run the MCM
negotiation.

If MCM activation fails, the manual activation button remains available and
auto-reactivation schedules another attempt after 10 seconds without requiring
a USB reconnect. A failure while responding to one runtime request is logged,
but the listener stays active for subsequent requests.

An MCM can send the original Sound Agent command `0x61` to show or hide the app
window. This is expected protocol behavior, not a random window action.

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
front-panel buttons, or may not forward them over USB at all. Use `Aprender
Controle`, follow the prompts and press each requested button to save a mapping.
Unknown reports
should be mapped only after a real capture; the app intentionally avoids turning
random bytes into media keys.

Reports `80 xx` are not buttons: they are requests from the stereo's PC Link
protocol. `80 4a` (`GET_PC_PLAYER1_STATUS`, repeated while the stereo looks
for PC software) is answered, never ignored or learned as a button. Only
`80 80` remains an autonomous status report.

## Interface (0.6.0)

The window follows the look of the early-2000s FWM micro systems: brushed
aluminium header with a status LED, a blue back-lit LCD that mirrors the
stereo display, graphite panels and chrome buttons. Navigation is a large
sidebar (Inicio, Visor, Som, Controle, Configuracoes, Log) plus a complete
menu bar with every option. The home page is a 3-step checklist (WinUSB
driver, PC Link active, stereo as Windows output), each step with its button.

## Display Modes

Modes: music (title and artist as separate pages), clock, PC (CPU/RAM),
internet (download/upload), free text, and auto (all in turn). The stereo's
two playlist buttons (`80 14` / `80 15`) switch modes. Only the music (and a
long free text) scrolls; clock, PC, internet and notices stay still. Notices
(VOL xx%, MUDO, PAUSA, PLAY, mode names) appear at once.

## Equalizer and Tone

- Tone of the stereo itself: bass and treble (-12..+12 dB) and AGC of the
  UAC3553B, set through Windows (IAudioBass/IAudioTreble). No install needed.
- Sound Agent 2 style equalizer: 10 bands (31 Hz-16 kHz), Rock/Pop/Jazz/
  Classic and more presets, user presets and VBass. Processed by Equalizer APO
  (GPL-2.0), bundled and installed by "Ativar equalizador" only on the Philips
  Audio Set output (same registry changes as its Device Selector, with the
  original values backed up; ownership is taken only if Windows denies the
  write, and the original owner/permissions are restored). If that still
  fails, Equalizer APO's own Device Selector opens as a fallback.

## Song Title on the Display

Enable `Mostrar musica no visor do radio` (on by default). The control channel
stays open even when the radio buttons are disabled, because the stereo asks
the PC for the title. Text is sent as plain ASCII (accents are removed and
quotes dropped). The FWM589 display shows 8 characters and does not scroll by
itself, so the app scrolls `Title - Artist` through it (set `Visor` to the
number of characters your model shows; longer text scrolls). The current song
is also shown in the app window.

On FWM/FWC units the reply mailbox is created on first use by writing the same
setup block that the original Philips filter driver wrote on every
`0471:0111 Rev_0402` unit. MCM mode writes it during activation.

## Volume Behavior

The UAC3553B tells Windows it has a hardware volume control (USB Audio feature
unit, -41..0 dB), so Windows sends the volume to the chip instead of
attenuating the audio. The chip ignores that value; only mute works. Result
without a fix: with `Philips Audio Set` as output, the Windows slider and
volume keys only produce 100% or 0% (verified on a FWM589; a player's own
volume, such as YouTube's, still works).

`Corrigir volume do Windows na saida Philips` (on by default) applies the
Windows volume in software: the app follows the Philips endpoint level and
scales every audio session playing on it, like a player's own volume does.
Levels changed in the Windows volume mixer are kept as relative levels, and
all sessions return to their own level when the option is turned off or the
app quits.

There are two volume layers:

- Windows volume (default output, or only the `Philips Audio Set` output).
- The stereo amplifier volume controlled by the physical radio knob.

`Botoes de volume` selects what the radio's volume buttons and PC volume
commands control:

- `Volume do Windows (padrao)`: the Windows volume keys are pressed, so the
  default output changes and the Windows volume indicator appears, exactly as
  with a keyboard volume key.
- `Somente a saida Philips`: only the `Philips Audio Set` endpoint volume.

The stereo can also ask for the PC volume (`GET_PC_VOLUME_STATUS`) and set an
absolute value (`SET_PC_VOLUME`); both use the selected target. If a model
keeps its volume buttons for its own amplifier and never reports them over
USB, Windows cannot see those presses.

The app does not re-encode, resample or route audio through its own mixer. Audio
playback stays on the Philips USB Audio interface exposed to Windows, so quality
is limited by the stereo's USB audio format, the Windows audio engine settings
and the source material. For the cleanest output, avoid clipping in Windows/app
volume and use the Philips endpoint directly.

## Development

```powershell
python -m pip install -r .\requirements.txt
python -m unittest discover -s tests
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
