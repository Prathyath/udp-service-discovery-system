#!/usr/bin/env python3
"""Mininet topology for the UDP service discovery project.

        h1 registry   (10.0.0.1)
        h2 server 1   (10.0.0.2)
        h3 server 2   (10.0.0.3)       all attached to switch s1,
        h4 client     (10.0.0.4)       which is managed by the remote
        h5 rogue host (10.0.0.5)       Ryu controller (port 6653)
        root          (10.0.0.254)     the VM itself; the registry sends
                                       server up/down events to the
                                       controller here (UDP 6000)

Run:  sudo python3 topology/topo.py
(start the controller first: ryu-manager controller/sdn_ctrl.py)
"""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch, Node
from mininet.cli import CLI
from mininet.log import setLogLevel


def run():
    net = Mininet(controller=RemoteController, switch=OVSSwitch,
                  autoSetMacs=True)
    net.addController("c0", ip="127.0.0.1", port=6653)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    for name, ip in [("h1", "10.0.0.1"), ("h2", "10.0.0.2"),
                     ("h3", "10.0.0.3"), ("h4", "10.0.0.4"),
                     ("h5", "10.0.0.5")]:
        net.addLink(net.addHost(name, ip=ip + "/24"), s1)

    # connect the VM's root namespace (where Ryu runs) to the switch
    root = Node("root", inNamespace=False)
    intf = net.addLink(root, s1).intf1
    root.setIP("10.0.0.254/24", intf=intf)

    net.start()
    print("\nRoles: h1=registry  h2,h3=servers  h4=client  h5=rogue  "
          "root(10.0.0.254)=controller events")
    CLI(net)
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    run()
