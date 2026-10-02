"""
protocol.py - Application-level protocol for the P2P network.

Every message on the wire looks like this:

    [4-byte length][JSON payload]

* The 4-byte length is an unsigned big-endian integer ("!I") that tells the
  receiver how many bytes of JSON follow.  This is called MESSAGE FRAMING.
  TCP is a byte stream, so without it we could not tell where one message
  ends and the next one begins.

* For file transfers, a JSON "file" message (metadata) is sent first, and
  then exactly `filesize` raw bytes follow immediately after it.
"""

import json
import struct

HEADER_SIZE = 4                 # 4 bytes for the length prefix
MAX_JSON_SIZE = 1024 * 1024     # sanity limit for a JSON message (1 MB)
CHUNK_SIZE = 64 * 1024          # files are sent/received in 64 KB chunks

# Message types
HELLO = "hello"
HELLO_ACK = "hello_ack"
TEXT = "text"
FILE = "file"
ERROR = "error"


# ---------------------------------------------------------------- building
def make_hello(peer_id, name, port):
    return {"type": HELLO, "peer_id": peer_id, "peer_name": name, "port": port}


def make_hello_ack(peer_id, name, port):
    return {"type": HELLO_ACK, "peer_id": peer_id, "peer_name": name, "port": port}


def make_text(peer_id, name, message):
    return {"type": TEXT, "sender_id": peer_id, "sender_name": name, "message": message}


def make_file(peer_id, name, filename, filesize):
    return {"type": FILE, "sender_id": peer_id, "sender_name": name,
            "filename": filename, "filesize": filesize}


def make_error(message):
    return {"type": ERROR, "message": message}


# ---------------------------------------------------------- encode / decode
def encode_message(obj):
    """dict -> [4-byte length][JSON bytes]"""
    payload = json.dumps(obj).encode("utf-8")
    return struct.pack("!I", len(payload)) + payload


def send_message(sock, obj):
    sock.sendall(encode_message(obj))


def recv_exact(sock, n):
    """
    Read EXACTLY n bytes from the socket.
    One recv() call may return fewer bytes than requested, so we loop
    until we have everything (or the peer disconnects).
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), CHUNK_SIZE))
        if not chunk:
            raise ConnectionError("Peer disconnected")
        buf.extend(chunk)
    return bytes(buf)


def recv_message(sock):
    """Read one framed JSON message and return it as a dict."""
    header = recv_exact(sock, HEADER_SIZE)
    (length,) = struct.unpack("!I", header)
    if length == 0 or length > MAX_JSON_SIZE:
        raise ValueError(f"Invalid message length: {length}")
    payload = recv_exact(sock, length)
    msg = json.loads(payload.decode("utf-8"))
    if not isinstance(msg, dict) or "type" not in msg:
        raise ValueError("Malformed message")
    return msg
