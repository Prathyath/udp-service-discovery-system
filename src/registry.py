#!/usr/bin/env python3
"""UDP service registry.

Receives ADVERTISE / DEREGISTER / DISCOVER datagrams and keeps a table of
live services. Entries not refreshed within --ttl seconds are purged.
Server up/down events are reported to the SDN controller over UDP.

Protocol (pipe-separated text):
  ADVERTISE|service|ip|port     server -> registry
  DEREGISTER|service|ip|port    server -> registry
  DISCOVER|service              client -> registry
  RESPONSE|service|ip:port,...  registry -> client (empty list if none)
Events (registry -> controller):
  EVENT|SERVER_UP|service|ip|port|new
  EVENT|SERVER_DOWN|service|ip|port|timeout   (or |deregister)
"""
import argparse
import socket
import threading
import time

registry = {}            # {service: {(ip, port): last_seen}}
lock = threading.Lock()
event_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
controller_addr = None   # (ip, port) or None when notifications are disabled


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def notify(kind, service, ip, port, reason):
    """Tell the SDN controller about a server event (best effort, UDP)."""
    if controller_addr is None:
        return
    msg = f"EVENT|{kind}|{service}|{ip}|{port}|{reason}"
    try:
        event_sock.sendto(msg.encode(), controller_addr)
        log(f"NOTIFY   controller <- {kind} {service} {ip}:{port} ({reason})")
    except OSError as e:
        log(f"NOTIFY   failed ({e})")


def purge_loop(ttl, check_interval):
    """Remove servers whose advertisements stopped arriving."""
    while True:
        time.sleep(check_interval)
        now = time.time()
        with lock:
            for service in list(registry):
                for ep in list(registry[service]):
                    if now - registry[service][ep] > ttl:
                        del registry[service][ep]
                        log(f"TIMEOUT  {service} {ep[0]}:{ep[1]} (no advertisement for {ttl}s)")
                        notify("SERVER_DOWN", service, ep[0], ep[1], "timeout")
                if not registry[service]:
                    del registry[service]


def handle(data, addr, sock):
    try:
        parts = data.decode().strip().split("|")
    except UnicodeDecodeError:
        log(f"BAD      undecodable datagram from {addr}")
        return
    cmd = parts[0]

    if cmd in ("ADVERTISE", "DEREGISTER") and len(parts) == 4:
        _, service, ip, port = parts
        try:
            ep = (ip, int(port))
        except ValueError:
            log(f"BAD      invalid port in {parts}")
            return
        with lock:
            if cmd == "ADVERTISE":
                new = ep not in registry.get(service, {})
                registry.setdefault(service, {})[ep] = time.time()
                if new:
                    log(f"REGISTER {service} {ip}:{port} (from {addr[0]})")
                    notify("SERVER_UP", service, ip, ep[1], "new")
            else:
                if registry.get(service, {}).pop(ep, None) is not None:
                    log(f"DEREGISTER {service} {ip}:{port}")
                    notify("SERVER_DOWN", service, ip, ep[1], "deregister")
                    if not registry[service]:
                        del registry[service]

    elif cmd == "DISCOVER" and len(parts) == 2:
        service = parts[1]
        with lock:
            eps = [f"{ip}:{p}" for (ip, p) in registry.get(service, {})]
        reply = f"RESPONSE|{service}|{','.join(eps)}"
        sock.sendto(reply.encode(), addr)
        log(f"DISCOVER {service} from {addr[0]} -> {len(eps)} server(s)")
    else:
        log(f"BAD      malformed message from {addr}: {data[:60]!r}")


def main():
    global controller_addr
    ap = argparse.ArgumentParser(description="UDP service registry")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--ttl", type=float, default=6.0, help="seconds before an entry expires")
    ap.add_argument("--controller-ip", default="10.0.0.254",
                    help="where to send server up/down events")
    ap.add_argument("--controller-port", type=int, default=6000)
    ap.add_argument("--no-controller", action="store_true",
                    help="do not send events to the controller")
    args = ap.parse_args()

    if not args.no_controller:
        controller_addr = (args.controller_ip, args.controller_port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    log(f"Registry listening on {args.bind}:{args.port} (ttl={args.ttl}s, "
        f"controller={controller_addr})")

    threading.Thread(target=purge_loop, args=(args.ttl, 1.0), daemon=True).start()

    try:
        while True:
            data, addr = sock.recvfrom(2048)
            handle(data, addr, sock)
    except KeyboardInterrupt:
        log("Registry stopped")


if __name__ == "__main__":
    main()
