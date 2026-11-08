import assert from 'node:assert/strict'
import test from 'node:test'
import {APPLICATIONS,changeAppType,newApp,parseStudyValues,paramOptions,receiversOf,renameScenarioDevice,toParam} from '../.test-build/scenario.js'

test('all application types have distinct defaults without stale protocol fields',()=>{
  const original={id:'voice',type:'voip',src:'h1',dst:'h2',start:.5,stop:9,dscp:46,codec:'g729',rate:'invalid',custom:'preserve'}
  for(const {value} of APPLICATIONS){
    const result=changeAppType(original,value,['h1','h2','h3'])
    assert.equal(result.type,value);assert.equal(result.dscp,46);assert.equal(result.custom,'preserve')
    assert.equal(result.start,.5);assert.equal(original.rate,'invalid')
    if(value!=='voip') assert.equal(result.codec,undefined)
    if(!['udp','mcast'].includes(value)) assert.equal(result.rate,undefined)
  }
  assert.equal(changeAppType(original,'http',[]).think,5)
  assert.equal(changeAppType(original,'ftp',[]).direction,'download')
  assert.equal(changeAppType(original,'ping',[]).interval,.2)
})
test('multicast conversion excludes source and converts back to a scalar destination',()=>{
  const original={id:'a',type:'udp',src:'h1',dst:'h1'}
  const multicast=changeAppType(original,'mcast',['h1','h2','h3'])
  assert.deepEqual(multicast.dst,['h2']);assert.equal(multicast.group,'239.1.1.1')
  assert.equal(changeAppType({...multicast,dst:['h2','h3']},'tcp',[]).dst,'h2')
  assert.deepEqual(receiversOf({...multicast,dst:' h2, h3 '}),['h2','h3'])
  assert.deepEqual(receiversOf({...multicast,dst:undefined,receivers:['h3']}),['h3'])
})
test('new applications get unused identifiers and a valid window in one-second scenarios',()=>{
  const sc={duration:1,apps:[{id:'a1'},{id:'a3'}],events:[]}
  const app=newApp(sc,['h1','h2'],['h1','h2'])
  assert.equal(app.id,'a2');assert.equal(app.start,0);assert.equal(app.stop,1)
  assert.equal(app.src,'h1');assert.equal(app.dst,'h2');assert.equal(sc.apps.length,2)
})
test('device renames preserve multicast arrays, legacy recipients, failures and event references',()=>{
  const sc={apps:[{src:'old',dst:'h2'},{type:'mcast',src:'h1',dst:['old','h2']},
    {type:'mcast',src:'h1',dst:'old, h2'},{type:'mcast',src:'h1',receivers:['old']}],
    events:[{device:'old'}],failures:{devices:{targets:['old','h2']},links:{targets:['old']}}}
  renameScenarioDevice(sc,'old','new')
  assert.equal(sc.apps[0].src,'new');assert.deepEqual(sc.apps[1].dst,['new','h2'])
  assert.equal(sc.apps[2].dst,'new,h2');assert.deepEqual(sc.apps[3].receivers,['new'])
  assert.equal(sc.events[0].device,'new');assert.deepEqual(sc.failures.devices.targets,['new','h2'])
  assert.deepEqual(sc.failures.links.targets,['old'])
})
test('study values preserve units and codec names, parse signed/exponent numbers and reject holes',()=>{
  assert.deepEqual(parseStudyValues(' -1, .25, +2, 1e2, 2Mbps, g711 '),[-1,.25,2,100,'2Mbps','g711'])
  assert.deepEqual(parseStudyValues('9007199254740993'),['9007199254740993'])
  assert.deepEqual(parseStudyValues('  '),[])
  for(const value of ['1,,2','1,','1e999']) assert.throws(()=>parseStudyValues(value))
})
test('failure study parameters use group metadata rather than an unknown target key',()=>{
  assert.deepEqual(toParam('failure|devices|mtbf_s'),{kind:'failure',group:'devices',field:'mtbf_s'})
  assert.deepEqual(toParam('failure|seed|seed'),{kind:'failure',field:'seed'})
  assert.deepEqual(toParam('app|voice|codec'),{kind:'app',app:'voice',field:'codec'})
})
test('study options expose application models and failure groups and respect Mininet RIP limits',()=>{
  const project={links:{},devices:{r1:{type:'router',config:{daemons:['ripd']}}}}
  const sc={apps:[{id:'voice',type:'voip'},{id:'web',type:'http'},{id:'file',type:'ftp'},{id:'tv',type:'mcast'}],failures:{devices:{}}}
  const values=paramOptions(project,sc,'analytic').map(option=>option.value)
  for(const value of ['app|voice|codec','app|web|page','app|web|think','app|file|file','app|tv|rate','failure|devices|mtbf_s','failure|seed|seed','router|r1|rip_update']) assert.ok(values.includes(value),value)
  assert.ok(!values.includes('app|voice|rate'))
  assert.ok(!paramOptions(project,sc,'mininet').some(option=>option.value.startsWith('router|')))
})
