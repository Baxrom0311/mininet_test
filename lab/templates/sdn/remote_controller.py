"""NetLab SDN masofaviy (Remote) kontroller qo'llanmasi.

NetLab Mininet switchlarini istalgan tashqi yoki Docker ichida ishlayotgan
SDN kontrollerga (ONOS, OpenDaylight, Floodlight, Faucet va h.k.) ulash imkonini beradi.

1. ONOS Kontrollerini Docker orqali ishga tushirish:
   $ docker run -d --name onos -p 6653:6653 -p 8181:8181 onosproject/onos:latest
   Web GUI: http://localhost:8181/onos/ui (login: karaf, parol: karaf)
   NetLab sozlamasi:
     type: "onos" yoki "remote"
     ip: "127.0.0.1"
     port: 6653

2. OpenDaylight (ODL) Kontrollerini Docker orqali ishga tushirish:
   $ docker run -d --name odl -p 6653:6653 -p 8181:8181 opendaylight/odl:latest
   NetLab sozlamasi:
     type: "odl" yoki "remote"
     ip: "127.0.0.1"
     port: 6653

3. Floodlight Kontrollerini ishga tushirish:
   $ docker run -d --name floodlight -p 6653:6653 -p 8080:8080 floodlight/floodlight
   NetLab sozlamasi:
     type: "floodlight" yoki "remote"
     ip: "127.0.0.1"
     port: 6653

4. Loyiha JSON formatida masofaviy kontroller ko'rinishi:
   "controllers": {
     "c_remote": {
       "type": "remote",
       "ip": "127.0.0.1",
       "port": 6653
     }
   }
"""
