"""Meshtastic link — local LoRa mesh via a USB dongle + the global internet mesh.

Two layers, both optional and fail-soft:

1. LOCAL MESH. Talks to a Meshtastic node plugged into a USB port using the
   `meshtastic` Python library (SerialInterface). Receives text from the LoRa
   mesh, lists nearby nodes, and broadcasts text out over the air.

2. GLOBAL MESH. Bridges the primary channel to the public worldwide broker
   (mqtt.meshtastic.org) using the Pi's own internet connection (paho-mqtt).
   Incoming global text is shown in the feed; outgoing text is published to
   the public JSON topic so it reaches the global mesh. The bridge lives
   entirely on the Pi — it does NOT reconfigure / reboot the user's node, so
   enabling or disabling "global" never mutates the dongle's settings.

Everything runs on one background worker thread; the UI polls snapshot().
Missing library, missing dongle, or no internet each degrade to a clear
status string instead of crashing.
"""
from __future__ import annotations

import copy
import glob
import json
import threading
import time
from dataclasses import dataclass, field

# Public global broker — the same defaults the Meshtastic firmware ships with.
PUBLIC_MQTT_HOST = "mqtt.meshtastic.org"
PUBLIC_MQTT_PORT = 1883
PUBLIC_MQTT_USER = "meshdev"
PUBLIC_MQTT_PASS = "large4cats"
MQTT_REGION = "US"            # root-topic region segment (msh/<region>/2/...)
DEFAULT_CHANNEL = "LongFast"  # default public channel name

MAX_LOG = 200

try:
    import meshtastic
    import meshtastic.serial_interface
    from pubsub import pub
    _HAS_MESHTASTIC = True
except Exception:
    _HAS_MESHTASTIC = False

try:
    import paho.mqtt.client as mqtt
    _HAS_PAHO = True
except Exception:
    _HAS_PAHO = False


def _find_ports() -> list[str]:
    """Likely Meshtastic serial ports, most-specific first."""
    ports: list[str] = []
    # by-id symlinks survive re-enumeration and name the chip clearly
    for p in sorted(glob.glob("/dev/serial/by-id/*")):
        low = p.lower()
        if any(k in low for k in ("cp210", "ch340", "ch910", "ttyusb",
                                  "ttyacm", "usb_serial", "silicon", "rak",
                                  "heltec", "lilygo", "tbeam", "seeed")):
            ports.append(p)
    for pat in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        ports.extend(sorted(glob.glob(pat)))
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in ports:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


@dataclass
class Message:
    ts: float
    source: str   # LORA | GLOBAL | ME | SYS
    sender: str
    text: str


@dataclass
class LinkState:
    phase: str = "INIT"   # INIT | NO_LIB | NO_DEVICE | CONNECTING | ONLINE | ERROR
    error: str = ""
    port: str = ""
    my_id: str = ""
    my_name: str = ""
    region: str = ""
    channel: str = DEFAULT_CHANNEL
    num_nodes: int = 0
    battery: int = -1
    global_enabled: bool = False
    mqtt_connected: bool = False
    sent: int = 0
    recv_lora: int = 0
    recv_global: int = 0
    messages: list = field(default_factory=list)


class MeshtasticLink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._st = LinkState()
        self._stop = threading.Event()
        self._iface = None
        self._mqtt = None
        self._my_num = 0
        self._want_global = False
        self._tx: list[str] = []
        self._started = False
        self._worker = threading.Thread(target=self._run, daemon=True)

    # ---- public API (called from UI thread) ----------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if not _HAS_MESHTASTIC:
            self._set(phase="NO_LIB",
                      error="meshtastic library missing — run Settings > Fix Dependencies, then update")
            self._log("SYS", "system", "meshtastic python library not installed")
            return
        self._worker.start()

    def snapshot(self) -> LinkState:
        with self._lock:
            return copy.deepcopy(self._st)

    def send(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._tx.append(text[:228])  # mesh payload cap

    def toggle_global(self) -> None:
        self._want_global = not self._want_global

    def reconnect(self) -> None:
        """Drop the serial link so the worker re-probes for a dongle."""
        iface = self._iface
        self._iface = None
        if iface is not None:
            try:
                iface.close()
            except Exception:
                pass
        self._set(phase="CONNECTING", error="", port="")

    def stop(self) -> None:
        self._stop.set()
        self._want_global = False
        self._teardown_mqtt()
        iface = self._iface
        self._iface = None
        if iface is not None:
            try:
                pub.unsubscribe(self._on_receive, "meshtastic.receive.text")
                pub.unsubscribe(self._on_connection, "meshtastic.connection.established")
            except Exception:
                pass
            try:
                iface.close()
            except Exception:
                pass

    # ---- state helpers --------------------------------------------------

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self._st, k, v)

    def _log(self, source: str, sender: str, text: str) -> None:
        with self._lock:
            self._st.messages.append(Message(time.time(), source, sender, text))
            if len(self._st.messages) > MAX_LOG:
                del self._st.messages[: len(self._st.messages) - MAX_LOG]

    # ---- worker ---------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._iface is None:
                self._connect_serial()
                if self._iface is None:
                    # back off before re-probing for a dongle
                    self._stop.wait(3.0)
                    continue
            try:
                self._service_global()
                self._drain_tx()
                self._refresh_node()
            except Exception as e:
                self._set(phase="ERROR", error=f"{type(e).__name__}: {e}")
                self.reconnect()
            self._stop.wait(1.0)

    def _connect_serial(self) -> None:
        ports = _find_ports()
        if not ports:
            self._set(phase="NO_DEVICE",
                      error="No Meshtastic dongle found on /dev/ttyUSB* or /dev/ttyACM*")
            return
        self._set(phase="CONNECTING", error="", port=ports[0])
        try:
            iface = meshtastic.serial_interface.SerialInterface(devPath=ports[0])
        except Exception as e:
            self._set(phase="ERROR", port=ports[0], error=f"open failed: {e}")
            return
        self._iface = iface
        try:
            pub.subscribe(self._on_receive, "meshtastic.receive.text")
            pub.subscribe(self._on_connection, "meshtastic.connection.established")
        except Exception:
            pass
        self._read_identity()
        self._set(phase="ONLINE", error="")
        self._log("SYS", "system", f"local mesh online via {ports[0]}")

    def _read_identity(self) -> None:
        iface = self._iface
        if iface is None:
            return
        try:
            self._my_num = int(getattr(iface.myInfo, "my_node_num", 0) or 0)
        except Exception:
            self._my_num = 0
        try:
            mi = iface.getMyNodeInfo() or {}
            user = mi.get("user", {}) or {}
            self._set(
                my_id=user.get("id", "") or f"!{self._my_num:08x}" if self._my_num else "",
                my_name=user.get("shortName") or user.get("longName") or user.get("id", "node"),
                battery=int(mi.get("deviceMetrics", {}).get("batteryLevel", -1)),
            )
        except Exception:
            pass
        try:
            region = iface.localNode.localConfig.lora.region
            self._set(region=str(region))
        except Exception:
            pass

    def _refresh_node(self) -> None:
        iface = self._iface
        if iface is None:
            return
        try:
            self._set(num_nodes=len(iface.nodes or {}))
        except Exception:
            pass

    def _drain_tx(self) -> None:
        iface = self._iface
        if iface is None:
            return
        with self._lock:
            pending, self._tx = self._tx, []
        for text in pending:
            sent_lora = False
            try:
                iface.sendText(text)
                sent_lora = True
            except Exception as e:
                self._log("SYS", "system", f"LoRa send failed: {e}")
            if self._st.global_enabled:
                self._mqtt_publish(text)
            if sent_lora or self._st.global_enabled:
                with self._lock:
                    self._st.sent += 1
                self._log("ME", self._st.my_name or "me", text)

    # ---- meshtastic pubsub callbacks ------------------------------------

    def _on_receive(self, packet=None, interface=None) -> None:
        try:
            dec = (packet or {}).get("decoded", {}) or {}
            text = dec.get("text")
            if not text:
                return
            sender = (packet or {}).get("fromId") or str((packet or {}).get("from", "?"))
            self._log("LORA", sender, text)
            with self._lock:
                self._st.recv_lora += 1
        except Exception:
            pass

    def _on_connection(self, interface=None, topic=None) -> None:
        self._read_identity()
        self._set(phase="ONLINE", error="")

    # ---- global MQTT bridge ---------------------------------------------

    def _service_global(self) -> None:
        """Reconcile the global bridge with what the user asked for."""
        if self._want_global and self._mqtt is None:
            self._start_mqtt()
        elif not self._want_global and self._mqtt is not None:
            self._teardown_mqtt()

    def _start_mqtt(self) -> None:
        if not _HAS_PAHO:
            self._log("SYS", "system", "paho-mqtt missing — global bridge unavailable")
            self._want_global = False
            return
        try:
            client = mqtt.Client()
            client.username_pw_set(PUBLIC_MQTT_USER, PUBLIC_MQTT_PASS)
            client.on_connect = self._on_mqtt_connect
            client.on_disconnect = self._on_mqtt_disconnect
            client.on_message = self._on_mqtt_message
            client.connect_async(PUBLIC_MQTT_HOST, PUBLIC_MQTT_PORT, keepalive=60)
            client.loop_start()
            self._mqtt = client
            self._set(global_enabled=True)
            self._log("SYS", "system", f"connecting to global broker {PUBLIC_MQTT_HOST}")
        except Exception as e:
            self._log("SYS", "system", f"global connect failed: {e}")
            self._want_global = False

    def _teardown_mqtt(self) -> None:
        client = self._mqtt
        self._mqtt = None
        self._set(global_enabled=False, mqtt_connected=False)
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
            self._log("SYS", "system", "global bridge disconnected")

    def _sub_topic(self) -> str:
        return f"msh/{MQTT_REGION}/2/json/{DEFAULT_CHANNEL}/#"

    def _pub_topic(self) -> str:
        gw = self._st.my_id or f"!{self._my_num:08x}"
        return f"msh/{MQTT_REGION}/2/json/mqtt/{gw}"

    def _mqtt_publish(self, text: str) -> None:
        client = self._mqtt
        if client is None:
            return
        payload = {
            "from": self._my_num,
            "type": "sendtext",
            "channel": 0,
            "payload": text,
        }
        try:
            client.publish(self._pub_topic(), json.dumps(payload), qos=0)
        except Exception as e:
            self._log("SYS", "system", f"global publish failed: {e}")

    def _on_mqtt_connect(self, client, userdata, flags, rc, *args) -> None:
        if rc == 0:
            self._set(mqtt_connected=True)
            try:
                client.subscribe(self._sub_topic())
            except Exception:
                pass
            self._log("SYS", "system", f"global mesh online ({DEFAULT_CHANNEL})")
        else:
            self._set(mqtt_connected=False)
            self._log("SYS", "system", f"global broker refused (rc={rc})")

    def _on_mqtt_disconnect(self, client, userdata, rc, *args) -> None:
        self._set(mqtt_connected=False)

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8", "ignore"))
        except Exception:
            return
        if data.get("type") not in ("text", "sendtext"):
            return
        if str(data.get("from", "")) == str(self._my_num):
            return  # don't echo our own uplinks back into the feed
        payload = data.get("payload")
        if isinstance(payload, dict):
            text = payload.get("text", "")
        else:
            text = str(payload or "")
        if not text:
            return
        sender = data.get("sender") or str(data.get("from", "?"))
        self._log("GLOBAL", sender, text)
        with self._lock:
            self._st.recv_global += 1
