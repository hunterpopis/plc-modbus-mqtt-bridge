#!/usr/bin/env python3
"""
plc_bridge.py - A Modbus TCP <-> MQTT bridge for a single-connection PLC.

Many PLCs accept only ONE Modbus TCP client connection at a time. That makes it
impossible to run several tools against the PLC at once - a dashboard, a logger,
and a phone app would all fight over that single connection, and whoever connects
last wins.

This bridge solves it. It is the ONLY program that ever connects to the PLC. It:
  - holds the one persistent Modbus connection,
  - PULLs the PLC's state (a heartbeat, the discrete inputs, the discrete outputs)
    and PUBLISHes it to MQTT, where any number of clients can subscribe, and
  - SUBSCRIBEs to a command topic and PUSHes those commands back to the PLC.

So the PLC becomes reachable by unlimited consumers, while only the bridge ever
speaks Modbus. It is meant to run on a Raspberry Pi or a PC alongside the PLC.

SCOPE / SAFETY: this bridge is intentionally slow and supervisory. It is for
non-critical monitoring and control (lighting, setpoints, status) ONLY - never
for fast or motion/safety-critical control. See the README before connecting
anything.

Requires: pymodbus, paho-mqtt
"""

from pymodbus.client import ModbusTcpClient
import paho.mqtt.client as mqtt
import time, threading

# ---------- CONFIGURATION (adapt to your own PLC and broker) ----------
PLC_IP    = "192.168.1.50"    # your PLC's IP address
PLC_PORT  = 502               # standard Modbus TCP port
MQTT_HOST = "localhost"       # your MQTT broker (localhost if on the same machine)
MQTT_PORT = 1883
POLL_SECONDS = 1.0            # how often to read/write - this bridge is deliberately slow

# Holding-register map. These addresses must match what YOUR PLC firmware exposes.
# This example assumes the firmware:
#   - keeps its own heartbeat in register HB_PLC
#   - reflects packed discrete INPUT states in the registers listed in INPUTS
#   - reflects packed discrete OUTPUT states in the registers listed in OUTPUTS
#   - watches register HB_BRIDGE for the bridge's heartbeat (so the PLC can tell
#     the bridge is alive, and fail safe on its own if the bridge goes silent)
#   - accepts an output-command word in register CMD_OUTPUTS
HB_PLC      = 0        # read:  PLC's heartbeat
HB_BRIDGE   = 20       # write: bridge's heartbeat
INPUTS      = [6, 7]   # read:  two 16-bit words of discrete input states
OUTPUTS     = [1, 2]   # read:  two 16-bit words of discrete output states
CMD_OUTPUTS = 21       # write: requested output word (each bit maps to an output)
READ_COUNT  = 8        # how many registers to read in one request (0 .. READ_COUNT-1)

# ---------- shared command state (set by MQTT, written to the PLC each loop) ----
cmd = {"outputs": None}        # None = no output override this cycle
cmd_lock = threading.Lock()


def bits16(word):
    """Turn a 16-bit register value into a '0101...' string, bit 0 first."""
    return "".join("1" if (word >> i) & 1 else "0" for i in range(16))


# ---------- MQTT setup (the SUBSCRIBE / commands-in side) ----------
def on_connect(client, userdata, flags, rc, props=None):
    print(f"MQTT connected rc={rc}")
    client.subscribe("plc/cmd/outputs")                    # integer payload: the output word
    client.publish("bridge/status", "online", retain=True)

def on_message(client, userdata, msg):
    """A command arrived. Stash it; the main loop writes it to the PLC."""
    payload = msg.payload.decode().strip()
    with cmd_lock:
        if msg.topic == "plc/cmd/outputs":
            try:
                cmd["outputs"] = int(payload)              # bits of this integer map to outputs
            except ValueError:
                print(f"  ignored non-integer outputs command: {payload!r}")

mc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="plc_bridge")
mc.will_set("bridge/status", "offline", retain=True)       # last-will if the bridge dies
mc.on_connect = on_connect
mc.on_message = on_message
mc.connect(MQTT_HOST, MQTT_PORT, 60)
mc.loop_start()

def pub(topic, value, retain=True):
    try: mc.publish(topic, str(value), retain=retain)
    except Exception: pass


# ---------- main loop (where PUSH and PULL happen) ----------
def main():
    client = ModbusTcpClient(PLC_IP, port=PLC_PORT); client.connect()
    bridge_hb = 0
    err = 0
    print(f"PLC bridge: Modbus {PLC_IP}:{PLC_PORT} <-> MQTT {MQTT_HOST}:{MQTT_PORT}")

    while True:
        bridge_hb = (bridge_hb + 1) % 30000        # rolling heartbeat counter
        with cmd_lock:
            out_cmd = cmd["outputs"]
            cmd["outputs"] = None                  # consume the pending command
        try:
            if not client.connected: client.connect()

            # --- PUSH: write the bridge heartbeat (and any output command) to the PLC ---
            client.write_register(HB_BRIDGE, bridge_hb)
            if out_cmd is not None:
                client.write_register(CMD_OUTPUTS, out_cmd)

            # --- PULL: read the PLC's state in one request ---
            rr = client.read_holding_registers(address=0, count=READ_COUNT)
            if not rr.isError():
                err = 0
                g = rr.registers
                def reg(i): return g[i] if i < len(g) else 0

                # --- PUBLISH: heartbeat, inputs, and outputs onto MQTT ---
                pub("plc/heartbeat",    reg(HB_PLC), retain=False)
                pub("bridge/heartbeat", bridge_hb,   retain=False)
                pub("plc/inputs",  "".join(bits16(reg(a)) for a in INPUTS))
                pub("plc/outputs", "".join(bits16(reg(a)) for a in OUTPUTS))
            else:
                raise IOError("Modbus read returned an error")

        except Exception as e:
            # self-healing: after several consecutive failures, rebuild the connection
            err += 1
            print(f"  PLC comms error ({err}): {e}")
            if err >= 5:
                try: client.close()
                except Exception: pass
                client = ModbusTcpClient(PLC_IP, port=PLC_PORT)
                try: client.connect()
                except Exception: pass
                err = 0

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pub("bridge/status", "offline")
        print("\nbridge stopped")
