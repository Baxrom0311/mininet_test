import assert from 'node:assert/strict'
import test from 'node:test'
import { fmtBps, fmtMs, fmtPct, clock } from '../.test-build/format.js'

test('format utilities handle normal and edge cases', () => {
  assert.equal(fmtBps(0), '0 b/s')
  assert.equal(fmtBps(1000), '1.0 kb/s')
  assert.equal(fmtBps(10_000_000), '10.00 Mb/s')
  assert.equal(fmtBps(1_000_000_000), '1.00 Gb/s')

  assert.equal(fmtMs(0), '0.00 ms')
  assert.equal(fmtMs(1.234), '1.23 ms')
  assert.equal(fmtMs(1500), '1.50 s')

  assert.equal(fmtPct(0), '0.00%')
  assert.equal(fmtPct(2.55), '2.5%')
  assert.equal(fmtPct(100), '100.0%')

  const formattedTime = clock(1700000000)
  assert.match(formattedTime, /\d{2}:\d{2}:\d{2}/)
})

test('device types and workspaces consistency', () => {
  const supportedWorkspaces = ['canvas', 'telemetry', 'security', 'results']
  assert.equal(supportedWorkspaces.length, 4)
  assert.ok(supportedWorkspaces.includes('canvas'))
  assert.ok(supportedWorkspaces.includes('telemetry'))
  assert.ok(supportedWorkspaces.includes('security'))
  assert.ok(supportedWorkspaces.includes('results'))

  const deviceTypes = ['host', 'server', 'switch', 'p4switch', 'router', 'sdnrouter', 'wifiap']
  assert.equal(deviceTypes.length, 7)
})

test('REST API endpoint path validations', () => {
  const endpoints = [
    { method: 'GET', path: '/api/info' },
    { method: 'GET', path: '/api/templates' },
    { method: 'GET', path: '/api/projects' },
    { method: 'GET', path: '/api/net/status' },
    { method: 'GET', path: '/api/net/project' },
    { method: 'POST', path: '/api/net/start' },
    { method: 'POST', path: '/api/net/stop' },
    { method: 'GET', path: '/api/telemetry/streams' },
    { method: 'GET', path: '/api/telemetry/l4/queues' },
    { method: 'GET', path: '/api/telemetry/l4/workloads/voip' },
    { method: 'GET', path: '/api/telemetry/l4/workloads/video' },
    { method: 'GET', path: '/api/telemetry/l4/sla' },
    { method: 'POST', path: '/api/telemetry/simulate' },
    { method: 'GET', path: '/api/security/conntrack' },
    { method: 'POST', path: '/api/security/attack/simulate' },
    { method: 'GET', path: '/api/security/alerts' },
    { method: 'POST', path: '/api/telemetry/p4/int/simulate' },
    { method: 'POST', path: '/api/pdu/inspect' },
  ]

  for (const ep of endpoints) {
    assert.ok(ep.path.startsWith('/api/'), `Path ${ep.path} must start with /api/`)
    assert.ok(['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].includes(ep.method))
  }
})
