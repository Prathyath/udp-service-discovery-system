"""Ryu controller for the UDP service discovery project.

- Learning switch for normal traffic (priority 1)
- High-priority flow rules for discovery traffic, UDP port 5000 (priority 100)
- Periodic flow-stats monitoring of the discovery flows
"""
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    DEAD_DISPATCHER, set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib import hub

DISCOVERY_PORT = 5000
PRIO_TABLE_MISS = 0
PRIO_LEARNED = 1
PRIO_DISCOVERY = 100
STATS_INTERVAL = 5          # seconds


class DiscoveryController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.last_counts = {}
        self.monitor_thread = hub.spawn(self._monitor)

    # ---------- helpers ----------
    def add_flow(self, dp, priority, match, actions, idle_timeout=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                      instructions=inst, idle_timeout=idle_timeout))

    # ---------- switch connect ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser

        # table-miss: send unknown packets to the controller
        self.add_flow(dp, PRIO_TABLE_MISS, parser.OFPMatch(),
                      [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])

        # discovery traffic (requests to port 5000 and replies from port 5000)
        for field in ("udp_dst", "udp_src"):
            match = parser.OFPMatch(eth_type=0x0800, ip_proto=17, **{field: DISCOVERY_PORT})
            self.add_flow(dp, PRIO_DISCOVERY, match,
                          [parser.OFPActionOutput(ofp.OFPP_NORMAL)])
        self.logger.info("s%s: installed discovery flows (UDP port %d, priority %d)",
                         dp.id, DISCOVERY_PORT, PRIO_DISCOVERY)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        else:
            self.datapaths.pop(dp.id, None)

    # ---------- learning switch ----------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]

        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
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
        for stat in ev.msg.body:
            if stat.priority != PRIO_DISCOVERY:
                continue
            kind = "udp_dst" if stat.match.get("udp_dst") else "udp_src"
            key = (dpid, kind)
            if self.last_counts.get(key) != stat.packet_count:
                self.last_counts[key] = stat.packet_count
                self.logger.info("s%s discovery %s=%d: packets=%d bytes=%d",
                                 dpid, kind, DISCOVERY_PORT,
                                 stat.packet_count, stat.byte_count)
