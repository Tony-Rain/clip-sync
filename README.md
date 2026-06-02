# clip-sync

Clipboard sync between Linux devices over the network. Built for GNOME Wayland.

Syncs **text**, **images**, **files** and **folders** — no flickering, no polling.

## Features

- **Text & Images** — copy on one device, paste on another
- **Files & Folders** — copy in file manager, paste on remote device (file manager, Telegram, etc.)
- **No flickering** — uses GTK3 via XWayland with event-based detection
- **No polling** — reacts to clipboard changes instantly via `owner-change` signal
- **Multi-device** — one server, multiple clients
- **Background mode** — runs as a daemon with `--daemon` flag

## Requirements

- Linux with GNOME + Wayland (GNOME 40+)
- Python 3.6+
- GTK3 + GObject Introspection
- GCC (for building the file clipboard helper)

### Install dependencies

**Arch Linux:**
```bash
sudo pacman -S python-gobject gtk3 gcc
```

**Ubuntu/Debian:**
```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gcc libgtk-3-dev
```

## Install

```bash
git clone https://github.com/YOUR_USERNAME/clip-sync.git
cd clip-sync
make
sudo make install
```

## Usage

```bash
# Machine A — start server
clip-sync

# Machine B — connect to server
clip-sync 192.168.0.100

# Run in background (no terminal needed)
clip-sync --daemon
clip-sync --daemon 192.168.0.100

# Stop
clip-sync --stop

# View logs (background mode)
tail -f ~/.clip-sync.log
```

## How it works

**Clipboard detection** — GTK3 runs with `GDK_BACKEND=x11` (XWayland). The `owner-change` signal fires when any app copies something — no polling needed.

**Text & Images** — read and written via GTK3 natively. No external tools, no subprocess spawning, no flickering.

**Files & Folders** — a small C helper (`clip-set-files`) sets the clipboard with three MIME types simultaneously:
- `x-special/gnome-copied-files` — for file managers (Nautilus)
- `text/uri-list` — for apps like Telegram
- `text/plain` — file path as text

Received files are saved to `~/Downloads/clip-sync/` (auto-detected via `xdg-user-dir`).

**Network** — simple TCP server/client. One server accepts multiple clients. When any device copies something, the change is broadcast to all connected peers.

**Loop prevention** — hash comparison + cooldown timer prevent clipboard changes from bouncing between devices.

## Limitations

- GNOME Wayland only (KDE/Sway not tested)
- Max transfer size: 200 MB per clipboard operation
- Files are loaded into memory during transfer
- No encryption (use on trusted local networks)

## Uninstall

```bash
sudo make uninstall
```

## License

MIT
