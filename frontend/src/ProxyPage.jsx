import React, {useState} from 'react'
import {Activity, Cable, FileUp, Play, RefreshCw, Square} from 'lucide-react'
import {api, useResource} from './api'
import {Badge, Button, CardTitle, Empty, ErrorNotice, Loading, Modal, PageTitle} from './ui'

function ImportProfile({close, saved}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function load(event) {
    const file = event.target.files[0]
    if (!file) return
    if (file.size > 2_000_000) return setError('请选择小于 2 MB 的配置文件')
    try {setText(await file.text()); setError('')}
    catch {setError('无法读取配置文件')}
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api('/mihomo/profile', {method: 'POST', body: {text}})
      saved()
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }

  return <Modal title="导入 Mihomo 配置" onClose={close}>
    <form className="form" onSubmit={submit}>
      <label>配置文件<input type="file" accept=".yaml,.yml,.json" onChange={load} disabled={busy}/></label>
      <label>配置内容<textarea placeholder="粘贴 Clash / Mihomo YAML" rows={8} value={text} onChange={e => setText(e.target.value)} disabled={busy} required/></label>
      <p>保存后使用这份配置。运行中的 Mihomo 会重新加载配置。</p>
      <ErrorNotice>{error}</ErrorNotice>
      <div className="form-actions"><Button onClick={close} disabled={busy}>取消</Button><button className="button primary" disabled={busy || !text.trim()}>{busy ? '正在校验…' : '保存配置'}</button></div>
    </form>
  </Modal>
}

export default function ProxyPage() {
  const {data, error, reload} = useResource('/mihomo', 8000)
  const [importing, setImporting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [busy, setBusy] = useState('')
  const [problem, setProblem] = useState('')
  const [checks, setChecks] = useState({})
  const running = data?.state === 'running'

  async function operate(path, method = 'POST', body) {
    setBusy(path)
    setProblem('')
    try {
      const result = await api('/mihomo' + path, {method, body})
      if (path.startsWith('/check/')) setChecks(values => ({...values, [path.split('/').at(-1)]: result}))
      else {
        setChecks({})
        setStopping(false)
        reload()
      }
    } catch (e) {setProblem(e.message)}
    finally {setBusy('')}
  }

  return <div className="page">
    <PageTitle title="网络代理">
      <Button icon={RefreshCw} onClick={reload}>刷新</Button>
      <Button icon={FileUp} variant="primary" disabled={!data?.available || !!busy} onClick={() => setImporting(true)}>导入配置</Button>
    </PageTitle>
    <ErrorNotice>{error || (!stopping && problem)}</ErrorNotice>
    {!data && !error && <Loading/>}
    {data && <section className="card">
      <CardTitle icon={Cable} title="Mihomo"><Badge tone={running ? 'good' : data.state === 'error' ? 'bad' : 'neutral'}>
        {running ? '运行中' : data.state === 'error' ? '启动失败' : data.available ? '已停止' : '未安装'}
      </Badge></CardTitle>
      {data.available ? <div className="entry proxy-details">
        <div className="entry-text"><strong className="mono">127.0.0.1:{data.port}</strong><small>
          {running && [data.version, {rule:'规则模式',global:'全局模式',direct:'直连模式'}[data.mode]].filter(Boolean).join(' · ')}
          {!running && (data.configured ? '已有配置' : '等待导入代理配置')}
        </small></div>
        {running ? <Button icon={Square} variant="danger" onClick={() => {setProblem(''); setStopping(true)}}>停止</Button> :
          <Button icon={Play} variant="primary" disabled={!!busy} onClick={() => operate('/start')}>{busy === '/start' ? '正在启动…' : '启动'}</Button>}
      </div> : <Empty icon={Cable} title="主机未找到 Mihomo">
        <p>将官方 <code>mihomo</code> 可执行文件放入服务的 PATH 后刷新页面。</p>
        <a className="text-link" href="https://github.com/MetaCubeX/mihomo/releases" target="_blank" rel="noreferrer">Mihomo 发行版</a>
      </Empty>}
    </section>}
    {running && <>
      <section className="card">
        <CardTitle icon={Cable} title="代理组"/>
        {data.groups.length ? data.groups.map(group => <div className="settings-row" key={group.name}>
          <strong>{group.name}</strong>
          <select aria-label={group.name + ' 当前节点'} value={group.current || ''} disabled={!!busy}
            onChange={e => operate('/selection', 'PUT', {group: group.name, node: e.target.value})}>
            {group.nodes.map(node => <option key={node} value={node}>{node}</option>)}
          </select>
        </div>) : <p className="section-note">当前配置没有手动选择的代理组。</p>}
      </section>
      <section className="card">
        <CardTitle icon={Activity} title="出口连通性"/>
        {[['openai', 'OpenAI API'], ['deepseek', 'DeepSeek API']].map(([key, name]) => <div className="entry" key={key}>
          <div className="entry-text"><strong>{name}</strong></div>
          {checks[key] && <Badge tone={checks[key].ok ? 'good' : 'bad'}>{checks[key].status ? 'HTTP ' + checks[key].status : '连接失败'}</Badge>}
          <Button disabled={!!busy} onClick={() => operate('/check/' + key)}>{busy === '/check/' + key ? '检测中…' : '检测'}</Button>
        </div>)}
      </section>
    </>}
    {importing && <ImportProfile close={() => setImporting(false)} saved={() => {setImporting(false); setChecks({}); reload()}}/>}
    {stopping && <Modal title="停止 Mihomo" onClose={() => setStopping(false)}>
      <p>正在通过 Mihomo 的连接会断开。</p>
      <ErrorNotice>{problem}</ErrorNotice>
      <div className="form-actions"><Button onClick={() => setStopping(false)}>取消</Button>
        <Button variant="danger solid" disabled={!!busy} onClick={() => operate('/stop')}>停止</Button></div>
    </Modal>}
  </div>
}
