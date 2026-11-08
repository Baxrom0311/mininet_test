import { useEffect, useRef, useState } from 'react'
import { Input } from './ui/input'

/** Keep partial typing local; native validity prevents submitting an empty required field. */
export function NumberInput({value,onChange,...props}:Omit<React.ComponentProps<typeof Input>,'value'|'onChange'|'type'> & {
  value:number|undefined;onChange:(value:number|undefined)=>void
}) {
  const [text,setText]=useState(value == null ? '' : String(value))
  const committed=useRef(value)
  useEffect(()=>{
    if(!Object.is(value,committed.current)) {committed.current=value;setText(value == null ? '' : String(value))}
  },[value])
  return <Input {...props} type="number" value={text} onChange={event=>{
    const raw=event.target.value;setText(raw)
    if(raw==='') {if(!props.required) {committed.current=undefined;onChange(undefined)};return}
    const parsed=Number(raw)
    if(Number.isFinite(parsed)) {committed.current=parsed;onChange(parsed)}
  }} />
}
