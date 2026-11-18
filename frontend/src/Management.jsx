import React, {useState} from 'react'
import {Activity, Box, Cable, ChevronRight, FileText, KeyRound, Network, Pencil, Play, Plus, RefreshCw, Square, SquareTerminal, Trash2} from 'lucide-react'
import {api, bytes, useResource} from './api'
import {Badge, Button, CardTitle, Empty, ErrorNotice, Loading, Modal, PageTitle} from './ui'

export function DockerPage({openTerminal}) {
  const resource = useResource('/docker', 12000)
  const ready = resource.data?.state === 'ready'
  const usage = useResource(ready && resource.data.containers.some(item => item.State === 'running') ? '/docker/stats' : null, 12000)
  const [dialog, setDialog] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [log, setLog] = useState('')
  const state = resource.data?.state
  const stateText = {missing: 'Docker 未安装', denied: '当前用户无权访问 Docker', offline: 'Docker Engine 未连接'}
  const actions = {start: '启动', stop: '停止', restart: '重启'}
  async function operate(item, action) {
    setBusy(true)
    setError('')
    try {
      const result = await api('/docker/' + item.ID + '/' + action, {method: action === 'logs' ? 'GET' : 'POST'})
      if (action === 'terminal') openTerminal(result)
      else if (action === 'logs') setLog(result.output || '最近没有日志')
      else {setDialog(null); resource.reload(); usage.reload()}
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }
  return <div className="page">
    <PageTitle title="Docker" description="查看容器状态、资源占用与日志。">
      {ready && <Badge tone="good">Engine {resource.data.version}</Badge>}
      <Button icon={RefreshCw} onClick={() => {resource.reload(); usage.reload()}}>刷新</Button>
    </PageTitle>
    <ErrorNotice>{resource.error || (!dialog && error)}</ErrorNotice>
    {!state && !resource.error && <Loading/>}
    {state && !ready && <section className="card"><Empty icon={Box} title={stateText[state]}>
      <p>{state === 'missing' ? 'macOS 可安装 Docker Desktop 或 OrbStack。Linux 可安装 Docker Engine。' : state === 'denied' ? '请检查当前用户的 Docker socket 权限与 Docker context。' : '请启动 Docker Engine 或桌面运行环境，并检查当前 Docker context。'}</p>
      {resource.data.detail && <pre className="output">{resource.data.detail}</pre>}
      <a className="text-link" href="https://docs.docker.com/get-started/get-docker/" target="_blank" rel="noreferrer">Docker 安装文档 <ChevronRight size={15}/></a>
    </Empty></section>}
    {ready && <section className="card">
      <CardTitle icon={Box} title={'容器 · ' + resource.data.containers.length}/>
      {usage.error && <div className="section-note">资源采样失败：{usage.error}</div>}
      {!resource.data.containers.length && <Empty icon={Box} title="Docker Engine 中没有容器"/>}
      {resource.data.containers.map(item => {
        const running = item.State === 'running'
        const stats = usage.data?.[item.ID]
        return <div className="entry Docker" key={item.ID}>
          <span className="metric-icon slate"><Box size={17}/></span><div className="entry-text"><strong>{item.Names}</strong><small>{item.Image} · {item.Status}</small></div>
          <div className="resource-text">{stats && <><span>CPU {stats.CPUPerc}</span><span>内存 {stats.MemUsage}</span></>}</div>
          <Badge tone={running ? 'good' : item.State === 'exited' ? 'neutral' : 'warning'}>{running ? '运行中' : item.State}</Badge>
          <div className="row-actions">
            <Button icon={FileText} title="日志" aria-label={'查看日志 ' + item.Names} disabled={busy} onClick={() => {setLog(''); setDialog({item, action: 'logs'}); operate(item, 'logs')}}/>
            <Button icon={SquareTerminal} title="进入容器终端" aria-label={'进入容器 ' + item.Names} disabled={!running || busy} onClick={() => operate(item, 'terminal')}/>
            {running ? <>
              <Button icon={RefreshCw} title="重启" aria-label={'重启 ' + item.Names} onClick={() => {setError(''); setDialog({item, action: 'restart'})}}/>
              <Button icon={Square} variant="danger" title="停止" aria-label={'停止 ' + item.Names} onClick={() => {setError(''); setDialog({item, action: 'stop'})}}/>
            </> : <Button icon={Play} title="启动" aria-label={'启动 ' + item.Names} disabled={busy} onClick={() => operate(item, 'start')}/>}
          </div>
        </div>
      })}
    </section>}
    {dialog && <Modal title={dialog.action === 'logs' ? dialog.item.Names + ' · 容器日志' : actions[dialog.action] + '容器'} onClose={() => setDialog(null)}>
      {dialog.action === 'logs' ? <><pre className="output logs">{busy ? '正在读取…' : log}</pre><Button icon={RefreshCw} disabled={busy} onClick={() => operate(dialog.item, 'logs')}>刷新日志</Button></> :
        <><p>确认{actions[dialog.action]}「{dialog.item.Names}」？</p><div className="form-actions"><Button onClick={() => setDialog(null)}>取消</Button><Button variant="danger solid" disabled={busy} onClick={() => operate(dialog.item, dialog.action)}>{actions[dialog.action]}</Button></div></>}
      <ErrorNotice>{error}</ErrorNotice>
    </Modal>}
  </div>
}

export function SSHPage({openTerminal}) {
  const resource = useResource('/ssh')
  const [editing, setEditing] = useState(null)
  const [removing, setRemoving] = useState('')
  const [form, setForm] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  function edit(entry) {
    setError('')
    setEditing(entry || {})
    setForm(entry || {alias: '', hostname: '', user: '', port: ''})
  }
  async function action(mode, item = editing) {
    setBusy(true)
    setError('')
    const alias = item?.alias ? '/' + encodeURIComponent(item.alias) : ''
    try {
      if (mode === 'connect') openTerminal(await api('/ssh' + alias + '/connect', {method: 'POST'}))
      else if (mode === 'remove') await api('/ssh' + alias, {method: 'DELETE'})
      else await api('/ssh' + alias, {method: alias ? 'PUT' : 'POST', body: {...form, hostname: form.hostname || form.alias, port: form.port ? Number(form.port) : null}})
      setEditing(null)
      setRemoving('')
      resource.reload()
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }
  return <div className="page">
    <PageTitle title="SSH 主机" description="使用系统 OpenSSH、现有密钥和 ~/.ssh/config。">
      <Button icon={RefreshCw} aria-label="刷新 SSH 配置" onClick={resource.reload}/>
      <Button icon={Plus} variant="primary" onClick={() => edit()}>添加主机</Button>
    </PageTitle>
    <ErrorNotice>{resource.error || (!editing && !removing && error)}</ErrorNotice>
    <section className="card">
      <CardTitle icon={KeyRound} title="连接配置"/>
      {!resource.data && !resource.error && <Loading/>}
      {resource.data?.length === 0 && <Empty icon={KeyRound} title="还没有 SSH 主机"><Button icon={Plus} onClick={() => edit()}>添加主机</Button></Empty>}
      {resource.data?.map(item => <div className="entry" key={item.alias}>
        <span className="metric-icon slate"><KeyRound size={17}/></span>
        <div className="entry-text"><button className="entry-link" disabled={busy} onClick={() => action('connect', item)}>{item.alias}</button><small className="mono" title={item.source}>{item.user ? item.user + '@' : ''}{item.hostname || item.alias}{item.port ? ':' + item.port : ''}</small></div>
        {!item.editable && <Badge>由 OpenSSH 配置继承</Badge>}
        <div className="row-actions">
          <Button icon={SquareTerminal} disabled={busy} onClick={() => action('connect', item)}>连接</Button>
          {item.editable && <><Button icon={Pencil} aria-label={'编辑 ' + item.alias} onClick={() => edit(item)}/><Button icon={Trash2} variant="danger" aria-label={'删除 ' + item.alias} onClick={() => {setError(''); setRemoving(item.alias)}}/></>}
        </div>
      </div>)}
    </section>
    {editing && <Modal title={editing.alias ? '编辑 SSH 主机' : '添加 SSH 主机'} onClose={() => setEditing(null)}>
      <form className="form" onSubmit={event => {event.preventDefault(); action('save')}}>
        {[['alias', '别名', 'dev-server'], ['hostname', '主机名或 IP', '留空时使用别名'], ['user', '用户名', '使用 OpenSSH 默认值'], ['port', '端口', '使用 OpenSSH 默认值']].map(([key, label, placeholder]) =>
          <label key={key}>{label}<input value={form[key] || ''} onChange={event => setForm(state => ({...state, [key]: event.target.value}))}
            placeholder={placeholder} required={key === 'alias'} autoFocus={key === 'alias'} type={key === 'port' ? 'number' : 'text'} min={1} max={65535}/></label>)}
        <ErrorNotice>{error}</ErrorNotice><div className="form-actions"><Button onClick={() => setEditing(null)}>取消</Button><button className="button primary" disabled={busy}>保存配置</button></div>
      </form>
    </Modal>}
    {removing && <Modal title="删除 SSH 配置" onClose={() => setRemoving('')}>
      <p>删除「{removing}」在 ~/.ssh/config 中的 Host 条目？</p><ErrorNotice>{error}</ErrorNotice><div className="form-actions">
        <Button onClick={() => setRemoving('')}>取消</Button><Button variant="danger solid" disabled={busy} onClick={() => action('remove', {alias: removing})}>删除配置</Button></div>
    </Modal>}
  </div>
}

export function NetworkPage() {
  const {data, error, reload} = useResource('/network', 20000)
  const [target, setTarget] = useState('')
  const [port, setPort] = useState(443)
  const [mode, setMode] = useState('ping')
  const [result, setResult] = useState(null)
  const [probeError, setProbeError] = useState('')
  const [busy, setBusy] = useState(false)
  const [all, setAll] = useState(false)
  const tailscale = data?.tailscale
  async function probe(event) {
    event.preventDefault()
    setProbeError('')
    setBusy(true)
    setResult(null)
    try {setResult(await api('/network/probe', {method: 'POST', body: {target, port: Number(port), mode}}))}
    catch (e) {setProbeError(e.message)}
    finally {setBusy(false)}
  }
  function prefill(peer) {
    setMode('tailping')
    setTarget(peer.ips?.[0] || peer.dns || peer.name)
    document.getElementById('probe-target')?.scrollIntoView({block: 'center'})
  }
  return <div className="page">
    <PageTitle title="网络状态" description="检查主机接口、Tailnet 设备与连通性。"><Button icon={RefreshCw} onClick={reload}>刷新</Button></PageTitle>
    <ErrorNotice>{error}</ErrorNotice>
    <div className="network-grid">
      <section className="card">
        <CardTitle icon={Network} title="Tailscale">{tailscale && <Badge tone={tailscale.state === 'Running' ? 'good' : 'warning'}>{tailscale.state === 'Running' ? '已连接' : tailscale.state}</Badge>}</CardTitle>
        {!data && !error && <Loading/>}
        {tailscale && <div className="network-summary">
          {tailscale.available ? <><strong>{tailscale.ips[0] || '暂无 Tailnet IP'}</strong><small>{tailscale.dns}{tailscale.ips[1] && ' · ' + tailscale.ips[1]}</small></> :
            <p>未检测到 Tailscale CLI。安装后可在此查看 Tailnet IP 与设备状态。</p>}
          {tailscale.health?.length > 0 && <div className="notice warning">{tailscale.health.join(' · ')}</div>}
        </div>}
        {tailscale?.peers.map(peer => <div className="entry" key={peer.dns || peer.name}>
          <div className="entry-text"><strong>{peer.name}</strong><small className="mono">{peer.ips[0]}{peer.endpoint ? ' · 直连 ' + peer.endpoint : peer.active && peer.relay ? ' · 中继 ' + peer.relay : ''}</small></div>
          <Badge tone={peer.online ? 'good' : 'neutral'}>{peer.online ? '在线' : '离线'}</Badge>
          <Button icon={Activity} title="检测连通性" aria-label={'检测 ' + peer.name} onClick={() => prefill(peer)}/>
        </div>)}
      </section>
      <section className="card">
        <CardTitle icon={Activity} title="连通性检测"/>
        <form className="form probe" onSubmit={probe}>
          <label>方式<select value={mode} onChange={e => setMode(e.target.value)}><option value="ping">系统 Ping</option><option value="tcp">TCP 连接</option><option value="tailping" disabled={!tailscale?.available}>Tailscale Ping</option><option value="netcheck" disabled={!tailscale?.available}>Tailscale Netcheck</option></select></label>
          {mode !== 'netcheck' && <label>目标地址<input id="probe-target" placeholder="主机名或 IP 地址" value={target} required onChange={e => setTarget(e.target.value)}/></label>}
          {mode === 'tcp' && <label>端口<input type="number" min="1" max="65535" required value={port} onChange={e => setPort(e.target.value)}/></label>}
          <button className="button primary" disabled={busy}>{busy ? '检测中…' : '开始检测'}</button>
          <ErrorNotice>{probeError}</ErrorNotice>
          {result && <div><Badge tone={result.ok ? 'good' : 'bad'}>{result.ok ? '检测通过' : '检测失败'}</Badge><pre className="output">{result.output}</pre></div>}
        </form>
      </section>
    </div>
    {data && <>
      <section className="card">
        <CardTitle icon={Cable} title={'网络接口 · ' + data.interfaces.length}/>
        {data.interfaces.map(item => <div className="entry interface-row" key={item.name}>
          <div className="entry-text"><strong className="mono">{item.name}</strong><small className="mono">{item.addresses.join(' · ') || '无 IP 地址'}</small></div>
          <span className="resource-text">↓ {bytes(item.rx)}　↑ {bytes(item.tx)}</span><Badge tone={item.up ? 'good' : 'neutral'}>{item.up ? '启用' : '未启用'}</Badge>
        </div>)}
      </section>
      <section className="card">
        <CardTitle title={'当前连接 · ' + data.connections.length}/>
        {data.note && <p className="section-note">{data.note}</p>}
        <div className="table-scroll"><table><thead><tr><th>协议</th><th>本地地址</th><th>远程地址</th><th>状态</th><th>PID</th></tr></thead>
          <tbody>{data.connections.slice(0, all ? undefined : 15).map((item, i) => <tr key={i}><td>{item.protocol}</td><td>{item.local}</td><td>{item.remote || '—'}</td><td>{item.state || '—'}</td><td>{item.pid || '—'}</td></tr>)}</tbody></table></div>
        {data.connections.length > 15 && <div className="table-more"><Button onClick={() => setAll(!all)}>{all ? '收起连接' : '显示全部连接'}</Button></div>}
      </section>
    </>}
  </div>
}
