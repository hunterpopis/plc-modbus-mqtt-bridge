# PLC Modbus-TCP to MQTT Bridge

A small, dependable bridge that lets a single-connection PLC be read and
controlled by any number of clients, by translating Modbus TCP to MQTT.

> **Built and tested on** an **AutomationDirect P1AM-100** (ProductivityOpen /
> P1000) running **OpenPLC**, exposing **Modbus TCP**. The bridge pattern works
> with any single-connection Modbus TCP PLC — just adjust the register map to
> match your firmware.

## Why this was built

Many PLCs accept only **one** Modbus TCP client connection at a time. That makes
it impossible to run more than one tool against the PLC at once — a dashboard, a
data logger, and a phone app would all fight over that single connection, and
whichever one connects last wins.

This bridge fixes that. It is the **only** program that ever connects to the PLC.
It holds the one Modbus connection, reads the PLC's state, and re-publishes it
over **MQTT**, which supports unlimited subscribers. Commands flow back the same
way. So any number of consumers can see the PLC's data and send it commands,
while only the bridge ever speaks Modbus.

It is designed to run on a **Raspberry Pi or a PC** that acts as the supervisory
controller for individual points, separate from the PLC.

## What it does

- Holds one persistent Modbus TCP connection to the PLC.
- **PULL:** reads a heartbeat, the discrete input states, and the discrete output
  states from the PLC.
- **PUBLISH:** broadcasts those onto MQTT topics for any subscriber.
- **SUBSCRIBE / PUSH:** accepts an output command over MQTT and writes it back to
  the PLC.
- Writes its own heartbeat to the PLC, so the PLC can tell the bridge is alive
  (and fail safe on its own if the bridge goes silent).
- Self-heals: rebuilds the connection after repeated comms failures.
- Publishes an MQTT "last will" so subscribers know immediately if the bridge dies.

### MQTT topics

Published:
- `plc/heartbeat` — the PLC's heartbeat counter
- `bridge/heartbeat` — the bridge's heartbeat counter
- `plc/inputs` — discrete input states as a string of 1s and 0s
- `plc/outputs` — discrete output states as a string of 1s and 0s
- `bridge/status` — `online` / `offline` (retained, last-will)

Subscribed:
- `plc/cmd/outputs` — an integer whose bits set the PLC's outputs

## Hardware this was built and tested on

The bridge was built against a custom controller on the AutomationDirect
**ProductivityOpen / P1000** platform, so the same setup can be sourced and
reproduced:

- **CPU:** AutomationDirect **P1AM-100** (Arduino-compatible, MKR-format CPU)
  with an Ethernet shield (e.g. **P1AM-ETH**), running custom firmware built in
  **OpenPLC** that exposes a **Modbus TCP server on port 502**. The real-time
  control logic runs
  *on* the PLC; the firmware reflects I/O state into, and reads commands from, a
  set of Modbus holding registers.
- **Discrete outputs (60):** 2 × **P1-15TD1** + 2 × **P1-15TD2** (15-point DC
  output modules)
- **Discrete inputs (32):** 2 × **P1-16NE3** (16-point DC input modules)
- **Analog / temperature (8):** 2 × **P1-04THM** (4-channel thermocouple input
  modules)

The PLC firmware packs the discrete I/O into Modbus holding registers (one bit
per point) and maintains a heartbeat and command registers — which is exactly
what this bridge reads and writes.

### How the PLC firmware was built (OpenPLC code injection)

The firmware was built in **OpenPLC**, but rather than writing the control logic
in OpenPLC's standard programming portal (the ladder / structured-text editor),
it was written as a **native C/C++ function block (POU) and injected directly**.
This makes it possible to run full native code on the SAMD21 instead of being
limited to the graphical editor.

One gotcha worth knowing: OpenPLC will **not** run an injected POU on its own —
it has to be invoked from the **main program**. So the main program holds a small
structured-text stub that declares the block, brings it alive, and calls it on
every scan:

```
VAR
    alive   : BOOL;
    runthis : test;   (* the injected C/C++ function block, named "test" *)
END_VAR

alive   := TRUE;
runthis();
```

Without that stub in the main program, the injected code never executes.

## Configuration

Everything is at the top of `plc_bridge.py`:

- `PLC_IP` / `PLC_PORT` — your PLC's address (standard Modbus TCP port is 502).
- `MQTT_HOST` / `MQTT_PORT` — your MQTT broker.
- The **register map** (`HB_PLC`, `HB_BRIDGE`, `INPUTS`, `OUTPUTS`,
  `CMD_OUTPUTS`, `READ_COUNT`) — **change these to match the registers your own
  PLC firmware exposes.** The defaults are illustrative.

## ⚠️ Scope and safety — read before connecting anything

This bridge is intentionally **slow and supervisory**. It polls the PLC roughly
**once per second** over TCP and relays everything through an MQTT broker. That
path has latency, and a message can occasionally be delayed, dropped, or misread.

**That is perfectly fine for slow, non-critical control and monitoring** —
lighting, HVAC setpoints, status displays, dashboards, logging. If a light turns
on a second late, or a reading is briefly wrong, nothing is harmed.

**Do NOT use this bridge to control anything fast-moving, motion-related, or
safety-critical.** No motors, actuators, machinery, presses, conveyors,
interlocks, or emergency stops. Anywhere a delayed, dropped, or duplicated
command — or a single misread — could cause damage, injury, or two commands
conflicting with each other, this bridge does not belong.

The rule of thumb:

> All time-critical, motion, interlock, and safety logic must run
> **deterministically on the PLC itself** (or a dedicated real-time / safety
> controller). This bridge is only for non-critical supervisory monitoring and
> control from a Raspberry Pi or PC.

Put plainly: a misread that flips a light is harmless. The same misread on a
moving machine is not. Keep this bridge on the harmless side of that line.

## Requirements

- Python 3
- `pymodbus`
- `paho-mqtt`
- An MQTT broker (e.g. Mosquitto)

```
pip install pymodbus paho-mqtt
python plc_bridge.py
```
