"""Simple Telnet BBS server for local network chat."""
from __future__ import annotations

import socket
import threading
from datetime import datetime

class BBSServer:
    def __init__(self, port: int = 2323) -> None:
        self.port = port
        self.clients = []
        self.history = []
        self._lock = threading.Lock()
        self._server = None
        self._stop = threading.Event()

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server.bind(("0.0.0.0", self.port))
            self._server.listen(5)
            threading.Thread(target=self._accept_loop, daemon=True).start()
            return True, f"BBS started on port {self.port}"
        except Exception as e:
            return False, str(e)

    def stop(self):
        self._stop.set()
        if self._server:
            try:
                # Force close by connecting to itself or just shutdown
                socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("127.0.0.1", self.port))
            except: pass
            self._server.close()

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
                if self._stop.is_set(): break
                threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()
            except: break

    def _handle_client(self, conn, addr):
        conn.send(b"\r\n*** WELCOME TO BIGBOX LOCAL BBS ***\r\n")
        conn.send(b"Enter handle: ")
        handle = conn.recv(1024).decode().strip() or f"user_{addr[1]}"
        
        with self._lock:
            self.clients.append(conn)
            # Send history
            for h in self.history[-20:]:
                conn.send(f"{h}\r\n".encode())

        welcome = f"\r\n*** {handle} HAS JOINED ***\r\n"
        self._broadcast(welcome)

        try:
            while not self._stop.is_set():
                conn.send(f"<{handle}> ".encode())
                data = conn.recv(1024).decode().strip()
                if not data: break
                
                msg = f"[{datetime.now().strftime('%H:%M')}] <{handle}> {data}"
                with self._lock:
                    self.history.append(msg)
                self._broadcast(f"\r{msg}\r\n")
        except: pass
        finally:
            with self._lock:
                if conn in self.clients: self.clients.remove(conn)
            self._broadcast(f"\r\n*** {handle} HAS LEFT ***\r\n")
            conn.close()

    def _broadcast(self, msg: str):
        with self._lock:
            for c in self.clients:
                try: c.send(msg.encode())
                except: pass
