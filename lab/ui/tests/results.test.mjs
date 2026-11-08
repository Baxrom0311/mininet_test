import assert from 'node:assert/strict'
import test from 'node:test'
import {csvCell,filterRuns,isActive,resultsCsv,runState,visibleFlows} from '../.test-build/results.js'
import {pointAt,samplePoints} from '../.test-build/chartSampling.js'
import {fmtBps,fmtMs,fmtPct} from '../.test-build/format.js'

test('missing measurements are not presented as zero',()=>{
  for(const value of [null,undefined,NaN,Infinity]) {
    assert.equal(fmtBps(value),'—');assert.equal(fmtMs(value),'—');assert.equal(fmtPct(value),'—')
  }
  assert.equal(fmtBps(0),'0 b/s');assert.equal(fmtPct(0),'0.00%')
})
test('queue overlay preserves actual phase and all active states',()=>{
  assert.equal(runState({status:'running',queue_state:'queued'}),'queued')
  for(const status of ['queued','running','cancelling']) assert.ok(isActive(status))
  for(const status of ['done','error','cancelled','interrupted']) assert.ok(!isActive(status))
})
test('CSV quotes commas, quotes and line breaks and uses flow keys',()=>{
  const run={run_id:'r',scenario_name:'test,"case"\nline',engine:'analytic',flows:{
    visible:{summary:{avg_thr_bps:0}},hidden:{hidden:true,summary:{avg_thr_bps:999}},
  }}
  assert.equal(visibleFlows(run).length,1)
  const csv=resultsCsv([run])
  assert.ok(csv.includes('"test,""case""\nline"'))
  assert.ok(csv.includes('"visible","0"'))
  assert.ok(!csv.includes('999'))
  assert.ok(csv.endsWith('\r\n'))
  assert.equal(csvCell(null),'""')
})
test('history search matches labels, scenario, identity and studies on the supplied page',()=>{
  const rows=[{run_id:'r1',label:'Ovoz',scenario_name:'lab'},{run_id:'r2',scenario_name:'test',study_id:'study'}]
  assert.deepEqual(filterRuns(rows,' OVOZ '),[rows[0]])
  assert.deepEqual(filterRuns(rows,'study'),[rows[1]])
  assert.deepEqual(filterRuns(rows,'absent'),[])
  assert.equal(filterRuns(rows,'').length,2)
})
test('large chart signals retain endpoints, extrema and segment boundaries',()=>{
  const points=Array.from({length:100000},(_,x)=>({x,y:x===1234 ? 9000 : x===50000 ? -50 : x===33333 ? null : x%11}))
  const result=samplePoints(points)
  assert.ok(result.points.length<=1200)
  assert.equal(result.points[0].x,0);assert.equal(result.points.at(-1).x,99999)
  assert.ok(result.points.some(point=>point.x===1234 && point.y===9000))
  assert.ok(result.points.some(point=>point.x===50000 && point.y===-50))
  assert.equal(result.yMax,9000);assert.equal(result.xMax,99999)
  for(let i=1;i<result.points.length;i++) {
    const a=result.points[i-1],b=result.points[i]
    if(a.segment===b.segment) assert.ok(!(a.x<33333 && b.x>33333))
  }
  assert.equal(points[33333].y,null)
})
test('small charts remain exact and empty/nonfinite samples cannot break domains',()=>{
  const source=[{x:0,y:0},{x:1,y:null},{x:2,y:2}]
  const result=samplePoints(source)
  assert.deepEqual(result.points.map(({x,y})=>({x,y})),[source[0],source[2]])
  assert.notEqual(result.points[0].segment,result.points[1].segment)
  assert.equal(samplePoints([]).valid,0)
  assert.equal(samplePoints([{x:Infinity,y:2},{x:1,y:NaN}]).valid,0)
  for(const limit of [0,3,1201,1.5]) assert.throws(()=>samplePoints([],limit))
})
test('hover lookup uses original sample values and handles unordered timestamps',()=>{
  const points=[{x:0,y:1},{x:1,y:null},{x:2,y:4},{x:2,y:5}]
  assert.equal(pointAt(points,2,true),points[2])
  assert.equal(pointAt(points,1,true).y,null)
  assert.equal(pointAt(points,9,true),undefined)
  const unordered=[points[2],points[0],points[1]]
  assert.equal(samplePoints(unordered).ordered,false)
  assert.equal(pointAt(unordered,0,false),points[0])
  assert.equal(samplePoints([{x:0,y:1},{x:NaN,y:null},{x:2,y:3}]).ordered,false)
})
