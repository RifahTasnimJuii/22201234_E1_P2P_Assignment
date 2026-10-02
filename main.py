"""
main.py - Tkinter user interface for the P2P network.

Run with:   python main.py
"""

import os
import queue
import socket
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

from p2p_node import P2PNode

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")


def get_local_ip():
    """Best-effort guess of this computer's LAN IP (no data is actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class App:
    def __init__(self, root):
        self.root = root
        root.title("P2P Network - CSE 433")
        root.minsize(760, 560)

        self.node = None
        self.events = queue.Queue()     # worker threads -> UI thread
        self.peer_ids = []              # listbox index -> peer_id

        self._build_ui()
        self._update_states()
        self.root.after(100, self._process_events)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # --- My Peer
        f1 = ttk.LabelFrame(self.root, text="My Peer")
        f1.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(f1, text="Name:").grid(row=0, column=0, **pad)
        self.name_var = tk.StringVar(value="Peer_Name")
        self.name_entry = ttk.Entry(f1, textvariable=self.name_var, width=16)
        self.name_entry.grid(row=0, column=1, **pad)
        ttk.Label(f1, text="Port:").grid(row=0, column=2, **pad)
        self.port_var = tk.StringVar(value="5000")
        self.port_entry = ttk.Entry(f1, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, **pad)
        self.start_btn = ttk.Button(f1, text="Start Peer", command=self.on_start)
        self.start_btn.grid(row=0, column=4, **pad)
        self.stop_btn = ttk.Button(f1, text="Stop", command=self.on_stop)
        self.stop_btn.grid(row=0, column=5, **pad)
        self.myip_label = ttk.Label(f1, text=f"My IP: {get_local_ip()}")
        self.myip_label.grid(row=0, column=6, padx=12)

        # --- Connect
        f2 = ttk.LabelFrame(self.root, text="Connect to Another Peer")
        f2.pack(fill="x", padx=8, pady=4)
        ttk.Label(f2, text="IP:").grid(row=0, column=0, **pad)
        self.ip_var = tk.StringVar(value="127.0.0.1")
        self.ip_entry = ttk.Entry(f2, textvariable=self.ip_var, width=18)
        self.ip_entry.grid(row=0, column=1, **pad)
        ttk.Label(f2, text="Port:").grid(row=0, column=2, **pad)
        self.rport_var = tk.StringVar(value="5001")
        self.rport_entry = ttk.Entry(f2, textvariable=self.rport_var, width=8)
        self.rport_entry.grid(row=0, column=3, **pad)
        self.connect_btn = ttk.Button(f2, text="Connect", command=self.on_connect)
        self.connect_btn.grid(row=0, column=4, **pad)

        # --- Peers + log
        f3 = ttk.Frame(self.root)
        f3.pack(fill="both", expand=True, padx=8, pady=4)
        f3.columnconfigure(1, weight=1)
        f3.rowconfigure(0, weight=1)

        pf = ttk.LabelFrame(f3, text="Connected Peers")
        pf.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        self.peer_list = tk.Listbox(pf, width=32, exportselection=False)
        self.peer_list.pack(fill="both", expand=True, padx=4, pady=4)

        lf = ttk.LabelFrame(f3, text="Messages / Events")
        lf.grid(row=0, column=1, sticky="nsew")
        self.log_box = scrolledtext.ScrolledText(lf, state="disabled", wrap="word", height=14)
        self.log_box.pack(fill="both", expand=True, padx=4, pady=4)

        # --- Send text
        f4 = ttk.Frame(self.root)
        f4.pack(fill="x", padx=8, pady=4)
        ttk.Label(f4, text="Send Text:").pack(side="left")
        self.msg_var = tk.StringVar()
        self.msg_entry = ttk.Entry(f4, textvariable=self.msg_var)
        self.msg_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.msg_entry.bind("<Return>", lambda e: self.on_send_text())
        self.send_btn = ttk.Button(f4, text="Send", command=self.on_send_text)
        self.send_btn.pack(side="left")

        # --- Send file
        f5 = ttk.Frame(self.root)
        f5.pack(fill="x", padx=8, pady=(4, 8))
        self.file_btn = ttk.Button(f5, text="Choose File & Send", command=self.on_send_file)
        self.file_btn.pack(side="left")
        self.all_btn = ttk.Button(f5, text="Send Text to All", command=self.on_send_all)
        self.all_btn.pack(side="left", padx=6)
        self.disc_btn = ttk.Button(f5, text="Disconnect Selected", command=self.on_disconnect)
        self.disc_btn.pack(side="left")

    def _update_states(self):
        running = self.node is not None
        st_on = "normal" if running else "disabled"
        st_off = "disabled" if running else "normal"
        self.start_btn.config(state=st_off)
        self.name_entry.config(state=st_off)
        self.port_entry.config(state=st_off)
        for w in (self.stop_btn, self.connect_btn, self.send_btn, self.file_btn,
                  self.all_btn, self.disc_btn, self.ip_entry, self.rport_entry, self.msg_entry):
            w.config(state=st_on)

    # -------------------------------------------------------------- logging
    def log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.config(state="normal")
        self.log_box.insert("end", f"[{stamp}] {text}\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def error(self, text):
        self.log(f"[ERROR] {text}")

    def _process_events(self):
        """Runs in the UI thread; drains events posted by network threads."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "peers":
                    self._refresh_peers()
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _refresh_peers(self):
        selected = self._selected_peer_id()
        self.peer_list.delete(0, "end")
        self.peer_ids = []
        if self.node:
            for p in self.node.get_peers():
                self.peer_list.insert("end", p.display())
                self.peer_ids.append(p.peer_id)
        if selected in self.peer_ids:
            self.peer_list.selection_set(self.peer_ids.index(selected))

    def _selected_peer_id(self):
        sel = self.peer_list.curselection()
        if not sel or sel[0] >= len(self.peer_ids):
            return None
        return self.peer_ids[sel[0]]

    # ------------------------------------------------------------- handlers
    def on_start(self):
        name = self.name_var.get().strip()
        if not name:
            return self.error("Peer name cannot be empty")
        try:
            port = int(self.port_var.get())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            return self.error("Invalid port: enter a number between 1 and 65535")

        node = P2PNode(
            name, port,
            log_callback=lambda m: self.events.put(("log", m)),
            peers_callback=lambda: self.events.put(("peers", None)),
            downloads_dir=DOWNLOADS_DIR,
        )
        try:
            node.start()
        except OSError as e:
            return self.error(f"Could not start peer on port {port}: {e}")
        self.node = node
        self._update_states()

    def on_stop(self):
        if self.node:
            self.node.stop()
            self.node = None
        self._refresh_peers()
        self._update_states()

    def on_connect(self):
        ip, port = self.ip_var.get(), self.rport_var.get()

        def work():
            try:
                self.node.connect(ip, port)
            except ConnectionRefusedError:
                self.events.put(("log", "[ERROR] Connection failed: Connection refused "
                                        "(is the other peer running?)"))
            except socket.timeout:
                self.events.put(("log", "[ERROR] Connection failed: timed out"))
            except Exception as e:
                self.events.put(("log", f"[ERROR] Connection failed: {e}"))

        self._run_async(work)

    def on_send_text(self):
        pid = self._selected_peer_id()
        if pid is None:
            return self.error("Please select a peer first")
        text = self.msg_var.get().strip()
        if not text:
            return self.error("Message is empty")
        self.msg_var.set("")
        self._run_async(lambda: self._safe(self.node.send_text, pid, text))

    def on_send_file(self):
        pid = self._selected_peer_id()
        if pid is None:
            return self.error("Please select a peer first")
        path = filedialog.askopenfilename(title="Choose a file to send")
        if not path:
            return
        self._run_async(lambda: self._safe(self.node.send_file, pid, path))

    def on_send_all(self):
        text = self.msg_var.get().strip()
        if not text:
            return self.error("Message is empty")
        self.msg_var.set("")
        self._run_async(lambda: self._safe(self.node.send_text_all, text))

    def on_disconnect(self):
        pid = self._selected_peer_id()
        if pid is None:
            return self.error("Please select a peer first")
        self._safe(self.node.disconnect, pid)

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            self.events.put(("log", f"[ERROR] {e}"))

    @staticmethod
    def _run_async(fn):
        threading.Thread(target=fn, daemon=True).start()

    def on_close(self):
        if self.node:
            self.node.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
