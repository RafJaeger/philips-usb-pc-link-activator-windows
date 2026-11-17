# Third-Party Notices

This project uses:

- PySide6 for the desktop UI.
- libusb1 and libusb-package for WinUSB/libusb access.
- pycaw and comtypes for Windows audio endpoint volume/default-device control.
- PyInstaller for Windows packaging.
- A libwdi/Zadig-generated WinUSB driver package for the validated Philips
  control interface.

The PC Link activation sequence is documented as interoperability information
from public Linux community work and local testing. Do not copy GPL source code
into this project unless the project license is changed accordingly.

See:

- https://github.com/nheir/usb_pc_link
- https://zadig.akeo.ie/
- https://github.com/pbatard/libwdi

## Equalizer APO (bundled installer)

The app bundles the unmodified official installer of Equalizer APO 1.4.2
(`vendor/equalizerapo/EqualizerAPO-x64-1.4.2.exe`, SHA-256
`7403be7427bbe1936a40dded082829b6e217fc4f5990fee5cba501f0ae055afa`) to provide
the 10-band equalizer on the Philips output. It is only installed when the
user clicks "Ativar equalizador".

- Author: Jonas Thedering and contributors.
- License: GNU General Public License, version 2 (or later).
- Project and complete corresponding source code:
  https://sourceforge.net/projects/equalizerapo/ (source archive
  `EqualizerAPO-src-1.4.2.zip` in the 1.4.2 files folder).

Equalizer APO is a separate program: this app does not contain or modify its
code. It only installs it and writes its configuration file
`philips-pc-link.txt`.

The Sound Agent 2 equalizer layout (bands 31 Hz-16 kHz, Rock/Pop/Jazz/Classic
presets, VBass) is reproduced from the original Philips/QSound control panel
as interoperability information; no QSound code or data is included.
