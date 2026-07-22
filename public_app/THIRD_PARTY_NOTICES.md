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
