import assert from 'node:assert/strict'
import test from 'node:test'

test('L4 telemetry metrics and conversions', () => {
  // Test CWND to in-flight KB conversion
  const cwnd = 24 // packets
  const mss = 1460 // bytes
  const inFlightKb = (cwnd * mss) / 1024
  assert.equal(Math.round(inFlightKb * 10) / 10, 34.2)

  // Test RTT and RTTVAR jitter calculation
  const rtt1 = 12.5
  const rtt2 = 14.2
  const jitter = Math.abs(rtt2 - rtt1)
  assert.equal(Math.round(jitter * 10) / 10, 1.7)

  // Test Delivery Rate bps to Mbps
  const deliveryRateBps = 85_400_000
  const rateMbps = deliveryRateBps / 1e6
  assert.equal(rateMbps, 85.4)
})

test('VoIP ITU-T G.107 E-Model MOS score evaluation logic', () => {
  // ITU-T G.107 E-Model R-factor calculation approximation:
  // R = 93.2 - Id - Ie_eff
  // where Id is delay impairment and Ie_eff is loss/equipment impairment
  const calculateMos = (delayMs, jitterMs, lossPct, codec = 'g711') => {
    const totalDelay = delayMs + 2 * jitterMs
    const id = totalDelay > 150 ? 0.024 * totalDelay + 0.11 * (totalDelay - 150) : 0.024 * totalDelay
    const ie = codec === 'g729' ? 11 : 0
    const bpl = codec === 'g729' ? 19 : 4.3
    const ieEff = ie + (95 - ie) * (lossPct / (lossPct + bpl))
    const r = Math.max(0, Math.min(100, 93.2 - id - ieEff))

    if (r < 0) return 1.0
    if (r > 100) return 4.5
    return 1 + 0.035 * r + r * (r - 60) * (100 - r) * 7e-6
  }

  // Ideal conditions: 10ms delay, 1ms jitter, 0% loss -> MOS should be ~4.3+ (Excellent)
  const idealMos = calculateMos(10, 1, 0, 'g711')
  assert.ok(idealMos >= 4.2 && idealMos <= 4.5, `Expected high MOS, got ${idealMos}`)

  // Degraded network: 250ms delay, 30ms jitter, 5% loss -> MOS should drop significantly
  const degradedMos = calculateMos(250, 30, 5, 'g711')
  assert.ok(degradedMos < 3.5, `Expected degraded MOS, got ${degradedMos}`)
})

test('Bufferbloat and Linux Qdisc queue dynamics', () => {
  const queuePackets = 120
  const limitPackets = 100
  const isOverflow = queuePackets > limitPackets
  const drops = isOverflow ? queuePackets - limitPackets : 0
  assert.equal(isOverflow, true)
  assert.equal(drops, 20)

  // Delay bloat calculation: queuing delay = (queue_packets * packet_size_bits) / bandwidth_bps
  const pktBits = 1500 * 8
  const bwBps = 10_000_000 // 10 Mbps
  const queueDelayMs = ((queuePackets * pktBits) / bwBps) * 1000
  assert.equal(queueDelayMs, 144) // 144ms queuing delay bloat!
})
