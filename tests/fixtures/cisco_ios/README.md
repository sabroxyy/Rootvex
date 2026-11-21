# Recorded Cisco IOS outputs

Layout: `<platform>/<DEVICE>/<command_slug>.txt`, where the slug is the command
lower-cased with non-alphanumerics replaced by `_`
(`show ip ospf neighbor` -> `show_ip_ospf_neighbor.txt`).

Scenario in this folder: **OSPF R1-R2 down because of an area mismatch**
(R1 Gi0/1 in area 0, R2 Gi0/1 in area 1). Interfaces are up/up.
Replace these with real captures from the GNS3 lab as it is built.
