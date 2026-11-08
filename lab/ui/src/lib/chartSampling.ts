export interface ChartPoint {x:number;y:number|null|undefined}
export interface DisplayPoint extends ChartPoint {segment:number}
export function samplePoints(points:ChartPoint[],limit=1200) {
  if (!Number.isInteger(limit) || limit < 4 || limit > 1200) throw new Error('Point limit: 4..1200')
  const width=points.length <= limit ? 1 : Math.max(1,Math.ceil(points.length/Math.floor(limit/4)))
  const output:DisplayPoint[]=[]
  let segment=0,gap=false,xMax=0,yMax=0,valid=0,ordered=true,previous=-Infinity
  for (let start=0;start < points.length;start+=width) {
    let first:number|undefined,last:number|undefined,min:number|undefined,max:number|undefined
    const candidates=new Map<number,DisplayPoint>()
    for (let index=start;index < Math.min(start+width,points.length);index++) {
      const point=points[index]
      if (!Number.isFinite(point.x)) ordered=false
      else {if (point.x < previous) ordered=false;previous=point.x;xMax=Math.max(xMax,point.x)}
      if (!Number.isFinite(point.x) || point.y == null || !Number.isFinite(point.y)) {gap=true;continue}
      if (gap) {segment++;gap=false}
      valid++;yMax=Math.max(yMax,point.y)
      const value={...point,segment}
      if (first == null) first=index
      last=index
      if (min == null || point.y < points[min].y!) min=index
      if (max == null || point.y > points[max].y!) max=index
      candidates.set(index,value)
      // Only extrema/endpoints of this bin are retained, rather than every point.
      for (const key of candidates.keys()) if (![first,last,min,max].includes(key)) candidates.delete(key)
    }
    for (const [,point] of [...candidates.entries()].sort((a,b)=>a[0]-b[0])) output.push(point)
  }
  return {points:output,xMax,yMax,valid,ordered,reduced:output.length < valid}
}

export function pointAt(points:ChartPoint[],x:number,ordered:boolean) {
  if (!ordered) return points.find(point => point.x === x)
  let lo=0,hi=points.length
  while (lo < hi) {const mid=(lo+hi)>>>1;if (points[mid].x < x) lo=mid+1;else hi=mid}
  return points[lo]?.x === x ? points[lo] : undefined
}
