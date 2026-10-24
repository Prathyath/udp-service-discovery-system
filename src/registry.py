#!/usr/bin/env python3
"""UDP service registry.

Receives ADVERTISE / DEREGISTER / DISCOVER datagrams and keeps a table of
live services. Entries not refreshed within --ttl seconds are purged.

Protocol (pipe-separated text):
  ADVERTISE|service|ip|port     server -> registry
  DEREGISTER|service|ip|port    server -> registry
  DISCOVER|service              client -> registry
  RESPONSE|service|ip:port,...  registry -> client (empty list if none)
"""
import argparse
import socket
import threading
import time

registry = {}            # {service: {(ip, port): last_seen}}
lock = threading.Lock()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
                        # TODO (SDN integration): notify controller that this server is down
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
            else:
                if registry.get(service, {}).pop(ep, None) is not None:
                    log(f"DEREGISTER {service} {ip}:{port}")
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
    ap = argparse.ArgumentParser(description="UDP service registry")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--ttl", type=float, default=6.0, help="seconds before an entry expires")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    log(f"Registry listening on {args.bind}:{args.port} (ttl={args.ttl}s)")

    threading.Thread(target=purge_loop, args=(args.ttl, 1.0), daemon=True).start()

    try:
        while True:
            data, addr = sock.recvfrom(2048)
            handle(data, addr, sock)
    except KeyboardInterrupt:
        log("Registry stopped")


if __name__ == "__main__":
    main()
