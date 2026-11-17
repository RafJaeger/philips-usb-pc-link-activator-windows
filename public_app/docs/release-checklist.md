# Release Checklist

- Build on a clean Windows 10 machine.
- Build on a clean Windows 11 machine.
- Confirm the app detects `0471:0111` before offering driver installation.
- Confirm WinUSB is installed only on `MI_03`.
- Confirm `MI_00` remains on Microsoft USB Audio.
- Confirm PC Link activates after install.
- Confirm PC Link reactivates after USB reconnect.
- Confirm PC Link reactivates after leaving PC Link mode and returning.
- Confirm Windows system volume changes the `Philips Audio Set` endpoint.
- Confirm the stereo hardware volume still works independently.
- Confirm Play/Pause, Stop, Next, Previous, Mute, Volume Up, and Volume Down.
- Confirm `Aprender Controle` records front-panel and remote-control reports
  without sending duplicate media keys during capture.
- Confirm FWM/FWC mode sends the standard two-write activation sequence.
- Confirm MCM mode completes Sound Agent startup and keeps the runtime listener active.
- Force one MCM response error; confirm it is logged and the listener handles the next request.
- Force one MCM activation failure; confirm the button remains available and auto-reactivation retries after 10 seconds without reconnecting USB.
- Confirm MCM command `0x61` can show and hide the app window.
- Confirm the saved family selection is restored after restarting the app.
- Confirm `Ativar PC Link` changes to `PC Link Ativo` after a successful
  activation and becomes available again after reconnect/driver loss.
- Confirm Start with Windows writes only the current user's Run key.
- Confirm Restore HID returns `MI_03` to the Windows HID/input driver.
- Generate diagnostics and verify it does not include private user content.
- Publish SHA256 checksums for release files.
