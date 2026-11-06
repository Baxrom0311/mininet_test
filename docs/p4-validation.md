# P4/BMv2 validation gate

```bash
python3 -m lab.tests.real_p4 --compile-only --output /tmp/p4-compile.json
sudo timeout 180 python3 -m lab.tests.real_p4 --output /tmp/p4-real.json
```

Compile-only validates every bundled P4 template with the actual compiler and
reads the compiled pipeline JSON. It never treats the demo source outline as a
successful compilation. The full gate additionally requires root, Mininet, OVS,
ping, simple_switch and simple_switch_CLI, then executes the existing p4_l2_lab
real forwarding test under `/tmp/netlab-real.lock`.

Exit 0 is passed, 1 failed, 2 blocked prerequisites. Compilation may succeed while
runtime remains blocked; both outcomes are recorded separately. On this cloud
instance compiler/root/Mininet/BMv2 are missing. Neither compile nor real P4
forwarding has been claimed successful.

Install on a dedicated supported Ubuntu host using the repository's optional
`lab/install_lab.sh --p4` after reviewing its distribution-specific p4lang apt
repository. This session did not modify system repositories or disable package
signatures/TLS checks. P4 repository availability and package compatibility must
be verified on that host; they cannot be established by the demo parser tests.
