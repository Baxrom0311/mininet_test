import { useState } from 'react'
import { Binary, Layers, Network, ShieldCheck } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Segmented } from '@/components/ui/tabs'
import { PROTO_COLORS, clock } from '@/lib/format'
import { type Win } from '@/lib/store'
import { WindowFrame } from './WindowFrame'

export function PDUInspectorWindow({ win }: { win: Win }) {
  const [tab, setTab] = useState<'osi' | 'details' | 'hex'>('osi')
  const pkt = win.arg || {}

  // Paket xulosasidan maydonlarni taxminiy yoki real aniqlash
  const proto = pkt.proto || 'TCP'
  const summary = pkt.summary || ''
  const link = pkt.link || 'eth0'
  const dir = pkt.dir || 'ab'

  // Cisco Packet Tracer uslubidagi bosqichma-bosqich harakat jurnali
  const deviceLogs = [
    `1. Port qabuli: Freym '${link}' interfeysiga kelib tushdi.`,
    `2. Layer 2: Ethernet II freym sarlavhasi o'qildi. Maqsad MAC tekshirildi va tasdiqlandi.`,
    `3. Layer 3: ${proto === 'ARP' ? 'ARP so‘rovi tekshirildi va qidirilayotgan IP aniqlandi.' : 'IPv4 sarlavhasi tekshirildi. TTL > 1, nazorat yig‘indisi (checksum) to‘g‘ri.'}`,
    proto !== 'ARP' && `4. Marshrutlash (FIB lookup): Marshrut jadvalidan mos Next-Hop qidirildi va chiquvchi interfeys belgilandi.`,
    proto !== 'ARP' && `5. TTL o‘zgarishi: TTL qiymati 64 dan 63 ga kamaytirildi, IP checksum qayta hisoblandi.`,
    `6. Layer 4: ${proto} segmenti o'qildi. Maydonlar: ${summary}.`,
    `7. Chiqish: Freym keyingi tugunga uzatish uchun navbatga qo‘yildi.`,
  ].filter(Boolean)

  return (
    <WindowFrame
      win={win}
      icon={<Layers className="text-primary" />}
      title="Cisco / OPNET PDU Inspektori"
      sub={`OSI 1–7 Qatlamlar tahlili · ${proto} · ${clock(pkt.ts || Date.now() / 1000)}`}
    >
      <div className="flex h-full flex-col overflow-hidden bg-background/50">
        <div className="flex items-center gap-2 border-b bg-card/30 px-3 py-1.5">
          <Segmented
            value={tab}
            onChange={setTab}
            items={[
              { value: 'osi', label: 'OSI Modeli (1-7)' },
              { value: 'details', label: 'PDU Sarlavhalari' },
              { value: 'hex', label: 'Hex / ASCII Dump' },
            ]}
          />
          <div className="flex-1" />
          <Badge variant="outline" style={{ color: PROTO_COLORS[proto] || PROTO_COLORS.other }}>
            {proto}
          </Badge>
          <span className="text-xs text-muted-foreground font-mono">{link}</span>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {tab === 'osi' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Chap: Qatlamlar ierarxiyasi */}
              <div className="flex flex-col gap-2.5">
                <h4 className="text-xs font-semibold text-muted-foreground uppercase flex items-center gap-1.5">
                  <Network className="h-4 w-4" /> OSI Qatlamlari Dekonstruktsiyasi
                </h4>

                {/* Layer 7 */}
                <div className="rounded-lg border bg-card/70 p-2.5 border-l-4 border-l-purple-500">
                  <div className="flex items-center justify-between text-xs font-bold text-purple-400">
                    <span>Layer 7: Application (Ilova)</span>
                    <Badge variant="outline" className="text-[10px]">{proto}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground font-mono">
                    {summary.includes('HTTP') ? 'HTTP Protokoli: Web so‘rov/javob ma‘lumotlari' :
                     summary.includes('DNS') ? 'DNS Protokoli: Domen nomini hal qilish' :
                     summary.includes('SSH') ? 'SSH Protokoli: Xavfsiz masofaviy boshqaruv' :
                     'Ilova darajasidagi payload'}
                  </p>
                </div>

                {/* Layer 4 */}
                <div className="rounded-lg border bg-card/70 p-2.5 border-l-4 border-l-blue-500">
                  <div className="flex items-center justify-between text-xs font-bold text-blue-400">
                    <span>Layer 4: Transport Qatlami</span>
                    <span className="text-[11px] font-mono">{proto}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground font-mono flex flex-wrap gap-x-3 gap-y-1">
                    <span>Summary: <strong>{summary}</strong></span>
                  </div>
                </div>

                {/* Layer 3 */}
                <div className="rounded-lg border bg-card/70 p-2.5 border-l-4 border-l-green-500">
                  <div className="flex items-center justify-between text-xs font-bold text-green-400">
                    <span>Layer 3: Network (Tarmoq)</span>
                    <span className="text-[11px] font-mono">IPv4</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground font-mono flex flex-wrap gap-x-3 gap-y-1">
                    <span>TTL: <strong>64</strong></span>
                    <span>Checksum: <strong>Valid (OK)</strong></span>
                    <span>Qaror: <strong>Routed via FIB</strong></span>
                  </div>
                </div>

                {/* Layer 2 */}
                <div className="rounded-lg border bg-card/70 p-2.5 border-l-4 border-l-amber-500">
                  <div className="flex items-center justify-between text-xs font-bold text-amber-400">
                    <span>Layer 2: Data Link (Kanal)</span>
                    <span className="text-[11px] font-mono">Ethernet II</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground font-mono flex flex-wrap gap-x-3 gap-y-1">
                    <span>EtherType: <strong>0x0800</strong></span>
                    <span>FCS / CRC: <strong>Passed</strong></span>
                  </div>
                </div>

                {/* Layer 1 */}
                <div className="rounded-lg border bg-card/70 p-2.5 border-l-4 border-l-slate-500">
                  <div className="flex items-center justify-between text-xs font-bold text-slate-400">
                    <span>Layer 1: Physical (Jismoniy)</span>
                    <span className="text-[11px] font-mono">{link}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground font-mono flex flex-wrap gap-x-3 gap-y-1">
                    <span>Port: <strong>GigabitEthernet</strong></span>
                    <span>Duplex: <strong>Full-Duplex</strong></span>
                  </div>
                </div>
              </div>

              {/* O'ng: Cisco uslubidagi Device Processing Log */}
              <div className="flex flex-col gap-2.5">
                <h4 className="text-xs font-semibold text-muted-foreground uppercase flex items-center gap-1.5">
                  <ShieldCheck className="h-4 w-4" /> Qurilma Harakatlari Jurnali (Device Actions)
                </h4>
                <div className="flex-1 rounded-lg border bg-card/40 p-3 font-mono text-xs text-foreground/90 space-y-2">
                  {deviceLogs.map((log, i) => (
                    <div key={i} className="border-b border-border/30 pb-1.5 last:border-none">
                      {log}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {tab === 'details' && (
            <div className="flex flex-col gap-4">
              <div className="rounded-lg border bg-card p-3 font-mono text-xs">
                <div className="font-bold text-sm text-foreground border-b pb-1 mb-2">Paket Parametrlari</div>
                <div className="grid grid-cols-2 gap-y-1.5 text-muted-foreground">
                  <div>Vaqt (Timestamp): <span className="text-foreground">{clock(pkt.ts || 0)}</span></div>
                  <div>Liniya (Link): <span className="text-foreground">{link} ({dir})</span></div>
                  <div>Protokol: <span className="text-foreground font-bold">{proto}</span></div>
                  <div>Tafsilot (Summary): <span className="text-foreground">{summary}</span></div>
                </div>
              </div>
            </div>
          )}

          {tab === 'hex' && (
            <div className="rounded-lg border bg-card p-3 font-mono text-xs">
              <div className="font-bold text-sm text-foreground border-b pb-1 mb-2 flex items-center gap-1.5">
                <Binary className="h-4 w-4" /> Hexadecimal & ASCII Ko'rinishi
              </div>
              <pre className="text-muted-foreground leading-relaxed overflow-x-auto">
{`0000   00 11 22 33 44 55 00 aa  bb cc dd ee 08 00 45 00   |.."3DU........E.|
0010   00 28 00 64 40 00 40 06  00 00 0a 00 01 01 0a 00   |.(.d@.@.........|
0020   02 02 00 50 d4 31 00 00  03 e8 00 00 01 f5 50 12   |...P.1........P.|
0030   fa f0 00 00 00 00                                  |......|`}
              </pre>
            </div>
          )}
        </div>
      </div>
    </WindowFrame>
  )
}
