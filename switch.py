#!/usr/bin/python3
import sys
import struct
import wrapper
import threading
import time
from wrapper import recv_from_any_link, send_to_link, get_switch_mac, get_interface_name

root_id = -1
switch_id = -1
def parse_ethernet_header(data):
    # Unpack the header fields from the byte array
    #dest_mac, src_mac, ethertype = struct.unpack('!6s6sH', data[:14])
    dest_mac = data[0:6]
    src_mac = data[6:12]
    
    # Extract ethertype. Under 802.1Q, this may be the bytes from the VLAN TAG
    ether_type = (data[12] << 8) + data[13]

    vlan_id = -1
    # Check for VLAN tag (0x8100 in network byte order is b'\x81\x00')
    if ether_type == 0x8200:
        vlan_tci = int.from_bytes(data[14:16], byteorder='big')
        vlan_id = vlan_tci & 0x0FFF  # extract the 12-bit VLAN ID
        ether_type = (data[16] << 8) + data[17]

    return dest_mac, src_mac, ether_type, vlan_id
def create_vlan_tag(vlan_id):
    # 0x8100 for the Ethertype for 802.1Q
    # vlan_id & 0x0FFF ensures that only the last 12 bits are used
    return struct.pack('!H', 0x8200) + struct.pack('!H', vlan_id & 0x0FFF)

def create_header_STP(root_bridge_id, sender_bridge_id, root_path_cost):
    root_bridge_id &= 0xFFFFFFFFFFFFFFFF
    sender_bridge_id &= 0xFFFFFFFFFFFFFFFF
    root_path_cost &= 0xFFFFFFFFFFFFFFFF

    
    header = struct.pack('!QQQ', root_bridge_id, sender_bridge_id, root_path_cost)

    return header
def extract_header_STP(data):
    root_bridge_id, sender_bridge_id, root_path_cost = struct.unpack('!QQQ', data[:24])
    return root_bridge_id, sender_bridge_id, root_path_cost

#verifica daca o adresa exista in tabela CAM
def check_addr_exist(MAC_Table, addr):
    for pair in MAC_Table:
        if pair[0] == addr:
            return (True, pair[1])
    return (False, -1)

def get_vlan_of_port(port, port_vlan):
    for pair in port_vlan:
        if pair[0] == port:
            return pair[1]
    return -1    

#verifica daca portul are un anumit vlan
def check_same_vlan(port, vlan, port_vlan):
    if int(get_vlan_of_port(port, port_vlan)) == int(vlan):
        return True
    else:
        return False
    #verific daca un port este blocat
def check_port_is_block(port, port_state):
    for pair in port_state:
        if pair[0] == port:
            if pair[1] == "block":
                return True
            else:
                return False    
def send_broadcast(state_port, interfaces, interface, data_for_trunk,
                    data_for_acces, vlan_id, port_vlan, length_for_trunk,
                    length_for_acces):
    for i in interfaces:
        if i != interface:
            if get_vlan_of_port(i, port_vlan) == 'T':
                if not check_port_is_block(i, state_port):                       
                    send_to_link(i, data_for_trunk, length_for_trunk)
            else:
                if check_same_vlan(i, vlan_id, port_vlan):
                    send_to_link(i, data_for_acces, length_for_acces)

#functie pentru a citi din fisier
def read_line(line, interface_vlan):
    words = line.split()

    interface_name = words[0]
    vlan = words[1]

    interface_vlan.append((interface_name, vlan))
def create_bdpu_packet(root_id, switch_id, path_cost):
    root_bridge_id = root_id
    sender_bridge_id = switch_id
    sender_path_cost = path_cost
    dataSTP = create_header_STP(root_bridge_id, sender_bridge_id, sender_path_cost)
    dest_mac = bytes(bytearray([0x01, 0x80, 0xC2, 0x00, 0x00, 0x00]))
    src_mac = get_switch_mac()
    packet = dest_mac + src_mac + dataSTP
    return packet

def send_bdpu_every_sec(state_port):
    while True:
        # TODO Send BDPU every second if necessary
        time.sleep(1)
        if root_id == switch_id:
            for pair in state_port:
                packet = create_bdpu_packet(root_id, switch_id, 0)
                send_to_link(pair[0], packet, 36)

def receive_BDPU(state_port, port, data, root_port, root_path_cost):
    root_bridge_id_receive, sender_bridge_id_receive, root_path_cost_receive = extract_header_STP(data[12:])
    global root_id
    global switch_id
    if root_bridge_id_receive < root_id:
        root_path_cost = root_path_cost_receive + 10
        root_port = port
        if root_id == switch_id:
            for i, pair in enumerate(state_port):
                if pair[0] != root_port:
                    state_port[i] = (pair[0] ,"block")               
        for i, pair in enumerate(state_port):
            if pair[0] == root_port:
                 if pair[1] == "block":
                    state_port[i] = (pair[0] ,"listen")
        root_id = root_bridge_id_receive
        for pair in state_port:
            packet = create_bdpu_packet(root_id, switch_id, root_path_cost)
            send_to_link(pair[0], packet, 36)
    else:
        if root_id == root_bridge_id_receive:
            if port == root_port and root_path_cost_receive + 10 < root_path_cost:
                root_path_cost = root_path_cost_receive + 10
            else:
                if port != root_port:
                    if root_path_cost_receive > root_path_cost:
                        for i, pair in enumerate(state_port):
                            if pair[0] == port:
                                if pair[1] == "block":
                                    state_port[i] = (pair[0] ,"listen")
        else:
            if sender_bridge_id_receive == switch_id:
                for i, pair in enumerate(state_port):
                    if pair[0] == port:
                        state_port[i] = (pair[0] ,"block")

    if(root_id == switch_id):
        for i, pair in enumerate(state_port):
            state_port[i] = (pair[0] ,"listen")

    return root_port, root_path_cost        


def main():
    # init returns the max interface number. Our interfaces
    # are 0, 1, 2, ..., init_ret value + 1
    id = sys.argv[1]

    num_interfaces = wrapper.init(sys.argv[2:])
    interfaces = range(0, num_interfaces)

    # fisierul de configuratie de unde citim
    file_name = "configs/switch" + id +".cfg"

    # lista de perechi nume_interfata si vlan-ul corespunzator

    interface_vlan = []
    # prioritatea switch-ului
    priority = -1


    try:
        with open(file_name, 'r') as file:
            # Citeste prima linie care contine "SWITCH PRIORITY"
            priority = file.readline().strip()
            for line in file:
                line = line.strip()
                if line:
                    read_line(line, interface_vlan)
    except FileNotFoundError:
        print(f"Fisierul {file_name} nu a fost gasit.")
    except Exception as e:
        print(f"A intervenit o eroare: {e}")

    priority = int(priority)
    global root_id
    global switch_id
    root_id = priority
    switch_id = priority

    
    port_vlan = []
    # Calculez pt fiecare port vlan-ul lui
    for i in interfaces:
        for pair in interface_vlan:
            if pair[0] == get_interface_name(i):
                newPair = (i, pair[1])
                port_vlan.append(newPair)
    state_port =[]
    for i in interfaces:
        if get_vlan_of_port(i, port_vlan) == 'T':
            pair = (i , "block")
            state_port.append(pair)      
    t = threading.Thread(target=send_bdpu_every_sec, args = (state_port,))
    t.start()
    MAC_Table = []
    root_port = -1
    root_path_cost = 0
    while True:
        interface, data, length = recv_from_any_link()

        dest_mac, src_mac, ethertype, vlan_id = parse_ethernet_header(data)

        #salvez adresele mac ca si numere pentru a le putea compara
        dest_mac_int = int.from_bytes(dest_mac, byteorder='big')
        src_mac_int = int.from_bytes(src_mac, byteorder='big')

        STP_mac_dest = int.from_bytes(bytes(bytearray([0x01, 0x80, 0xC2, 0x00, 0x00, 0x00])), byteorder='big')
        if STP_mac_dest == dest_mac_int:
            root_port, root_path_cost = receive_BDPU(state_port, interface, data, root_port, root_path_cost)
        else:
            (find, interface_MAC_Table) = check_addr_exist(MAC_Table, src_mac_int)

            if not find:
                newMAC = (src_mac_int, interface)
                MAC_Table.append(newMAC)
            
            come_from_trunk = True
            if vlan_id == -1:
                vlan_id = get_vlan_of_port(interface, port_vlan)
                come_from_trunk = False
        
            # Modific datele trimise in functie daca sunt trimise pe o interfata acces sau pe una trunk
            data_for_acces = data
            data_for_trunk = data
            length_for_trunk = length
            length_for_acces = length
            if come_from_trunk:
                length_for_acces = length - 4
                data_for_acces = data[0:12] + data[16:]
            else:
                length_for_trunk = length + 4
                data_for_trunk = data[0:12] + create_vlan_tag(int(vlan_id)) + data[12:]     

            if dest_mac_int == 0xFFFFFFFFFFFF:
                send_broadcast(state_port, interfaces, interface, data_for_trunk, data_for_acces,
                            vlan_id, port_vlan, length_for_trunk, length_for_acces)    
            else:
                (find, interface_MAC_Table) = check_addr_exist(MAC_Table, dest_mac_int)
                if find:
                    if get_vlan_of_port(interface_MAC_Table, port_vlan) == 'T':
                        if not check_port_is_block(interface_MAC_Table, state_port):
                            send_to_link(interface_MAC_Table, data_for_trunk, length_for_trunk)
                    else:
                        if check_same_vlan(interface_MAC_Table, vlan_id, port_vlan):
                            send_to_link(interface_MAC_Table, data_for_acces, length_for_acces)
                else:
                    send_broadcast(state_port, interfaces, interface, data_for_trunk, data_for_acces,
                            vlan_id, port_vlan, length_for_trunk, length_for_acces)

if __name__ == "__main__":
    main()
