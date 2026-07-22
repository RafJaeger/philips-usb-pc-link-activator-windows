# Philips USB PC Link compatibility research

Date: 2026-07-22

This note tracks known and likely Philips "USB PC Link" systems for the public
app. It does not change code. The safest initial support target is the Philips
composite USB audio family that enumerates as `Philips Audio Set` with
`VID_0471&PID_0111`, especially revision `Rev_0402`.

## Known protocol

For `0471:0111`, the working activation sequence is two vendor control writes:

```text
bmRequestType = 0x40, bRequest = 0x04, wValue = 0x0000, wIndex = 0xa4ef, data = 01
bmRequestType = 0x40, bRequest = 0x04, wValue = 0x0000, wIndex = 0xa4f0, data = ff
```

Sources:

- Public helper `nheir/usb_pc_link` documents the exact packets, targets
  vendor/product `0471:0111`, and says the command must be run after plugging in:
  https://github.com/nheir/usb_pc_link
- Ubuntu BR thread republishes the same code and reports successful loud output
  on MCM530 and FWM922 after running it:
  https://ubuntuforum-br.org/index.php/topic%2C6024.15.html
- Local extracted Philips driver `uacflt.inf` supports
  `USB\VID_0471&PID_0110` and `USB\VID_0471&PID_0111&Rev_0402`. Its plugin data
  begins with the same logical writes to `a4ef=01` and `a4f0=ff`.
- Local bench result on FWM589: keeping `MI_00` on Windows USB Audio and binding
  only `MI_03` to WinUSB allowed the app to send the two packets and restored
  loud PC Link output.

## Model evidence

| Model / family | Evidence level | USB ID evidence | Protocol expectation | Notes |
| --- | --- | --- | --- | --- |
| FWM589 / FW-M589 | Confirmed locally | Local unit is `0471:0111`, `Rev_0402` | Confirmed `a4ef/a4f0` | Manual requires USB PC Link Driver, Philips Sound Agent 2 and MusicMatch; Sound Agent 2 minimum is Windows 2000/XP. Manual source: https://www.manualslib.com/manual/425019/Philips-Fwm589-19.html?page=14 |
| MCM530 / MC-M530 | Confirmed by public helper and forum | Public helper uses `0471:0111`; forum reports same class of device | Confirmed by source reports | `nheir/usb_pc_link` says it works with MC-M530. Philips spec says USB PC Link, remote navigation, title display, Sound Agent 2 for Windows 2000/XP. Sources: https://github.com/nheir/usb_pc_link and https://www.documents.philips.com/assets/20231204/12c251712a78462087b7b0ce0046c402.pdf |
| MCM590 | Strong candidate | Ubuntu BR post shows MCM590 enumerating as `0471:0111` | Very likely `a4ef/a4f0` | Philips quick guide groups MCM530 and MCM590 under the same USB PC Link install path and Sound Agent 2. Sources: https://ubuntuforum-br.org/index.php/topic%2C6024.15.html and https://www.documents.philips.com/assets/20231206/26246a01980e4567b337b0d00079695b.pdf |
| FW-M779 / FWM779 | Strong candidate | Ubuntu BR initial post for FWM779 reports `0471:0111` | Very likely `a4ef/a4f0` | Manual/spec pages confirm USB PC Link, but public protocol proof is inherited from the shared ID and thread. Sources: https://ubuntuforum-br.org/index.php/topic%2C6024.0.html and https://philips.manymanuals.com/cd-players/fwm779-19/user-manual-44749 |
| FWM922 | Strong candidate | No USB ID seen in source, but same Ubuntu BR thread reports success with the same command | Likely `a4ef/a4f0` | Treat as opt-in/tested-by-user until a device snapshot confirms VID/PID. Source: https://ubuntuforum-br.org/index.php/topic%2C6024.15.html |
| FW-M777 / FWM777 | Probable | Forum user says FWM777 works as Philips USB audio; no explicit ID captured for that unit | Likely, not proven | Manual includes USB PC Link Driver and PC controls. Source: https://www.manualslib.com/manual/175381/Philips-Fw-M777-25.html |
| FW-C577 | Probable | No USB ID found | Unknown, likely same generation | Manual confirms USB PC Link and PC Link play/pause controls. Source: https://www.manualslib.com/manual/128532/Philips-Fw-C577.html |
| FWM799 / FWM799/22 | Probable | No USB ID found | Unknown, likely same generation | Manual/spec pages mention USB PC Link Driver, Sound Agent 2 and MusicMatch. Source: https://www.manualslib.com/manual/179640/Philips-Fwm799-22.html |
| MC-M570 / MC-500 / FW-M567 / FW-M569 / FW-C777 / MCM595 | Possible | No direct USB ID found in this pass | Unknown | Manual cross-references and search results show USB PC Link pages and the same old software stack, but these need real hardware snapshots before automatic support. Example MC-M570 source: https://drivers.plus/it/philips-mc-m570-37-micro-hi-fi-system-driver-1-2-0-20/18396/ |

## App compatibility rules

1. Default supported device should be `VID_0471&PID_0111`, product string
   `Philips Audio Set`, with an audio streaming interface plus HID/control
   interface.
2. On Windows, do not replace the whole composite device and do not replace the
   audio streaming interface. The working FWM589 setup only replaced the control
   interface `MI_03` with WinUSB; `MI_00` stayed on `usbaudio`.
3. Activation is not permanent. The helper and our local test both indicate that
   the two-packet command must be resent after USB reconnect and often after
   leaving/returning to PC Link.
4. Add `PID_0110` only as an experimental/manual mode. The old Philips INF lists
   it, but no public model-to-ID confirmation was found during this pass.
5. Do not auto-target generic Philips USB devices. Vendor `0471` covers many
   unrelated devices, including webcams, remotes, MP3 players and digital
   speaker systems. Matching only the vendor ID is unsafe.

## Button and display implications

Philips manuals list PC Link controls on the unit/remote, including play/pause,
stop, previous/next track, and playlist/album navigation. The public Linux helper
README says play, pause, forward and backward buttons control Debian once the
device is active.

For the Windows app this means:

- If `MI_03` is bound to WinUSB, Windows will no longer consume the HID media
  buttons directly. The app must read/report the control interface itself and
  translate events into Windows media commands.
- If `MI_03` stays on the Windows HID driver, the buttons may work natively, but
  the app may be blocked from sending the activation vendor requests. A filter
  driver or a custom interface-driver strategy may eventually be needed for the
  most polished commercial version.
- Song title/playlist display was part of the original MusicMatch integration on
  some models, but no public packet protocol for display metadata was found.

## Volume implications

Do not assume Windows volume controls Philips hardware volume on every model.
The public Linux helper notes that Debian volume does not control the Philips
volume, while the radio volume does. The FWM589 manual also warns users to check
PC mute, so PC-side software volume still matters.

Commercial app recommendation:

- Keep normal Windows endpoint volume support where the stock audio driver
  exposes it.
- Add optional software pre-gain/attenuation only if we build an audio-routing
  mode; avoid clipping.
- Investigate USB Audio Feature Unit volume controls per model, but do not
  promise hardware volume sync until tested on real devices.

## Risks in generalizing

- "USB PC Link" is a marketing feature, not a guaranteed USB protocol name.
  Later Philips products may have front USB mass-storage playback, which is a
  different feature and must not be touched by this app.
- Some manuals confirm the old software stack but not USB IDs. Those models
  should appear as "experimental" until users submit Device Manager or `lsusb`
  snapshots.
- Replacing the wrong interface driver can break audio output, HID controls, or
  other USB functions. The installer must show the exact interface it will bind.
- The `nheir/usb_pc_link` code is GPL-2.0. We can document the protocol facts,
  but if code is copied or derived, the app licensing must comply.

## Next data to collect

- Device Manager hardware IDs and USB descriptors for MCM530, MCM590, FWM779,
  FWM922, FW-C577, FW-M777, FWM799, MC-M570 and MC-500.
- HID report bytes for buttons after `MI_03` is bound to WinUSB.
- A Windows XP USB capture from the original Philips stack to recover display
  metadata, playlist navigation and any extra Sound Agent 2 mixer commands.
