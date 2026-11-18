import React from 'react'
import {ArrowUpRight, Globe, LayoutGrid, RefreshCw} from 'lucide-react'
import {useResource} from './api'
import {Badge, Button, CardTitle, Empty, ErrorNotice, Loading, PageTitle} from './ui'

function hue(value) {
  let result = 0
  for (const character of value) result = (result * 31 + character.codePointAt(0)) % 360
  return result
}

function Tile({app}) {
  return <a className="app-tile" href={app.url} target="_blank" rel="noreferrer" style={{'--hue': hue(app.name)}}>
    <span className="app-tile-icon" aria-hidden="true">{Array.from(app.name)[0]}</span>
    <span className="app-tile-text">
      <strong>{app.name}</strong>
      <small className="mono">{app.process && app.process !== app.name ? app.process + ' · ' : ''}{app.local}</small>
    </span>
    <span className="app-tile-side">
      <Badge tone={app.proxied ? 'neutral' : 'good'}>{app.proxied ? '经转发' : '可直连'}</Badge>
      <ArrowUpRight size={15}/>
    </span>
  </a>
}

export default function AppsPage() {
  const {data, error, reload} = useResource('/apps', 30000)
  const apps = data?.apps || []
  const stale = Boolean(error && /not found|404/i.test(error))
  return <div className="page">
    <PageTitle title="我的应用">
      {data && <Badge>{apps.length} 个服务</Badge>}
      <Button icon={RefreshCw} onClick={reload}>重新扫描</Button>
    </PageTitle>
    <ErrorNotice>{error && !stale ? error : ''}</ErrorNotice>
    {stale && <div className="notice warning">控制台服务尚未加载「我的应用」接口，重启 Server Console 后即可使用。</div>}
    <section className="card">
      <CardTitle icon={LayoutGrid} title="本机 Web 服务"/>
      {!data && !error && <Loading>正在扫描本机 Web 服务…</Loading>}
      {data && apps.length === 0 && <Empty icon={Globe} title="没有检测到 Web 服务">
        <p>服务监听 TCP 端口并返回 HTML 页面时才会出现在这里。启动服务后点击「重新扫描」。</p>
      </Empty>}
      {apps.length > 0 && <div className="apps-grid">{apps.map(app => <Tile key={app.port} app={app}/>)}</div>}
      {apps.length > 0 && <p className="section-note">列表每 30 秒自动刷新。回环地址上的服务经控制台临时转发：端口 10 分钟无活动自动关闭，且没有额外登录校验，请仅在同一 Tailnet 或可信局域网内使用。</p>}
    </section>
  </div>
}
