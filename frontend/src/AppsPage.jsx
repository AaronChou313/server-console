import React, {useState} from 'react'
import {ArrowUpRight, Globe, LayoutGrid, RefreshCw} from 'lucide-react'
import {api, useResource} from './api'
import {Badge, Button, CardTitle, Empty, ErrorNotice, Loading, Modal, PageTitle} from './ui'

function Tile({app, openLocal}) {
  const contents = <>
    <span className="app-tile-icon" aria-hidden="true"><Globe size={18}/></span>
    <span className="app-tile-text">
      <strong title={app.name}>{app.name}</strong>
      <small className="mono" title={app.local}>{app.local}</small>
    </span>
    <span className="app-tile-side">
      <Badge tone={app.proxied ? 'neutral' : 'good'}>{app.local_only ? '主机本机' : app.proxied ? '临时转发' : '可直连'}</Badge>
      <ArrowUpRight size={15} aria-hidden="true"/>
    </span>
  </>
  if (app.local_only) {
    return <button type="button" className="app-tile" onClick={() => openLocal(app)}>{contents}</button>
  }
  return <a className="app-tile" href={app.url} target="_blank" rel="noreferrer">{contents}</a>
}

export default function AppsPage() {
  const {data, error, reload} = useResource('/apps', 30000)
  const apps = data?.apps || []
  const [localApp, setLocalApp] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState('')

  async function rescan() {
    setScanning(true)
    setScanError('')
    try {
      await api('/apps?refresh=true')
      reload()
    } catch (error) {setScanError(error.message)}
    finally {setScanning(false)}
  }

  return <div className="page">
    <PageTitle title="我的应用">
      {data && <Badge>{apps.length} 个服务</Badge>}
      <Button icon={RefreshCw} disabled={scanning} onClick={rescan}>{scanning ? '正在扫描…' : '重新扫描'}</Button>
    </PageTitle>
    <ErrorNotice>{scanError || error}</ErrorNotice>
    <section className="card">
      <CardTitle icon={LayoutGrid} title="本机 Web 服务"/>
      {!data && !error && <Loading>正在扫描本机 Web 服务…</Loading>}
      {data && apps.length === 0 && <Empty icon={Globe} title="没有检测到其他 Web 服务">
        <p>Server Console 自身不会列出。本机有服务监听端口并返回 HTML 页面时会自动出现，也可以点击「重新扫描」。</p>
      </Empty>}
      {apps.length > 0 && <div className="apps-grid">{apps.map(app => <Tile key={app.port} app={app} openLocal={setLocalApp}/>)}</div>}
      {apps.length > 0 && <p className="section-note">临时转发只绑定控制台的监听地址，10 分钟无活动后关闭。通过 SSH 隧道访问时，请为「主机本机」应用单独转发页面显示的端口。</p>}
    </section>
    {localApp && <Modal title={localApp.name} onClose={() => setLocalApp(null)}>
      <p>如果浏览器运行在服务器上，请直接打开。通过 SSH 访问时，请在自己的电脑运行下面的命令，再打开本机地址。</p>
      <pre className="output">ssh -N -L {localApp.port}:{localApp.upstream || localApp.local} 用户名@服务器IP</pre>
      <div className="form-actions"><a className="button primary" href={'http://127.0.0.1:' + localApp.port + '/'} target="_blank" rel="noreferrer">打开本机地址</a></div>
    </Modal>}
  </div>
}
