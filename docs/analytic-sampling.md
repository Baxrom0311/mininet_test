# Analytical time steps and fractional metrics

The analytical engine now honors the scenario's `sample` interval, with a default
of one second. Set it in scenario JSON or submit the scenario through the API:

```json
{
  "duration": 2.5,
  "sample": 0.1,
  "apps": [{"id": "video", "type": "udp", "src": "h0x0", "dst": "h2x0",
            "rate": "1Mbps", "start": 0, "stop": 2.5}],
  "events": []
}
```

The existing sampling bounds apply: numeric `0.001..3600` seconds, with at most
100000 intervals per scenario. More intervals increase CPU time and result size;
these limits do not guarantee every large topology/flow combination fits RAM.
No UI editor changes are included.

Flow, link and node samples have exact `t_start`/`t_end`; `t` is the interval end.
For duration 1.1 and sample .25 the intervals are `[0,.25]`, `[.25,.5]`,
`[.5,.75]`, `[.75,1]`, `[1,1.1]`. New result JSON records `sample_s=.25`.
The last interval ends exactly at duration rather than overshooting it.

This remains a time-stepped flow approximation. Link events and app start/stop
are evaluated at sample boundaries, so an event at .25 with sample .1 is
processed at .3. Events retain requested `t` and add `actual_t` for their
event-loop processing time. Align app boundaries to the grid when exact active
interval coverage matters. Coarse steps can miss a short application entirely;
reducing the step reduces this error rather than introducing packet events.

Mobility, VRRP selection and the LAG hold approximation use the actual interval
midpoint. Mobility/VRRP derive from their requested-time timelines and may affect
a bin whose midpoint is after a requested event even before the event loop
records it. Thus `actual_t` is not a universal physical effect timestamp.
Wi-Fi roaming retains its one-second outage approximation, quantized to the
chosen grid; selecting a shorter step does not shorten that modeled delay.
Route-event display timestamps still round to milliseconds; sample interval
bounds retain their full numeric precision.

Analytical throughput/delay/jitter/loss summaries weight bins by duration.
For one second of 1 Mbps followed by a .25-second outage, mean throughput is
.8 Mbps and time-averaged loss is 20%. The short tail is not half of the run.
Finite TCP transfers continue to integrate transferred bits over actual bin
lengths. Constant-path regressions preserve total bytes and completion time across
sample sizes; changing topology timing can still change a run's result.
Link drops and node discards are fluid estimates and may be fractional packets;
six decimal places prevent integer rounding from erasing short-bin drops.

Application postprocessing also respects explicit intervals. VoIP MOS/delay/loss/
throughput and multicast receiver summaries use duration weighting. FTP's
fallback completion estimator integrates observed intervals without assigning
bytes to gaps. Availability uses observed active seconds, clips to active bounds,
and separates outages across unobserved gaps. Malformed, mixed or overlapping
explicit intervals are rejected rather than counted twice. Legacy timestamp-only
application/availability processing retains its historical behavior.

Archived analytic runs **without `sample_s` keep their one-second grid**, even
when their old input requested another sample value. Such inputs were previously
ignored; they cannot establish a finer historical resolution. No archived JSON
is rewritten. New analytic results must match the recorded input sample in paired
comparisons. ns-3 continues to use its recorded scenario sample. Source/runtime
matching and complete-bin window rules remain in force; see
[analysis-windows.md](analysis-windows.md).

The change improves sampling resolution, not protocol calibration or packet
accuracy. It does not replace real FRR or ns-3 measurements.

```sh
python -m unittest lab.tests.test_analytic_sampling lab.tests.test_sampling_compatibility lab.tests.test_fractional_postprocess -v
```
