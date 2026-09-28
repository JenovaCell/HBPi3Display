#!/usr/bin/env python3
"""Tiny status dashboard for a 480x320 SPI TFT (ILI9486) on a headless Raspberry Pi.

Mirrors the basics of the Homebridge UI status page: CPU load/temp, memory,
network, uptime, system info and the tail of the Homebridge log.

Draws with Pillow straight into the Linux framebuffer (no X11 needed), so it
works with either the fbtft or the DRM driver behind the `piscreen` overlay.

    hbdisplay.py                 run on the display
    hbdisplay.py --png out.png   render one frame to a PNG (for previewing)
"""
import argparse
import array
import glob
import os
import re
import socket
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320

BG = (0, 0, 0)
TILE = (22, 22, 26)
TILE_HI = (44, 36, 78)
FG = (235, 235, 235)
DIM = (140, 140, 150)
ACCENT = (140, 100, 235)
GOOD = (60, 200, 90)
BAD = (235, 60, 60)
WARN = (255, 160, 0)

SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


F_HEAD = font(SANS_B, 14)
F_BIG = font(SANS_B, 22)
F_LBL = font(SANS, 10)
F_SMALL = font(SANS, 11)
F_LOG = font(MONO, 9)


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------
class Stats:
    def __init__(self, log_path, iface):
        self.log_path = log_path
        self.iface = iface
        self._cpu = self._read_cpu()
        self._net = (time.time(), *self._read_net())
        self.cpu_pct = 0.0
        self.rx = self.tx = 0.0  # bytes/sec

    @staticmethod
    def _read_cpu():
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + v[4]  # total, idle+iowait

    def _read_net(self):
        with open("/proc/net/dev") as f:
            for line in f:
                name, _, rest = line.partition(":")
                if name.strip() == self.iface:
                    c = rest.split()
                    return int(c[0]), int(c[8])
        return 0, 0

    def update(self):
        total, idle = self._read_cpu()
        dt, di = total - self._cpu[0], idle - self._cpu[1]
        self.cpu_pct = 100.0 * (dt - di) / dt if dt else 0.0
        self._cpu = (total, idle)

        now = time.time()
        rx, tx = self._read_net()
        el = max(now - self._net[0], 0.001)
        self.rx = max(rx - self._net[1], 0) / el
        self.tx = max(tx - self._net[2], 0) / el
        self._net = (now, rx, tx)

    @staticmethod
    def temp_c():
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read()) / 1000.0
        except OSError:
            return None

    @staticmethod
    def memory():
        m = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                m[k] = int(v.split()[0]) * 1024
        return m["MemTotal"], m.get("MemAvailable", m["MemFree"])

    @staticmethod
    def uptime():
        with open("/proc/uptime") as f:
            s = int(float(f.read().split()[0]))
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        return f"{d}d {h}h" if d else f"{h}h {s // 60}m"

    @staticmethod
    def ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # no packet is actually sent
            return s.getsockname()[0]
        except OSError:
            return "no network"
        finally:
            s.close()

    @staticmethod
    def homebridge_running():
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "homebridge"],
                capture_output=True, text=True, timeout=3,
            )
            return r.stdout.strip() == "active"
        except (OSError, subprocess.SubprocessError):
            return None

    def log_tail(self, n):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(f.tell() - 8192, 0))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return ["(log not readable: %s)" % self.log_path]
        return [ANSI.sub("", ln) for ln in lines[-n:]]


def rate(bps):
    bits = bps * 8
    if bits >= 1e6:
        return f"{bits / 1e6:.1f} Mb/s"
    if bits >= 1e3:
        return f"{bits / 1e3:.0f} Kb/s"
    return f"{bits:.0f} b/s"


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------
def tile(d, x, y, w, h, label, value, sub=None, bar=None, color=FG, hi=False):
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=TILE_HI if hi else TILE)
    d.text((x + 6, y + 4), label, font=F_LBL, fill=DIM)
    d.text((x + w // 2, y + 31), value, font=F_BIG, fill=color, anchor="mm")
    if sub:
        d.text((x + w // 2, y + 48), sub, font=F_SMALL, fill=DIM, anchor="mm")
    if bar is not None:
        d.rectangle([x + 6, y + h - 7, x + w - 7, y + h - 4], fill=(50, 50, 58))
        fill_w = int((w - 13) * min(max(bar, 0), 100) / 100)
        d.rectangle([x + 6, y + h - 7, x + 6 + fill_w, y + h - 4], fill=ACCENT)


def render(stats, temp_unit="F"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Header
    running = stats.homebridge_running()
    dot = GOOD if running else BAD if running is False else WARN
    d.text((6, 4), "Status", font=F_HEAD, fill=ACCENT)
    d.ellipse([W - 14, 8, W - 6, 16], fill=dot)
    d.text((W - 22, 10), stats.ip(), font=F_SMALL, fill=DIM, anchor="rm")

    # Row 1: CPU load / temp / memory / uptime
    tc = stats.temp_c()
    if tc is None:
        temp, tcol = "--", FG
    else:
        val = tc * 9 / 5 + 32 if temp_unit == "F" else tc
        temp, tcol = f"{val:.0f}°{temp_unit}", (BAD if tc >= 75 else WARN if tc >= 65 else FG)
    total, avail = stats.memory()
    used_pct = 100 * (total - avail) / total

    y, th, gw = 26, 66, 4
    tw = (W - 5 * gw) // 4
    xs = [gw + i * (tw + gw) for i in range(4)]
    tile(d, xs[0], y, tw, th, "CPU Load", f"{stats.cpu_pct:.0f}%", bar=stats.cpu_pct)
    tile(d, xs[1], y, tw, th, "CPU Temp", temp, color=tcol)
    tile(d, xs[2], y, tw, th, "Memory", f"{avail / 2**30:.2f} GB",
         sub=f"free of {total / 2**30:.2f}", bar=used_pct, hi=True)
    tile(d, xs[3], y, tw, th, "Uptime", stats.uptime())

    # Row 2: network + system info
    y2, h2 = y + th + gw, 62
    half = (W - 3 * gw) // 2
    d.rectangle([gw, y2, gw + half - 1, y2 + h2 - 1], fill=TILE)
    d.text((gw + 6, y2 + 4), f"Network ({stats.iface})", font=F_LBL, fill=DIM)
    d.text((gw + half // 4, y2 + 34), rate(stats.rx), font=F_HEAD, fill=FG, anchor="mm")
    d.text((gw + half * 3 // 4, y2 + 34), rate(stats.tx), font=F_HEAD, fill=FG, anchor="mm")
    d.text((gw + half // 4, y2 + 51), "Received", font=F_LBL, fill=DIM, anchor="mm")
    d.text((gw + half * 3 // 4, y2 + 51), "Sent", font=F_LBL, fill=DIM, anchor="mm")

    x2 = gw * 2 + half
    d.rectangle([x2, y2, x2 + half - 1, y2 + h2 - 1], fill=TILE)
    d.text((x2 + 6, y2 + 4), "System", font=F_LBL, fill=DIM)
    info = [("Host", socket.gethostname()),
            ("Homebridge", "running" if running else "stopped" if running is False else "unknown"),
            ("Load", " ".join(f"{v:.2f}" for v in os.getloadavg()))]
    for i, (k, v) in enumerate(info):
        d.text((x2 + 8, y2 + 19 + i * 14), k, font=F_SMALL, fill=DIM)
        d.text((x2 + 78, y2 + 19 + i * 14), v, font=F_SMALL, fill=FG)

    # Log panel
    y3 = y2 + h2 + gw
    d.rectangle([gw, y3, W - gw - 1, H - gw - 1], fill=TILE)
    d.text((gw + 6, y3 + 3), "Homebridge Logs", font=F_LBL, fill=DIM)
    rows = (H - gw - y3 - 18) // 11
    cols = (W - 2 * gw - 12) // 5  # DejaVu Mono 9px is ~5.4px wide; truncated below
    for i, line in enumerate(stats.log_tail(rows)):
        col = BAD if re.search(r"fail|error|EHOSTUNREACH", line, re.I) else FG
        d.text((gw + 6, y3 + 17 + i * 11), line[: cols - 6], font=F_LOG, fill=col)
    return img


# --------------------------------------------------------------------------
# Framebuffer output
# --------------------------------------------------------------------------
def find_fb(match):
    """Return /dev/fbN whose driver name contains `match` (default: ili9486)."""
    for name_file in sorted(glob.glob("/sys/class/graphics/fb*/name")):
        with open(name_file) as f:
            if match.lower() in f.read().lower():
                return "/dev/" + name_file.split("/")[-2]
    raise SystemExit(
        f"No framebuffer matching '{match}' found. Is the overlay loaded? "
        "Check: cat /sys/class/graphics/fb*/name")


def fb_bytes(img, bpp, swap_rb):
    raw = img.tobytes()
    r, g, b = raw[0::3], raw[1::3], raw[2::3]
    if swap_rb:
        r, b = b, r
    if bpp == 16:
        words = array.array("H", [((a & 0xF8) << 8) | ((c & 0xFC) << 3) | (e >> 3)
                                  for a, c, e in zip(r, g, b)])
        if sys.byteorder == "big":
            words.byteswap()
        return words.tobytes()
    if bpp == 32:  # XRGB8888 little-endian => B, G, R, X
        out = bytearray(len(r) * 4)
        out[0::4], out[1::4], out[2::4] = b, g, r
        return bytes(out)
    raise SystemExit(f"Unsupported framebuffer depth: {bpp}bpp")


def write_fb(dev, img, swap_rb):
    sysdir = "/sys/class/graphics/" + os.path.basename(dev)
    with open(sysdir + "/bits_per_pixel") as f:
        bpp = int(f.read())
    with open(sysdir + "/virtual_size") as f:
        vw, _ = (int(x) for x in f.read().split(","))
    if vw != img.width:
        img = img.resize((vw, int(vw * img.height / img.width)))
    data = fb_bytes(img, bpp, swap_rb)
    with open(dev, "wb", buffering=0) as fb:
        fb.write(data)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--png", help="render a single frame to this PNG and exit")
    p.add_argument("--fb", help="framebuffer device (default: auto-detect ILI9486)")
    p.add_argument("--fb-match", default="ili9486", help="substring of fb driver name to auto-detect")
    p.add_argument("--swap-rb", action="store_true", help="swap red/blue if colours look wrong")
    p.add_argument("--celsius", action="store_true", help="show temperature in C instead of F")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between refreshes")
    p.add_argument("--iface", default="eth0", help="network interface to graph")
    p.add_argument("--log", default="/var/lib/homebridge/homebridge.log")
    a = p.parse_args()

    unit = "C" if a.celsius else "F"
    stats = Stats(a.log, a.iface)
    time.sleep(0.5)
    stats.update()

    if a.png:
        render(stats, unit).save(a.png)
        return

    dev = a.fb or find_fb(a.fb_match)
    while True:
        stats.update()
        write_fb(dev, render(stats, unit), a.swap_rb)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
