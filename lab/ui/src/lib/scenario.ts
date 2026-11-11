import type { Project, Scenario, ScenarioApp } from './types'

export const APPLICATIONS = [
  {value:'udp',label:'UDP (CBR)',hint:'Belgilangan tezlikdagi doimiy trafik.'},
  {value:'tcp',label:'TCP (bulk)',hint:'Mavjud sig‘imdan foydalanadigan TCP oqimi.'},
  {value:'ping',label:'Ping',hint:'ICMP orqali RTT va yo‘qotishni o‘lchash.'},
  {value:'voip',label:'VoIP',hint:'Ikki tomonlama ovoz; natijada MOS va ovoz sifati.'},
  {value:'http',label:'HTTP',hint:'Mijoz serverdan sahifalar yuklaydi; javob vaqti hisoblanadi.'},
  {value:'ftp',label:'FTP',hint:'Belgilangan hajmdagi fayl; uzatish vaqti hisoblanadi.'},
  {value:'mcast',label:'Multicast',hint:'IPv4 guruhiga bitta manbadan bir nechta qabul qiluvchi. Routerlarda PIM kerak.'},
] as const
export const receiversOf = (app:ScenarioApp):string[] => Array.isArray(app.dst) ? app.dst :
  app.dst != null ? app.dst.split(',').map(value=>value.trim()).filter(Boolean) : app.receivers || []

export function changeAppType(app:ScenarioApp,type:ScenarioApp['type'],receivers:string[]):ScenarioApp {
  const result={...app,type}
  const unicast=receiversOf(app)[0] || receivers.find(name=>name!==app.src) || ''
  for(const key of ['rate','size','codec','page','think','file','direction','interval','bytes','group','receivers','dst_ip','dport'] as const) delete result[key]
  result.dst=unicast
  if(type==='udp' || type==='mcast') Object.assign(result,{rate:type==='mcast'?'4Mbps':'1Mbps',size:1000})
  if(type==='tcp') result.size=1000
  if(type==='ping') Object.assign(result,{size:64,interval:.2})
  if(type==='voip') result.codec='g711'
  if(type==='http') Object.assign(result,{page:'500KB',think:5})
  if(type==='ftp') Object.assign(result,{file:'10MB',direction:'download'})
  if(type==='mcast') {result.group='239.1.1.1';result.dst=receiversOf(app).filter(name=>receivers.includes(name) && name!==app.src);if(!result.dst.length) result.dst=receivers.filter(name=>name!==app.src).slice(0,1)}
  return result
}
export function newApp(sc:Scenario,endpoints:string[],receivers:string[]):ScenarioApp {
  let n=1;while(sc.apps.some(app=>app.id==='a'+n)) n++
  return changeAppType({id:'a'+n,type:'udp',src:endpoints[0] || '',dst:endpoints[endpoints.length-1] || '',start:0,stop:sc.duration},'udp',receivers)
}
export function renameScenarioDevice(sc:Scenario,oldName:string,newName:string) {
  for(const app of sc.apps || []) {
    if(app.src===oldName) app.src=newName
    if(Array.isArray(app.dst)) app.dst=app.dst.map(name=>name===oldName?newName:name)
    else if(app.type==='mcast' && typeof app.dst==='string') app.dst=app.dst.split(',').map(name=>name.trim()===oldName?newName:name.trim()).join(',')
    else if(app.dst===oldName) app.dst=newName
    if(app.receivers) app.receivers=app.receivers.map(name=>name===oldName?newName:name)
  }
  for(const event of sc.events || []) if(event.device===oldName) event.device=newName
  const targets=sc.failures?.devices?.targets
  if(Array.isArray(targets)) sc.failures!.devices!.targets=targets.map(name=>name===oldName?newName:name)
}

export const PARAM_DEFAULTS:Record<string,string>={bw:'1,2,5,10,20',delay:'1,5,10,20',loss:'0,1,5,10',queue:'10,100,1000',
  rate:'1Mbps,2Mbps,5Mbps',size:'200,1000,1400',tx_power:'5,15,25',channel:'36,40',codec:'g711,g729',
  page:'100KB,500KB,1MB',think:'1,3,5',file:'1MB,5MB,10MB',mtbf_s:'30,120,300',mttr_s:'1,5,30',seed:'1,2,3',rip_update:'5,15,30',rip_timeout:'30,90,180'}
export const FIELD_LABEL:Record<string,string>={bw:'tezlik (Mbit/s)',delay:'kechikish (ms)',loss:'yo‘qotish (%)',queue:'navbat (paket)',rate:'tezlik',size:'paket (B)',tx_power:'quvvat (dBm)',channel:'kanal',codec:'kodek',page:'sahifa hajmi',think:'so‘rov oralig‘i (s)',file:'fayl hajmi',mtbf_s:'MTBF (s)',mttr_s:'MTTR (s)',seed:'tasodifiylik urug‘i',rip_update:'RIP update (s)',rip_timeout:'RIP timeout (s)'}
export function paramOptions(project:Project,sc:Scenario,engine:string) {
  const options:{value:string;label:string}[]=[]
  const add=(kind:string,target:string,fields:string[],label:string)=>fields.forEach(field=>options.push({value:[kind,target,field].join('|'),label:label+' · '+FIELD_LABEL[field]}))
  for(const [id,link] of Object.entries(project.links)) add('link',id,['bw','delay','loss','queue'],`${id}: ${link.a.dev}↔${link.b.dev}`)
  for(const [id,device] of Object.entries(project.devices)) {
    if(device.type==='wifiap') add('ap',id,['tx_power','channel'],'Wi-Fi '+id)
    if(engine!=='mininet' && device.type==='router' && device.config.daemons?.includes('ripd')) add('router',id,['rip_update','rip_timeout'],id)
  }
  const fields:Record<ScenarioApp['type'],string[]>={udp:['rate','size'],tcp:['rate'],ping:[],voip:['codec'],http:['page','think'],ftp:['file'],mcast:['rate','size']}
  for(const app of sc.apps) add('app',app.id,fields[app.type] || [],'ilova '+app.id)
  for(const group of ['links','devices'] as const) if(sc.failures?.[group]) add('failure',group,['mtbf_s','mttr_s'],'nosozlik: '+(group==='links'?'kabellar':'qurilmalar'))
  if(sc.failures) add('failure','seed',['seed'],'nosozlik')
  return options
}
export function toParam(value:string) {
  const [kind,target,field]=value.split('|')
  return kind==='failure' ? {kind,field,...(field==='seed'?{}:{group:target})} : {kind,field,[kind]:target}
}
export function parseStudyValues(text:string):(number|string)[] {
  if(!text.trim()) return []
  return text.split(',').map(value=>{
    const trimmed=value.trim()
    if(!trimmed) throw new Error('Qiymatlar orasida bo‘sh element bor.')
    if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(trimmed)) return trimmed
    const number=Number(trimmed)
    if(!Number.isFinite(number)) throw new Error('Qiymat chekli son bo‘lishi kerak.')
    return Number.isInteger(number) && !Number.isSafeInteger(number) ? trimmed : number
  })
}
