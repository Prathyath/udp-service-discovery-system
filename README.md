# UDP Service Discovery System with SDN

Computer Networks (UE25CS243A) Jackfruit Mini Project, Project 21.

A UDP-based service discovery system running in Mininet and controlled by a
Ryu OpenFlow 1.3 controller. Servers advertise services, a registry tracks
them, and clients discover them. The controller prioritises discovery traffic,
blocks unauthorised hosts, and reacts to server failures reported by the
registry.

Team: Prathyath / PES1UG25CS673 |
      M.VAISHNAVI / PES1UG25CS662 |
      NIHAL J GOWDA / PES1UG25CS668 |

## Features
| Requirement | Implementation |
|---|---|
| UDP service advertisements | `src/server.py` sends `ADVERTISE` every 2 s |
| Service registry | `src/registry.py`, thread-safe table with timestamps |
| Discover available services | `src/client.py` sends `DISCOVER`, registry replies `RESPONSE` |
| Handle service timeouts | Registry TTL of 6 s; client timeout 1 s with 3 retries |
| Detect unavailable servers | Missed advertisements -> `TIMEOUT`, server removed from replies |
| Use SDN to control service traffic | `controller/sdn_ctrl.py`: priority flows, allow-list, blocking, monitoring |
| Dynamic service registration | Servers join and leave at runtime; no restarts or static lists |
| Measure discovery latency | Client measures round trip; optional CSV output (`--csv`) |

SDN features:
- Priority-100 flows for discovery traffic (UDP 5000) from allowed hosts only
- Unauthorised hosts are detected and blocked (priority 90 -> 95 drop rule)
- The registry sends server up/down events to the controller; on `SERVER_DOWN`
  the controller drops traffic to that server (priority 96), and removes the
  rule on `SERVER_UP`
- Flow statistics are polled every 5 s and logged

See `docs/architecture.md` for the architecture diagram, message formats and
flow-table design.

## Repository layout
```
controller/sdn_ctrl.py   Ryu controller
topology/topo.py         Mininet topology (registry, 2 servers, client, rogue host)
src/registry.py          UDP service registry
src/server.py            UDP service provider
src/client.py            UDP discovery client + latency measurement
docs/architecture.md     Architecture, protocol, flow table
docs/screenshots/        Test evidence
results/                 Logs and measurements (logs are git-ignored)
```

## Setup (Ubuntu 20.04 / 22.04 VM)
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install mininet openvswitch-switch git python3-pip -y
```

Ryu 4.34 does not build on new Python versions, so use Python 3.9 in a venv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
git clone https://github.com/Prathyath/udp-service-discovery-system.git
cd udp-service-discovery-system
uv venv --python 3.9 venv
source venv/bin/activate
uv pip install "setuptools==58.0.4" wheel "eventlet==0.30.2"
uv pip install --no-build-isolation "ryu==4.34"
ryu-manager --version        # ryu-manager 4.34
```
The registry, server and client use only the Python standard library.

## How to run
Always run from the repository root. Start the controller first.

**Terminal 1: controller**
```bash
source venv/bin/activate
ryu-manager controller/sdn_ctrl.py
```

**Terminal 2: network**
```bash
sudo mn -c
sudo python3 topology/topo.py
```

Roles: `h1` registry (10.0.0.1), `h2`/`h3` servers, `h4` client, `h5` rogue
host, and the VM itself at 10.0.0.254 receives registry events (UDP 6000).

At the `mininet>` prompt:
```
pingall
h1 python3 src/registry.py > results/registry.log 2>&1 &
h2 python3 src/server.py --name web --port 8001 > results/s1.log 2>&1 &
h3 python3 src/server.py --name web --port 8002 > results/s2.log 2>&1 &
h4 python3 src/client.py --service web --count 5 --use
```

Inspect the switch from a third terminal:
```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
```

Useful options: `client.py --csv results/latency.csv --label normal`,
`registry.py --no-controller` (disable event notifications), `--ttl`.

Cleanup: type `exit`, then `sudo mn -c`, and Ctrl+C the controller.

## Implementation

### Application layer (UDP sockets)
- **Server** (`src/server.py`): a loop sends `ADVERTISE|service|ip|port` to the
  registry every 2 s. A separate thread listens on the service port and answers
  requests. On Ctrl+C or SIGTERM it sends `DEREGISTER`.
- **Registry** (`src/registry.py`): the main thread receives datagrams and updates
  the table `{service: {(ip, port): last_seen}}`. A background purge thread runs
  every 1 s. A lock protects the table, because both threads use it.
- **Client** (`src/client.py`): sends `DISCOVER`, waits 1 s for a reply, retries up
  to 3 times, and measures the round trip with `time.perf_counter()`.

### Server crash handling
A crashed server cannot announce its own failure, so the registry detects it
from missing advertisements (soft state).

1. The server is killed (`kill -9`), so no `DEREGISTER` is sent and its
   advertisements stop.
2. The purge thread finds that `now - last_seen > 6 s` (TTL = 3 missed
   advertisements), deletes the entry, and logs `TIMEOUT`.
3. The registry sends `EVENT|SERVER_DOWN|service|ip|port|timeout` to the
   controller (UDP 6000).
4. The controller accepts the event only if it comes from the registry's IP, then
   installs a **priority-96 drop rule** (`ipv4_dst=ip, udp_dst=port`) on every
   connected switch. Traffic to the dead server is now dropped in the switch.
5. From then on the registry no longer returns that server in `RESPONSE`
   messages, so new clients never see it.

Detection time is 6 to 7 s after the last advertisement: the 6 s TTL plus up to
1 s until the next purge check. The controller reacts within milliseconds of
the event.

A graceful exit follows the same path, except the server sends `DEREGISTER`.
The registry removes it immediately and sends
`SERVER_DOWN|...|deregister`, so there is no 6 s delay.

### Server recovery
Recovery needs no manual step and no restart of the other components.

1. The restarted server resumes sending `ADVERTISE`.
2. The registry sees an endpoint that is not in its table, logs `REGISTER`, and
   sends `EVENT|SERVER_UP|service|ip|port|new` to the controller.
3. The controller removes the priority-96 drop rule with a strict OpenFlow
   delete, and clients discover the server again on their next query.
4. If a switch reconnects to the controller, the controller re-installs the drop
   rules for servers still marked as down, so the network state stays
   consistent.

### SDN layer (`controller/sdn_ctrl.py`)
- Learning switch for normal traffic (priority 1).
- Discovery traffic (UDP 5000) allowed from the allow-listed hosts (priority 100).
- Unknown hosts sending to UDP 5000 match a priority-90 rule that sends the
  packet to the controller. The controller logs `BLOCKED` and installs a
  priority-95 drop rule for that host.
- A UDP event listener (port 6000) applies the registry events above.
- Flow statistics are polled every 5 s and logged (discovery packets, rogue
  drops, dead-server drops).

### Flow table summary
| Priority | Match | Action | Installed |
|---|---|---|---|
| 100 | UDP dst 5000 from allowed host; UDP src 5000 | normal forwarding | at switch connect |
| 96 | UDP to a server reported down | drop | on `SERVER_DOWN`, removed on `SERVER_UP` |
| 95 | UDP dst 5000 from a blocked host | drop | when a rogue host is detected |
| 90 | UDP dst 5000 from anyone else | send to controller | at switch connect |
| 1 | learned MAC pair | output port | by the learning switch |
| 0 | any | send to controller | at switch connect |

## Implementation

### Application layer (UDP sockets)
- **Server** (`src/server.py`): a loop sends `ADVERTISE|service|ip|port` to the
  registry every 2 s. A separate thread listens on the service port and answers
  requests. On Ctrl+C or SIGTERM it sends `DEREGISTER`.
- **Registry** (`src/registry.py`): the main thread receives datagrams and updates
  the table `{service: {(ip, port): last_seen}}`. A background purge thread runs
  every 1 s. A lock protects the table, because both threads use it.
- **Client** (`src/client.py`): sends `DISCOVER`, waits 1 s for a reply, retries up
  to 3 times, and measures the round trip with `time.perf_counter()`.

### Server crash handling
A crashed server cannot announce its own failure, so the registry detects it
from missing advertisements (soft state).

1. The server is killed (`kill -9`), so no `DEREGISTER` is sent and its
   advertisements stop.
2. The purge thread finds that `now - last_seen > 6 s` (TTL = 3 missed
   advertisements), deletes the entry, and logs `TIMEOUT`.
3. The registry sends `EVENT|SERVER_DOWN|service|ip|port|timeout` to the
   controller (UDP 6000).
4. The controller accepts the event only if it comes from the registry's IP, then
   installs a **priority-96 drop rule** (`ipv4_dst=ip, udp_dst=port`) on every
   connected switch. Traffic to the dead server is now dropped in the switch.
5. From then on the registry no longer returns that server in `RESPONSE`
   messages, so new clients never see it.

Detection time is 6 to 7 s after the last advertisement: the 6 s TTL plus up to
1 s until the next purge check. The controller reacts within milliseconds of
the event.

A graceful exit follows the same path, except the server sends `DEREGISTER`.
The registry removes it immediately and sends
`SERVER_DOWN|...|deregister`, so there is no 6 s delay.

### Server recovery
Recovery needs no manual step and no restart of the other components.

1. The restarted server resumes sending `ADVERTISE`.
2. The registry sees an endpoint that is not in its table, logs `REGISTER`, and
   sends `EVENT|SERVER_UP|service|ip|port|new` to the controller.
3. The controller removes the priority-96 drop rule with a strict OpenFlow
   delete, and clients discover the server again on their next query.
4. If a switch reconnects to the controller, the controller re-installs the drop
   rules for servers still marked as down, so the network state stays
   consistent.

### SDN layer (`controller/sdn_ctrl.py`)
- Learning switch for normal traffic (priority 1).
- Discovery traffic (UDP 5000) allowed from the allow-listed hosts (priority 100).
- Unknown hosts sending to UDP 5000 match a priority-90 rule that sends the
  packet to the controller. The controller logs `BLOCKED` and installs a
  priority-95 drop rule for that host.
- A UDP event listener (port 6000) applies the registry events above.
- Flow statistics are polled every 5 s and logged (discovery packets, rogue
  drops, dead-server drops).

### Flow table summary
| Priority | Match | Action | Installed |
|---|---|---|---|
| 100 | UDP dst 5000 from allowed host; UDP src 5000 | normal forwarding | at switch connect |
| 96 | UDP to a server reported down | drop | on `SERVER_DOWN`, removed on `SERVER_UP` |
| 95 | UDP dst 5000 from a blocked host | drop | when a rogue host is detected |
| 90 | UDP dst 5000 from anyone else | send to controller | at switch connect |
| 1 | learned MAC pair | output port | by the learning switch |
| 0 | any | send to controller | at switch connect |

## Test cases
| # | Test | Steps | Expected result | Evidence |
|---|---|---|---|---|
| T1 | Connectivity | `pingall` | 0% dropped | [01](docs/screenshots/01_mininet_pingall.png) |
| T2 | Controller starts | Start Ryu, then Mininet | Event listener on UDP 6000; discovery flows installed | [02](docs/screenshots/02_controller_startup.png) |
| T3 | Discovery and latency | Start registry and 2 servers; run client | Client lists both servers, reports latency, service replies | [03](docs/screenshots/03_discovery_client.png), [04](docs/screenshots/04_registry_log.png) |
| T4 | Discovery flows | `dump-flows s1 \| grep priority=100` | Allowed-host and registry-reply flows with packet counts | [05](docs/screenshots/05_flows_priority100.png) |
| T5 | Dynamic registration | Start servers | Controller logs `EVENT server UP` for each | [06](docs/screenshots/06_server_up_events.png) |
| T6 | Server crash | `h3 pkill -9 -f "port 8002"` | After about 6 s: registry `TIMEOUT`, controller `EVENT server DOWN`, priority-96 drop rule installed | [07](docs/screenshots/07_server_down_event.png), [08](docs/screenshots/08_flow_priority96.png) |
| T7 | Traffic to dead server | Send 20 UDP packets to 10.0.0.3:8002 | Switch drops them; `dead-server-drops=20` | [09](docs/screenshots/09_dead_server_drops.png) |
| T8 | Server recovery | Restart the server | `EVENT server UP`; drop rule removed | [10](docs/screenshots/10_server_recovery.png) |
| T9 | Rogue host | `h5` starts a server and advertises | Controller logs `BLOCKED unauthorized host 10.0.0.5`; priority-95 drop rule; client never sees the rogue | [11](docs/screenshots/11_rogue_blocked.png), [12](docs/screenshots/12_flow_priority95.png), [13](docs/screenshots/13_client_ignores_rogue.png) |

## Known limitations
- Registry-to-controller events are best-effort UDP; a lost event is not retried.
- The controller accepts events only from the registry's IP, which could be spoofed.
- The rogue-host rule blocks all discovery traffic from that host, since
  `ADVERTISE` and `DISCOVER` share UDP port 5000.
- The current topology has a single switch; no link-failure rerouting yet.

## Status
- D1: sockets, Mininet topology, controller, socket-SDN integration, initial tests: done
- D2: link-failure rerouting, performance evaluation against a baseline, final report: in progress
