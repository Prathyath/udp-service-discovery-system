"""Ryu controller for the UDP service discovery project.

- Learning switch for normal traffic (priority 1)
- Discovery traffic (UDP port 5000) allowed only from ALLOWED_HOSTS (priority 100)
- Unknown hosts sending to port 5000 are logged and blocked (priority 90 -> 95)
- Periodic flow-stats monitoring
"""
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    DEAD_DISPATCHER, set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, udp
from ryu.lib import hub

DISCOVERY_PORT = 5000
ALLOWED_HOSTS = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]

PRIO_TABLE_MISS = 0
PRIO_LEARNED = 1
PRIO_TO_CONTROLLER = 90
PRIO_BLOCK = 95
PRIO_DISCOVERY = 100
STATS_INTERVAL = 5          # seconds


class DiscoveryController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.blocked = set()        # (dpid, ip) already blocked
        self.last_stats = {}
        self.monitor_thread = hub.spawn(self._monitor)

    # ---------- helpers ----------
    def add_flow(self, dp, priority, match, actions, idle_timeout=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = []
        if actions:   # empty action list = drop
            inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                      instructions=inst, idle_timeout=idle_timeout))

    # ---------- switch connect ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        to_ctrl = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        normal = [parser.OFPActionOutput(ofp.OFPP_NORMAL)]

        # table-miss: unknown packets go to the controller
        self.add_flow(dp, PRIO_TABLE_MISS, parser.OFPMatch(), to_ctrl)

        # allowed hosts may send to the discovery port
        for ip in ALLOWED_HOSTS:
            match = parser.OFPMatch(eth_type=0x0800, ip_proto=17,
                                    ipv4_src=ip, udp_dst=DISCOVERY_PORT)
            self.add_flow(dp, PRIO_DISCOVERY, match, normal)

        # replies from the registry
        match = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_src=DISCOVERY_PORT)
        self.add_flow(dp, PRIO_DISCOVERY, match, normal)

        # everyone else sending to the discovery port -> controller decides
        match = parser.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=DISCOVERY_PORT)
        self.add_flow(dp, PRIO_TO_CONTROLLER, match, to_ctrl)

        self.logger.info("s%s: discovery flows installed (allowed hosts: %s)",
                         dp.id, ", ".join(ALLOWED_HOSTS))

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        else:
            self.datapaths.pop(dp.id, None)
            self.blocked = {b for b in self.blocked if b[0] != dp.id}

    # ---------- packet-in: block rogues, learn MACs ----------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        ip = pkt.get_protocol(ipv4.ipv4)
        u = pkt.get_protocol(udp.udp)
        if ip and u and u.dst_port == DISCOVERY_PORT and ip.src not in ALLOWED_HOSTS:
            if (dp.id, ip.src) not in self.blocked:
                self.blocked.add((dp.id, ip.src))
                self.logger.warning("s%s: BLOCKED unauthorized host %s (port %d, "
                                    "seen on switch port %s)",
                                    dp.id, ip.src, DISCOVERY_PORT, in_port)
                match = parser.OFPMatch(eth_type=0x0800, ip_proto=17,
                                        ipv4_src=ip.src, udp_dst=DISCOVERY_PORT)
                self.add_flow(dp, PRIO_BLOCK, match, [])
            return      # drop this packet

        # learning switch
        src, dst = eth.src, eth.dst
        table = self.mac_to_port.setdefault(dp.id, {})
        table[src] = in_port
        out_port = table.get(dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            self.add_flow(dp, PRIO_LEARNED, match, actions)

        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions, data=msg.data))

    # ---------- monitoring ----------
    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp))
            hub.sleep(STATS_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        requests = replies = dropped = 0
        for stat in ev.msg.body:
            if stat.priority == PRIO_DISCOVERY:
                if stat.match.get("udp_dst") is not None:
                    requests += stat.packet_count
                elif stat.match.get("udp_src") is not None:
                    replies += stat.packet_count
            elif stat.priority == PRIO_BLOCK:
                dropped += stat.packet_count
        current = (requests, replies, dropped)
        if self.last_stats.get(dpid) != current:
            self.last_stats[dpid] = current
            self.logger.info("s%s discovery: to-registry=%d replies=%d blocked-drops=%d",
                             dpid, requests, replies, dropped)
