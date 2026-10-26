#!/usr/bin/env python3
"""Mininet topology for the UDP service discovery project.

        h1 registry   (10.0.0.1)
        h2 server 1   (10.0.0.2)
        h3 server 2   (10.0.0.3)       all attached to switch s1,
        h4 client     (10.0.0.4)       which is managed by the remote
        h5 rogue host (10.0.0.5)       Ryu controller (port 6653)

Run:  sudo python3 topology/topo.py
(start the controller first: ryu-manager controller/sdn_ctrl.py)
"""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel


def run():
    net = Mininet(controller=RemoteController, switch=OVSSwitch,
                  autoSetMacs=True)
    net.addController("c0", ip="127.0.0.1", port=6653)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    hosts = {}
    for name, ip in [("h1", "10.0.0.1"), ("h2", "10.0.0.2"),
                     ("h3", "10.0.0.3"), ("h4", "10.0.0.4"),
                     ("h5", "10.0.0.5")]:
        hosts[name] = net.addHost(name, ip=ip + "/24")
        net.addLink(hosts[name], s1)

    net.start()
    print("\nRoles: h1=registry  h2,h3=servers  h4=client  h5=rogue")
    print("Example: h1 python3 src/registry.py > results/registry.log 2>&1 &\n")
    CLI(net)
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    run()
