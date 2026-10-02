"""
p2p_node.py - The P2P networking layer.

Every peer = TCP Server + TCP Client
  * Server role : listens on a port and accepts incoming connections.
  * Client role : connects to the IP:port of another peer.

Each connection (incoming or outgoing) is handled in its own thread, so a
peer can talk to many peers at the same time.  There is no central server.
"""

import ipaddress
import os
import socket
import threading
import time
import uuid

import protocol as proto


class Peer:
    """Information about one connected remote peer."""

    def __init__(self, peer_id, name, sock, address, listen_port):
        self.peer_id = peer_id
        self.name = name
        self.sock = sock
        self.address = address            # (ip, ephemeral_port) of the socket
        self.listen_port = listen_port    # the port that peer listens on
        self.send_lock = threading.Lock() # one sender at a time per socket

    def label(self):
        return f"{self.name} [{self.peer_id}]"

    def display(self):
        return f"{self.name} [{self.peer_id}]  ({self.address[0]}:{self.listen_port})"


class P2PNode:
    def __init__(self, name, port, log_callback=print, peers_callback=None,
                 downloads_dir="downloads"):
        self.name = name
        self.port = port
        self.peer_id = uuid.uuid4().hex[:8]
        self.log = log_callback
        self.peers_changed = peers_callback or (lambda: None)
        self.downloads_dir = downloads_dir

        self.server_socket = None
        self.running = False
        self.peers = {}                   # peer_id -> Peer
        self.peers_lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle
    def start(self):
        """Create the TCP server socket and start accepting connections."""
        os.makedirs(self.downloads_dir, exist_ok=True)
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("0.0.0.0", self.port))   # bind()
        self.server_socket.listen()                        # listen()
        self.server_socket.settimeout(1.0)  # so the accept loop can notice stop()
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.log(f"[INFO] Peer '{self.name}' [{self.peer_id}] listening on port {self.port}")

    def stop(self):
        """Close the server socket and every peer connection."""
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None
        with self.peers_lock:
            peers = list(self.peers.values())
            self.peers.clear()
        for p in peers:
            self._close_socket(p.sock)
        self.peers_changed()
        self.log("[INFO] Peer stopped")

    # --------------------------------------------------------- server role
    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()   # accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # one thread per incoming connection
            threading.Thread(target=self._handle_incoming,
                             args=(conn, addr), daemon=True).start()

    def _handle_incoming(self, conn, addr):
        """Handshake for an incoming connection, then run the receive loop."""
        try:
            conn.settimeout(10)
            msg = proto.recv_message(conn)
            if msg.get("type") != proto.HELLO:
                raise ValueError("First message was not HELLO")

            remote_id = msg["peer_id"]
            if remote_id == self.peer_id:
                proto.send_message(conn, proto.make_error("Cannot connect to yourself"))
                raise ValueError("Connection from itself")
            if self._has_peer(remote_id):
                proto.send_message(conn, proto.make_error("Already connected"))
                raise ValueError("Duplicate connection")

            proto.send_message(conn, proto.make_hello_ack(self.peer_id, self.name, self.port))
            conn.settimeout(None)
            peer = Peer(remote_id, msg["peer_name"], conn, addr, int(msg["port"]))
        except Exception as e:
            self.log(f"[ERROR] Incoming connection from {addr[0]} rejected: {e}")
            self._close_socket(conn)
            return

        self._register(peer)
        self._receive_loop(peer)

    # --------------------------------------------------------- client role
    def connect(self, ip, port):
        """Connect to another peer. Raises an Exception with a readable message on failure."""
        ip = ip.strip()
        if ip.lower() != "localhost":
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise ValueError(f"Invalid IP address: '{ip}'")
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ValueError("Invalid port: must be a number")
        if not (1 <= port <= 65535):
            raise ValueError("Invalid port: must be between 1 and 65535")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((ip, port))                                   # connect()
            proto.send_message(sock, proto.make_hello(self.peer_id, self.name, self.port))
            reply = proto.recv_message(sock)
            if reply.get("type") == proto.ERROR:
                raise ConnectionError(reply.get("message", "Rejected by peer"))
            if reply.get("type") != proto.HELLO_ACK:
                raise ConnectionError("Peer did not reply with HELLO_ACK")
            if reply["peer_id"] == self.peer_id:
                raise ConnectionError("Cannot connect to yourself")
            if self._has_peer(reply["peer_id"]):
                raise ConnectionError("Already connected to this peer")
            sock.settimeout(None)
            peer = Peer(reply["peer_id"], reply["peer_name"], sock, (ip, port), int(reply["port"]))
        except Exception:
            self._close_socket(sock)
            raise

        self._register(peer)
        threading.Thread(target=self._receive_loop, args=(peer,), daemon=True).start()
        return peer

    # ------------------------------------------------------ peer bookkeeping
    def _has_peer(self, peer_id):
        with self.peers_lock:
            return peer_id in self.peers

    def _register(self, peer):
        with self.peers_lock:
            self.peers[peer.peer_id] = peer
        self.log(f"[INFO] Connected to {peer.label()} at {peer.address[0]}:{peer.listen_port}")
        self.peers_changed()

    def _remove_peer(self, peer, reason=None):
        with self.peers_lock:
            removed = self.peers.pop(peer.peer_id, None)
        self._close_socket(peer.sock)
        if removed:
            self.log(f"[INFO] {peer.label()} disconnected" + (f" ({reason})" if reason else ""))
            self.peers_changed()

    def get_peers(self):
        with self.peers_lock:
            return list(self.peers.values())

    def get_peer(self, peer_id):
        with self.peers_lock:
            return self.peers.get(peer_id)

    @staticmethod
    def _close_socket(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------ receiving
    def _receive_loop(self, peer):
        """Runs in its own thread: read messages from one peer until it disconnects."""
        reason = None
        try:
            while self.running:
                msg = proto.recv_message(peer.sock)
                mtype = msg.get("type")
                if mtype == proto.TEXT:
                    self.log(f"{peer.name} -> You: {msg.get('message', '')}")
                elif mtype == proto.FILE:
                    self._receive_file(peer, msg)
                else:
                    self.log(f"[WARN] Unknown message type from {peer.label()}: {mtype}")
        except ConnectionError:
            reason = "connection closed"
        except (OSError, ValueError, KeyError) as e:
            reason = str(e)
        finally:
            self._remove_peer(peer, reason)

    def _receive_file(self, peer, msg):
        filename = os.path.basename(str(msg.get("filename", ""))) or "received_file"
        try:
            filesize = int(msg["filesize"])
            if filesize < 0:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError("Invalid file size in metadata")

        path = self._unique_path(filename)
        self.log(f"[INFO] Receiving '{filename}' ({filesize} bytes) from {peer.name}...")
        remaining = filesize
        start = time.time()          # for transfer time / speed
        try:
            with open(path, "wb") as f:
                while remaining > 0:      # file size tells us when the file is complete
                    chunk = peer.sock.recv(min(proto.CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError("Peer disconnected during file transfer")
                    f.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            if os.path.exists(path):
                os.remove(path)           # delete the incomplete file
            raise
        elapsed = max(time.time() - start, 0.001)     # avoid divide-by-zero
        speed = filesize / 1024 / elapsed             # KB per second
        self.log(f"[FILE] File received from {peer.name}: {os.path.basename(path)} "
                 f"({elapsed:.2f}s, {speed:.0f} KB/s, saved in {self.downloads_dir}/)")

    def _unique_path(self, filename):
        path = os.path.join(self.downloads_dir, filename)
        base, ext = os.path.splitext(filename)
        i = 1
        while os.path.exists(path):
            path = os.path.join(self.downloads_dir, f"{base}_{i}{ext}")
            i += 1
        return path

    # -------------------------------------------------------------- sending
    def send_text(self, peer_id, text):
        peer = self.get_peer(peer_id)
        if peer is None:
            raise ConnectionError("Selected peer is not connected")
        try:
            with peer.send_lock:
                proto.send_message(peer.sock, proto.make_text(self.peer_id, self.name, text))
        except OSError as e:
            self._remove_peer(peer, str(e))
            raise ConnectionError(f"Could not send message: {e}")
        self.log(f"You -> {peer.name}: {text}")

    def send_text_all(self, text):
        """Send the same text to every connected peer (one direct connection each)."""
        peers = self.get_peers()
        if not peers:
            raise ConnectionError("No connected peers")
        for p in peers:
            try:
                self.send_text(p.peer_id, text)
            except ConnectionError as e:
                self.log(f"[ERROR] {p.name}: {e}")

    def disconnect(self, peer_id):
        """Close the connection to one peer on purpose."""
        peer = self.get_peer(peer_id)
        if peer is None:
            raise ConnectionError("Selected peer is not connected")
        self._remove_peer(peer, "disconnected by you")

    def send_file(self, peer_id, path):
        peer = self.get_peer(peer_id)
        if peer is None:
            raise ConnectionError("Selected peer is not connected")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File does not exist: {path}")
        filename = os.path.basename(path)
        filesize = os.path.getsize(path)
        self.log(f"[INFO] Sending '{filename}' ({filesize} bytes) to {peer.name}...")
        start = time.time()          # for transfer time / speed
        try:
            with peer.send_lock:
                # 1) metadata (JSON)
                proto.send_message(peer.sock,
                                   proto.make_file(self.peer_id, self.name, filename, filesize))
                # 2) raw bytes, in chunks (never load the whole file into memory)
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(proto.CHUNK_SIZE)
                        if not chunk:
                            break
                        peer.sock.sendall(chunk)
        except OSError as e:
            self._remove_peer(peer, str(e))
            raise ConnectionError(f"File transfer failed: {e}")
        elapsed = max(time.time() - start, 0.001)
        speed = filesize / 1024 / elapsed
        self.log(f"[FILE] File sent to {peer.name}: {filename} ({elapsed:.2f}s, {speed:.0f} KB/s)")
