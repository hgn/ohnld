common = {
        'verbose' : 'debug'
}

core = dict()
core['tx-interval'] = "10"
core['validity-time'] = 30

core['iface-name'] = "term00"

core['v4-mcast-addr'] = "224.0.0.1"
core['v4-mcast-port'] = 31001
core['v4-mcast-ttl'] = 10
core['v4-unicast-addr'] = "10.10.10.228"
core['terminal-v4-addr'] = "10.10.10.228"

terminal_ipc = dict()
terminal_ipc['url'] = "http://localhost:5180/api/v1/interface"
terminal_ipc['update-interval'] = "20"

update_ipc = dict()
update_ipc['max-update-interval'] = "5"
update_ipc['host'] = "127.0.0.1"
update_ipc['url'] =  "/api/v1/underlay-route-full-dynamic"
update_ipc['content-type'] = "json"
update_ipc['port'] = "16001"

network_announcement = list()
# the submitted local interface ip prefix
network_announcement.append([ "10.2.101.0", "29" ])

terminal_data = dict()

# the initial air address. is later updated by
# quering the interface
terminal_data['addr-air-v4'] = "192.166.10.10"
