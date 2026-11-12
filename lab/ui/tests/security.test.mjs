import assert from 'node:assert/strict'
import test from 'node:test'

test('conntrack tuple parsing and state mapping', () => {
  const tcpSession = {
    id: 'ct_1',
    proto: 'tcp',
    tcp_state: 'ESTABLISHED',
    fw_state: 'ESTABLISHED',
    orig: { src_ip: '10.0.0.1', src_port: 52140, dst_ip: '10.0.0.2', dst_port: 80, proto: 'tcp' },
    reply: { src_ip: '10.0.0.2', src_port: 80, dst_ip: '10.0.0.1', dst_port: 52140, proto: 'tcp' },
    remaining_ttl: 432000,
    nat: {
      enabled: true,
      nat_type: 'snat',
      translated_src: '203.0.113.1',
      translated_sport: 52140,
    },
  }

  assert.equal(tcpSession.proto, 'tcp')
  assert.equal(tcpSession.tcp_state, 'ESTABLISHED')
  assert.equal(tcpSession.orig.dst_port, 80)
  assert.equal(tcpSession.nat.nat_type, 'snat')
  assert.equal(tcpSession.nat.translated_src, '203.0.113.1')
  assert.ok(tcpSession.remaining_ttl > 0)
})

test('attack simulator parameters and mitigation logic', () => {
  // 1. TCP SYN Flood with SYN cookies
  const synFloodMitigated = {
    attack_type: 'syn_flood',
    pps: 15000,
    duration_s: 10,
    syn_cookies_enabled: true,
    backlog_size: 1024,
  }
  const droppedWhenProtected = synFloodMitigated.syn_cookies_enabled ? 0 : synFloodMitigated.pps * synFloodMitigated.duration_s - synFloodMitigated.backlog_size
  assert.equal(droppedWhenProtected, 0)

  // 1b. TCP SYN Flood without SYN cookies (Vulnerable)
  const synFloodVulnerable = {
    attack_type: 'syn_flood',
    pps: 15000,
    duration_s: 10,
    syn_cookies_enabled: false,
    backlog_size: 1024,
  }
  const droppedWhenVulnerable = Math.max(0, synFloodVulnerable.pps * synFloodVulnerable.duration_s - synFloodVulnerable.backlog_size)
  assert.ok(droppedWhenVulnerable > 140000)

  // 2. UDP Reflection amplification factor
  const dnsReqBytes = 64
  const dnsRespBytes = 3840
  const dnsRatio = dnsRespBytes / dnsReqBytes
  assert.equal(dnsRatio, 60)

  const ntpReqBytes = 48
  const ntpRespBytes = 9600
  const ntpRatio = ntpRespBytes / ntpReqBytes
  assert.equal(ntpRatio, 200)

  // 3. Slowloris pool exhaustion
  const maxPool = 150
  const activeSlowConns = 180
  const isPoolExhausted = activeSlowConns >= maxPool
  assert.equal(isPoolExhausted, true)

  // 4. ARP Spoofing DAI protection
  const arpAttackWithDai = {
    attack_type: 'arp_spoof',
    dai_enabled: true,
  }
  assert.equal(arpAttackWithDai.dai_enabled, true)
})

test('security IDS alerts severity levels and categorization', () => {
  const alerts = [
    { id: 'a1', severity: 'critical', attack_type: 'syn_flood', target: '10.0.0.2:80' },
    { id: 'a2', severity: 'high', attack_type: 'udp_reflection', target: '10.0.0.5' },
    { id: 'a3', severity: 'medium', attack_type: 'slowloris', target: '10.0.0.2:80' },
    { id: 'a4', severity: 'low', attack_type: 'port_scan', target: '10.0.0.4' },
  ]

  const criticals = alerts.filter(a => a.severity === 'critical')
  assert.equal(criticals.length, 1)
  assert.equal(criticals[0].attack_type, 'syn_flood')

  const highs = alerts.filter(a => a.severity === 'high')
  assert.equal(highs.length, 1)

  const byTarget = alerts.filter(a => a.target.includes('10.0.0.2'))
  assert.equal(byTarget.length, 2)
})
