#!/usr/bin/env python3
"""
clip-sync - Clipboard sync between devices (GNOME Wayland).
Supports text, images, files and folders. Multi-device.
Uses GTK3 via XWayland — no polling, no flickering.

Usage:
    clip-sync                       # Start server
    clip-sync <server_ip>           # Connect to server
    clip-sync --daemon              # Server in background
    clip-sync --daemon <server_ip>  # Client in background
    clip-sync --stop                # Stop running instance
"""

import os
import sys
import subprocess

# --stop: kill running instance
if "--stop" in sys.argv:
    subprocess.run(["pkill", "-f", "clip-sync"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(0)

# Daemon: fork BEFORE any GTK/threads
if "--daemon" in sys.argv:
    sys.argv.remove("--daemon")
    pid = os.fork()
    if pid > 0:
        os._exit(0)
    os.setsid()
    log = os.path.expanduser("~/.clip-sync.log")
    sys.stdout = open(log, "a", buffering=1)
    sys.stderr = sys.stdout
    sys.stdin = open(os.devnull, "r")

os.environ["GDK_BACKEND"] = "x11"

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

import socket
import struct
import threading
import hashlib
import json
import time
import urllib.parse

PORT = 9876
MAX_SIZE = 200 * 1024 * 1024
COOLDOWN = 2.0
HELPER_NAME = "clip-set-files"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

TYPE_TEXT = 1
TYPE_IMAGE = 2
TYPE_FILES = 3


def get_download_dir():
    try:
        r = subprocess.run(["xdg-user-dir", "DOWNLOAD"],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            return os.path.join(r.stdout.strip(), "clip-sync")
    except Exception:
        pass
    return os.path.expanduser("~/Downloads/clip-sync")


def find_helper():
    for p in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), HELPER_NAME),
        os.path.join("/usr/local/bin", HELPER_NAME),
        os.path.join(os.path.expanduser("~/.local/bin"), HELPER_NAME),
        os.path.expanduser(f"~/{HELPER_NAME}"),
    ]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


DOWNLOAD_DIR = get_download_dir()
HELPER_PATH = find_helper()


class ClipSync:
    def __init__(self, mode, peer_ip=None):
        self.mode = mode
        self.peer_ip = peer_ip
        self.peers = []
        self.peers_lock = threading.Lock()
        self.last_hash = None
        self.setting_clipboard = False
        self.last_activity = 0
        self._file_helper = None

        Gtk.init([])

        self.window = Gtk.Window()
        self.window.set_default_size(1, 1)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_decorated(False)
        self.window.set_accept_focus(False)
        self.window.move(-100, -100)
        self.window.show()

        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.clipboard.connect("owner-change", self.on_owner_change)

    @property
    def connected(self):
        with self.peers_lock:
            return len(self.peers) > 0

    # ─── Clipboard events ───

    def on_owner_change(self, clipboard, event):
        if self.setting_clipboard:
            return
        if time.time() - self.last_activity < COOLDOWN:
            return
        if not self.connected:
            return
        GLib.idle_add(self.read_and_send)

    def read_and_send(self):
        if self.setting_clipboard:
            return False
        msg_type, data = self.get_clipboard()
        if data and not self.is_same_hash(data):
            self.set_hash(data)
            self.last_activity = time.time()
            self.broadcast(msg_type, data)
        return False

    # ─── Clipboard read ───

    def get_clipboard(self):
        success, targets = self.clipboard.wait_for_targets()
        if not success:
            return None, None
        target_names = [t.name() for t in targets]

        if "x-special/gnome-copied-files" in target_names:
            atom = Gdk.Atom.intern("x-special/gnome-copied-files", False)
            sel = self.clipboard.wait_for_contents(atom)
            if sel:
                text = sel.get_data().decode("utf-8", errors="ignore")
                if text.startswith("copy\n"):
                    uris = [u.strip() for u in text.strip().split("\n")[1:]
                            if u.strip()]
                    if uris and not self.is_from_sync_dir(uris) \
                            and self.files_exist(uris):
                        data = self.pack_files(uris)
                        if data:
                            return TYPE_FILES, data

        pixbuf = self.clipboard.wait_for_image()
        if pixbuf:
            success, buf = pixbuf.save_to_bufferv("png", [], [])
            if success and len(buf) > 8:
                return TYPE_IMAGE, buf

        text = self.clipboard.wait_for_text()
        if text:
            return TYPE_TEXT, text.encode("utf-8")

        return None, None

    # ─── Clipboard write ───

    def set_clipboard_data(self, msg_type, data):
        self.setting_clipboard = True
        self.set_hash(data)
        try:
            if msg_type == TYPE_FILES:
                files = self.unpack_files(data)
                saved = self.save_files(files)
                if not saved:
                    return
                if len(saved) == 1 and self.is_image_file(saved[0]):
                    img_data = open(saved[0], "rb").read()
                    self.set_hash(img_data)
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(img_data)
                    loader.close()
                    self.clipboard.set_image(loader.get_pixbuf())
                else:
                    self._set_files_clipboard(saved)
            elif msg_type == TYPE_IMAGE:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                self.clipboard.set_image(loader.get_pixbuf())
            else:
                self.clipboard.set_text(data.decode("utf-8", errors="replace"), -1)
        except Exception as e:
            print(f"[!] Clipboard write error: {e}", flush=True)
        finally:
            self.last_activity = time.time()
            GLib.timeout_add(1000, self._reset_setting_flag)

    def _set_files_clipboard(self, paths):
        if not HELPER_PATH:
            print("[!] clip-set-files helper not found", flush=True)
            return
        try:
            if self._file_helper and self._file_helper.poll() is None:
                self._file_helper.terminate()
            self._file_helper = subprocess.Popen(
                [HELPER_PATH] + paths,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"[!] clip-set-files error: {e}", flush=True)

    def _reset_setting_flag(self):
        self.setting_clipboard = False
        return False

    # ─── Hash ───

    def set_hash(self, data):
        self.last_hash = hashlib.md5(data).hexdigest()

    def is_same_hash(self, data):
        return hashlib.md5(data).hexdigest() == self.last_hash

    # ─── Files ───

    def is_from_sync_dir(self, uris):
        for uri in uris:
            if uri.startswith("file://"):
                if not urllib.parse.unquote(uri[7:]).startswith(DOWNLOAD_DIR + "/"):
                    return False
        return True

    def files_exist(self, uris):
        for uri in uris:
            if uri.startswith("file://"):
                if os.path.exists(urllib.parse.unquote(uri[7:])):
                    return True
        return False

    def pack_files(self, uris):
        meta, chunks, total = [], [], 0
        for uri in uris:
            path = urllib.parse.unquote(uri[7:]) if uri.startswith("file://") else uri
            if os.path.isfile(path):
                try:
                    d = open(path, "rb").read()
                    meta.append({"name": os.path.basename(path), "size": len(d)})
                    chunks.append(d)
                    total += len(d)
                except Exception:
                    pass
            elif os.path.isdir(path):
                base = os.path.basename(path)
                for root, dirs, files in os.walk(path):
                    for fn in files:
                        fp = os.path.join(root, fn)
                        rel = os.path.join(base, os.path.relpath(fp, path))
                        try:
                            d = open(fp, "rb").read()
                            meta.append({"name": rel, "size": len(d)})
                            chunks.append(d)
                            total += len(d)
                        except Exception:
                            pass
        if not meta or total > MAX_SIZE:
            return None
        mj = json.dumps(meta).encode("utf-8")
        return struct.pack("!I", len(mj)) + mj + b"".join(chunks)

    def unpack_files(self, data):
        ml = struct.unpack("!I", data[:4])[0]
        meta = json.loads(data[4:4 + ml])
        off = 4 + ml
        result = []
        for f in meta:
            result.append((f["name"], data[off:off + f["size"]]))
            off += f["size"]
        return result

    def save_files(self, files):
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        saved, saved_dirs = [], set()
        for name, data in files:
            path = os.path.join(DOWNLOAD_DIR, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            top = name.split(os.sep)[0] if os.sep in name else name
            top_path = os.path.join(DOWNLOAD_DIR, top)
            if top_path not in saved_dirs:
                saved_dirs.add(top_path)
                if top_path not in saved:
                    saved.append(top_path)
        if len(saved) == 1:
            print(f"    Saved: {saved[0]}", flush=True)
        else:
            print(f"    Saved: {len(files)} files -> {DOWNLOAD_DIR}", flush=True)
        return saved

    def is_image_file(self, p):
        return os.path.splitext(p)[1].lower() in IMAGE_EXTENSIONS

    # ─── Network ───

    def broadcast(self, msg_type, data, exclude=None):
        msg = struct.pack("!BI", msg_type, len(data)) + data
        kind = {TYPE_FILES: "files", TYPE_IMAGE: "image"}.get(msg_type, "text")
        with self.peers_lock:
            dead = []
            for sock in self.peers:
                if sock is exclude:
                    continue
                try:
                    sock.sendall(msg)
                except Exception:
                    dead.append(sock)
            for sock in dead:
                self.peers.remove(sock)
                try:
                    sock.close()
                except Exception:
                    pass
        print(f"[>] Sent: {kind} ({len(data)} bytes)", flush=True)

    def recv_exact(self, sock, n):
        buf = b""
        while len(buf) < n:
            c = sock.recv(min(n - len(buf), 65536))
            if not c:
                return None
            buf += c
        return buf

    def recv_msg(self, sock):
        h = self.recv_exact(sock, 5)
        if not h:
            return None, None
        t, l = struct.unpack("!BI", h)
        if l > MAX_SIZE:
            return None, None
        d = self.recv_exact(sock, l)
        return (t, d) if d else (None, None)

    def handle_peer(self, sock, addr):
        print(f"[+] Connected: {addr}", flush=True)
        try:
            while True:
                msg_type, data = self.recv_msg(sock)
                if data is None:
                    print(f"[-] Disconnected: {addr}", flush=True)
                    return
                kind = {TYPE_FILES: "files", TYPE_IMAGE: "image"}.get(msg_type, "text")
                print(f"[<] Received: {kind} ({len(data)} bytes)", flush=True)
                self.last_activity = time.time()
                GLib.idle_add(self.set_clipboard_data, msg_type, data)
                if self.mode == "server":
                    self.broadcast(msg_type, data, exclude=sock)
        except Exception as e:
            print(f"[-] Error with {addr}: {e}", flush=True)
        finally:
            with self.peers_lock:
                if sock in self.peers:
                    self.peers.remove(sock)
            try:
                sock.close()
            except Exception:
                pass

    def server_loop(self):
        while True:
            srv = None
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("0.0.0.0", PORT))
                srv.listen(5)
                print(f"[~] Listening on port {PORT}...", flush=True)
                while True:
                    conn, addr = srv.accept()
                    with self.peers_lock:
                        self.peers.append(conn)
                    threading.Thread(
                        target=self.handle_peer,
                        args=(conn, addr[0]),
                        daemon=True
                    ).start()
            except OSError as e:
                print(f"[!] Server error: {e}", flush=True)
                time.sleep(2)
            finally:
                if srv:
                    try:
                        srv.close()
                    except Exception:
                        pass

    def client_loop(self):
        while True:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect((self.peer_ip, PORT))
                sock.settimeout(None)
                with self.peers_lock:
                    self.peers.append(sock)
                self.handle_peer(sock, self.peer_ip)
            except (ConnectionRefusedError, socket.timeout, OSError):
                pass
            print("[~] Reconnecting...", flush=True)
            time.sleep(2)

    # ─── Main ───

    def run(self):
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        print(f"[*] clip-sync | Mode: {self.mode.upper()} | PID: {os.getpid()}", flush=True)
        if self.peer_ip:
            print(f"[*] Peer: {self.peer_ip}:{PORT}", flush=True)
        print(f"[*] Files -> {DOWNLOAD_DIR}", flush=True)
        if HELPER_PATH:
            print(f"[*] Helper: {HELPER_PATH}", flush=True)
        else:
            print("[!] clip-set-files not found — file paste disabled", flush=True)

        loop = self.server_loop if self.mode == "server" else self.client_loop
        threading.Thread(target=loop, daemon=True).start()

        try:
            Gtk.main()
        except KeyboardInterrupt:
            print("\n[*] Stopped.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        ClipSync("server").run()
    elif len(sys.argv) == 2:
        ClipSync("client", sys.argv[1]).run()
    else:
        print("Usage:")
        print(f"  {sys.argv[0]}                       # Server")
        print(f"  {sys.argv[0]} <server_ip>            # Client")
        print(f"  {sys.argv[0]} --daemon               # Server (background)")
        print(f"  {sys.argv[0]} --daemon <server_ip>   # Client (background)")
        print(f"  {sys.argv[0]} --stop                  # Stop")
        sys.exit(1)
