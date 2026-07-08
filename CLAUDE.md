# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

- Chat with the user in **Russian**.
- Everything written to disk stays in **English**: source code, identifiers, comments, commit messages, PR descriptions, README, docs, log/print strings, error messages, CLI help.
- Do not translate existing English strings in the codebase to Russian.

## Build / Install / Run

```bash
make                    # Build the C helper (clip-set-files)
sudo make install       # Install both binaries to /usr/local/bin/
make clean              # Remove built helper
sudo make uninstall     # Or: sudo clip-sync --uninstall

# Run
clip-sync                       # server
clip-sync <ip>                  # client
clip-sync --daemon [<ip>]       # background (forks, logs to ~/.clip-sync.log)
clip-sync --stop                # pkill -f clip-sync
tail -f ~/.clip-sync.log
```

There are no tests or linters. The installed script at `/usr/local/bin/clip-sync` may be newer than the repo — diff before assuming the repo is authoritative.

## Architecture

Two processes cooperate to bridge GNOME Wayland's clipboard over TCP:

**`clip-sync.py`** — long-running GTK3 app + TCP peer. Runs under `GDK_BACKEND=x11` (XWayland) because Wayland's clipboard API doesn't expose the events we need. Detection is purely event-driven via `Gtk.Clipboard`'s `owner-change` signal — no polling. One `ClipSync` instance runs either `server_loop` (listen on 9876, accept many clients, re-broadcast to peers excluding the sender) or `client_loop` (connect + auto-reconnect every 2s). GTK main loop runs on the main thread; network loops run on daemon threads and marshal clipboard writes back via `GLib.idle_add`.

**`clip-set-files`** (C, built from `clip-set-files.c`) — one-shot helper. Python GTK bindings lack `gtk_clipboard_set_with_data`, so setting file URIs with the three MIME types Nautilus/Telegram expect (`x-special/gnome-copied-files`, `text/uri-list`, `text/plain`) requires this native helper. Python spawns it via `subprocess.Popen`; the helper owns the clipboard until another app takes it (then `clear_func` → `gtk_main_quit`) or a 5-minute timeout. `HELPER_PATH` is discovered at import time by scanning script dir, `/usr/local/bin`, `~/.local/bin`, `~/`.

**Wire format** — `!BI` header (1-byte type, 4-byte big-endian length) then payload. Types: 1=text (utf-8), 2=image (PNG bytes), 3=files. Files payload = `!I` meta-length + JSON `[{name, size}, ...]` + concatenated raw file bytes. `MAX_SIZE = 200 MB`; everything is buffered in memory.

**Loop prevention** — three mechanisms combined: (1) MD5 of last payload compared before send *and* set on receive to skip echoes; (2) `setting_clipboard` flag guards our own `set_*` calls from firing `owner-change`; (3) `COOLDOWN = 2.0s` via `last_activity` timestamp suppresses rapid bounces. When a received file happens to be a lone image (`IMAGE_EXTENSIONS`), it's re-injected as a pixbuf instead of a file URI so pasting into chat apps works.

**Sync-dir guard** — `is_from_sync_dir` blocks re-broadcasting files whose URIs all live inside `DOWNLOAD_DIR` (from `xdg-user-dir DOWNLOAD` + `/clip-sync`, fallback `~/Downloads/clip-sync`). Without this, copying a just-received file would echo back.

**TCP keepalive** — both server-accepted and client sockets set `SO_KEEPALIVE` with `TCP_KEEPIDLE=10, KEEPINTVL=5, KEEPCNT=3` so dead peers (suspend, network drop) surface within ~25s and trigger the client's reconnect loop.

**Daemon mode** — `--daemon` must fork *before* any GTK/threading import, so the argv check and `os.fork()`/`setsid` happen at the very top of the script before `import gi`.

## External integration (informational — lives outside this repo)

clip-sync is wrapped by Deskflow launchers on the user's machines so the clipboard sync starts alongside the KVM session:
- Ubuntu Flatpak: modified `.desktop` launches a `deskflow-clip` wrapper.
- Arch native: `/usr/bin/deskflow` is replaced by a wrapper; original is at `/usr/bin/deskflow.real`.

These wrappers are not in this repo; don't recreate them here unless asked.
