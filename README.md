# HBPi3Display

A small status screen for a headless Raspberry Pi 3B running Homebridge, on the
3.5" 480x320 GPIO SPI display (ILI9486 driver, XPT2046 resistive touch).
It shows the same basics as the Homebridge UI status page: CPU load and temp,
memory, network throughput, uptime, host/IP, Homebridge service state and the
live Homebridge log (errors in red).

## Install (on the Pi)

1. Power off, seat the display on the first 26 pins of the GPIO header.
2. ```
   git clone <this repo> && cd HBPi3Display
   sudo ./install.sh
   sudo reboot
   ```

The installer adds `dtoverlay=piscreen,speed=16000000,rotate=90` to
`/boot/firmware/config.txt` (this is the stock overlay for ILI9486 SPI panels
and also enables the touch controller), installs `hbdisplay.py` to
`/opt/hbdisplay`, and enables the `hbdisplay` systemd service.

## Check it

```
cat /sys/class/graphics/fb*/name     # expect something containing ili9486
sudo systemctl status hbdisplay
journalctl -u hbdisplay -e
```

## Tweaks

Edit `ExecStart` in `/etc/systemd/system/hbdisplay.service`, then
`sudo systemctl daemon-reload && sudo systemctl restart hbdisplay`.

| Symptom | Fix |
| --- | --- |
| Colours look wrong (blue background) | the service already passes `--swap-rb`; remove it if yours looks right without |
| White screen / garbage | lower `speed=` in config.txt (e.g. `12000000`) |
| Wrong orientation | change `rotate=` (0, 90, 180, 270) |
| No ili9486 framebuffer | try `dtoverlay=piscreen,drm,speed=16000000,rotate=90` instead |
| Celsius | add `--celsius` |
| Wi-Fi instead of Ethernet | add `--iface wlan0` |

Preview without hardware: `python3 hbdisplay.py --png preview.png`
(needs `python3-pil`).

## Notes

- The Pi is headless, so this draws directly to the framebuffer with Pillow;
  no desktop or X11 needed. Refresh is every 2 s (`--interval`).
- Touch isn't used yet; the overlay exposes it as an evdev input if you want
  to add page switching later.
- I haven't been able to test on real hardware, only rendering.
