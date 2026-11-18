import React, {useEffect, useRef, useState} from 'react'
import {Activity, Cable, ChevronDown, Download, FileUp, Play, Plus, RefreshCw, Square} from 'lucide-react'
import {api, bytes, useResource} from './api'
import {Badge, Button, CardTitle, Empty, ErrorNotice, Loading, Modal, PageTitle} from './ui'

const modes = {rule: '规则', global: '全局', direct: '直连'}
const selectable = new Set(['Selector', 'URLTest', 'Fallback'])
const sections = {profiles: '配置', nodes: '策略与节点', rules: '路由规则', connections: '连接', logs: '日志'}
const profilePath = id => '/profiles/' + encodeURIComponent(id)
const date = time => time ? new Date(time * 1000).toLocaleString() : ''

function ProfileEditor({item, close, saved}) {
  const [name, setName] = useState(item?.name || '')
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [kind, setKind] = useState(item ? 'yaml' : 'url')
  const [ready, setReady] = useState(!item)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!item) return
    const controller = new AbortController()
    api('/mihomo' + profilePath(item.id), {signal: controller.signal}).then(row => {
      setText(row.text)
      setReady(true)
    }).catch(e => {if (!controller.signal.aborted) setError(e.message)})
    return () => controller.abort()
  }, [item])

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
    const body = {name, ...(kind === 'url' ? {url: url.trim()} : {text})}
    try {
      await api('/mihomo' + (item ? profilePath(item.id) : '/profiles'), {method: item ? 'PUT' : 'POST', body})
      saved()
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }

  return <Modal title={item ? '编辑 Mihomo 配置' : '导入 Mihomo 配置'} onClose={() => {if (!busy) close()}}>
    <form className="form" onSubmit={submit}>
      <label>配置名称<input value={name} onChange={e => setName(e.target.value)} maxLength={120} required autoFocus disabled={busy}/></label>
      <div className="segments import-method" role="group" aria-label="配置来源">
        <button type="button" aria-pressed={kind === 'url'} className={kind === 'url' ? 'selected' : ''}
          onClick={() => setKind('url')} disabled={busy}>{item?.subscription ? '替换订阅' : '订阅链接'}</button>
        <button type="button" aria-pressed={kind === 'yaml'} className={kind === 'yaml' ? 'selected' : ''}
          onClick={() => setKind('yaml')} disabled={busy}>配置文件或文本</button>
      </div>
      {kind === 'url' ? <>
        <label>{item?.subscription ? '新订阅地址' : '订阅地址'}
          <input type="password" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://…" value={url}
            required onChange={e => setUrl(e.target.value)} disabled={busy}/>
        </label>
        <p className="form-note">优先使用服务商提供的 Clash / Mihomo 订阅链接。服务器无法下载时，可以换用配置文件或粘贴文本。</p>
      </> : <>
        <label>{item ? '替换配置文件' : '配置文件'}<input type="file" accept=".yaml,.yml,.json,.txt" onChange={load} disabled={busy}/></label>
        <label>配置内容<textarea placeholder="粘贴 Clash / Mihomo YAML 或节点链接" rows={9} value={text}
          onChange={e => setText(e.target.value)} disabled={!ready || busy} required/></label>
      </>}
      {item?.active && <p className="form-note">保存前会校验新配置。配置内容变化后会重新加载代理。</p>}
      <ErrorNotice>{error}</ErrorNotice>
      <div className="form-actions"><Button onClick={close} disabled={busy}>取消</Button>
        <button className="button primary" disabled={!ready || busy}>{busy ? (kind === 'url' ? '正在下载和校验…' : '正在校验…') : item ? '保存修改' : '导入'}</button></div>
    </form>
  </Modal>
}

function Profiles({items, running, busy, add, edit, operate, confirm}) {
  const [search, setSearch] = useState('')
  const rows = items.filter(item => item.name.toLowerCase().includes(search.toLowerCase().trim()))
  return <section className="card">
    <CardTitle title="配置库"><Button icon={Plus} variant="primary" disabled={!!busy} onClick={add}>导入配置</Button></CardTitle>
    {items.length > 1 && <div className="proxy-filter"><input aria-label="搜索配置" placeholder="搜索配置" value={search} onChange={e => setSearch(e.target.value)}/></div>}
    {rows.map(item => <div className="entry proxy-entry" key={item.id}>
      <div className="entry-text"><strong>{item.name}</strong><small>{item.subscription ? '订阅' : '本地配置'} · {date(item.updated)}</small>
        {item.usage?.total > 0 && <small>已用 {bytes((item.usage.upload || 0) + (item.usage.download || 0))} / {bytes(item.usage.total)}
          {item.usage.expire > 0 && ' · 到期 ' + date(item.usage.expire)}</small>}</div>
      {item.active && <Badge tone={running ? 'good' : 'neutral'}>{running ? '正在使用' : '已选中'}</Badge>}
      <div className="row-actions">
        <Button variant="primary" disabled={item.active || !!busy} onClick={() => operate(profilePath(item.id) + '/activate')}>{busy === profilePath(item.id) + '/activate' ? '正在应用…' : '使用配置'}</Button>
        {item.subscription && <Button disabled={!!busy} onClick={() => operate(profilePath(item.id) + '/refresh')}>{busy === profilePath(item.id) + '/refresh' ? '正在更新…' : '更新订阅'}</Button>}
        <Button disabled={!!busy} onClick={() => edit(item)}>编辑</Button>
        <a className="button icon-button" aria-label={'导出 ' + item.name} title="导出 YAML" href={'/api/mihomo' + profilePath(item.id) + '/download'} download><Download size={16}/></a>
        <Button variant="danger" disabled={!!busy || (item.active && running)}
          title={item.active && running ? '请先切换配置或停止 Mihomo' : undefined}
          onClick={() => confirm('删除配置', '从配置库删除「' + item.name + '」？', profilePath(item.id), 'DELETE')}>删除</Button>
      </div>
    </div>)}
    {!rows.length && <Empty title={items.length ? '没有匹配的配置' : '暂无代理配置'}>
      {!items.length && <p>导入 Clash / Mihomo YAML 或订阅链接，然后选择使用配置。</p>}
    </Empty>}
  </section>
}

function Nodes({data, busy, operate}) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState(false)
  const [latencies, setLatencies] = useState({})
  const [progress, setProgress] = useState(null)
  const [checks, setChecks] = useState({})
  const job = useRef(null)
  const providers = useResource('/mihomo/providers')
  useEffect(() => () => job.current?.abort(), [])
  const query = search.toLowerCase().trim()
  const builtins = new Set(['COMPATIBLE', 'DIRECT', 'PASS', 'REJECT', 'REJECT-DROP'])
  const all = Object.keys(data.nodes || {}).filter(node => !builtins.has(node))
  const groups = data.groups.filter(g => !query || [g.name, ...g.nodes].some(n => n.toLowerCase().includes(query)))

  function members(g) {
    const seen = new Set()
    function walk(list) {
      return list.flatMap(name => {
        if (seen.has(name)) return []
        seen.add(name)
        const group = data.groups.find(row => row.name === name)
        return group ? walk(group.nodes) : [name]
      })
    }
    return walk(g.nodes)
  }

  async function test(list) {
    if (!list.length || job.current) return
    const controller = new AbortController()
    job.current = controller
    setProgress({done: 0, total: list.length})
    let index = 0, done = 0
    async function consume() {
      while (!controller.signal.aborted && index < list.length) {
        const name = list[index++]
        let delay = null
        try {
          const result = await api('/mihomo/delay', {method: 'POST', body: {name}, signal: controller.signal})
          delay = result.delay
        } catch {}
        if (!controller.signal.aborted) {
          setLatencies(values => ({...values, [name]: delay}))
          setProgress({done: ++done, total: list.length})
        }
      }
    }
    await Promise.all(Array.from({length: Math.min(4, list.length)}, consume))
    if (!controller.signal.aborted) {
      job.current = null
      setProgress({done, total: list.length})
    }
  }

  function cancel() {
    job.current?.abort()
    job.current = null
    setProgress(null)
  }

  function value(node) {
    if (Object.hasOwn(latencies, node)) return latencies[node]
    const info = data.nodes?.[node]
    const last = info?.history[0]
    if (info?.alive === false || (last && last.delay <= 0)) return null
    return last?.delay
  }

  async function check(target) {
    const result = await operate('/check/' + target)
    if (result) setChecks(previous => ({...previous, [target]: result}))
  }

  return <>
    <section className="card">
      <CardTitle icon={Cable} title="策略与节点">
        <div className="segments" aria-label="代理模式">{Object.entries(modes).map(([mode, label]) =>
          <button key={mode} className={mode === data.mode ? 'selected' : ''} aria-pressed={mode === data.mode}
            disabled={!!busy} onClick={() => operate('/mode', 'PUT', {mode})}>{label}</button>)}</div>
      </CardTitle>
      <div className="proxy-filter proxy-toolbar">
        <input aria-label="搜索节点" placeholder="搜索代理组或节点" value={search} onChange={e => setSearch(e.target.value)}/>
        <label className="proxy-check"><input type="checkbox" checked={sort} onChange={e => setSort(e.target.checked)}/> 按延迟排序</label>
        <Button icon={Activity} disabled={!!job.current || !all.length} onClick={() => test(all)}>全部测速</Button>
        {job.current && <Button onClick={cancel}>取消测速</Button>}
        {progress && <span role="status">{progress.done}/{progress.total}</span>}
      </div>
      {groups.map((group, index) => {
        const nodes = group.nodes.filter(node => !query || group.name.toLowerCase().includes(query) || node.toLowerCase().includes(query))
        if (sort) nodes.sort((a, b) => (value(a) ?? Infinity) - (value(b) ?? Infinity))
        return <details className="proxy-group" key={group.name} open={index === 0 || undefined}>
          <summary><ChevronDown size={15}/><strong>{group.name}</strong><span>{group.current || group.type}</span></summary>
          <div className="proxy-group-body">
            <div className="row-actions proxy-group-actions">
              {group.fixed && group.type !== 'Selector' && <Button disabled={!!busy} onClick={() => operate('/selection', 'PUT', {group: group.name, node: null})}>恢复自动</Button>}
              <Button icon={Activity} disabled={!!job.current || !group.nodes.length} onClick={() => test(members(group))}>本组测速</Button>
            </div>
            <div className="proxy-nodes">{nodes.map(node => {
              const latency = value(node)
              const chosen = group.current === node
              const latencyTone = latency === undefined ? '' : latency === null ? 'latency-bad'
                : latency < 500 ? 'latency-good' : 'latency-warning'
              return <div className={'proxy-node' + (chosen ? ' chosen' : '')} key={node}>
                <button className="proxy-node-label" title={node} aria-pressed={chosen}
                  disabled={!selectable.has(group.type) || !!busy || (chosen && (group.type === 'Selector' || group.fixed === node))}
                  onClick={() => operate('/selection', 'PUT', {group: group.name, node})}>
                  <strong>{node}</strong><small>{data.nodes?.[node]?.type || '代理组'}</small>
                </button>
                <Button className={'proxy-latency ' + latencyTone} title={'测速 ' + node} aria-label={'测速 ' + node} disabled={!!job.current}
                  onClick={() => test([node])}>{latency === undefined ? '测速' : latency === null ? '不可达' : latency + ' ms'}</Button>
              </div>
            })}</div>
          </div>
        </details>
      })}
      {!groups.length && <Empty title={query ? '没有匹配的节点' : '当前配置没有代理组'}/>}
    </section>
    {providers.data?.filter(item => item.type !== 'Compatible').length > 0 && <section className="card">
      <CardTitle title="节点提供者"/>
      {providers.data.filter(item => item.type !== 'Compatible').map(item => <div className="entry" key={item.name}>
        <div className="entry-text"><strong>{item.name}</strong><small>{item.nodes} 个节点</small></div>
        <div className="row-actions">
          <Button disabled={!!busy} onClick={async () => {
            if (await operate('/providers/check', 'POST', {name: item.name})) providers.reload()
          }}>健康检查</Button>
          <Button disabled={!!busy || item.type !== 'HTTP'} onClick={async () => {
            if (await operate('/providers', 'PUT', {name: item.name})) providers.reload()
          }}>更新</Button>
        </div>
      </div>)}
    </section>}
    <section className="card">
      <CardTitle icon={Activity} title="出口连通性"/>
      {[['openai', 'OpenAI API'], ['deepseek', 'DeepSeek API']].map(([key, label]) => <div className="entry" key={key}>
        <div className="entry-text"><strong>{label}</strong></div>
        {checks[key] && <Badge tone={checks[key].ok ? 'good' : 'bad'}>{checks[key].status ? 'HTTP ' + checks[key].status : '连接失败'}</Badge>}
        <Button disabled={!!busy} onClick={() => check(key)}>检测</Button>
      </div>)}
    </section>
  </>
}

function Inspection({section, confirm}) {
  const resource = useResource('/mihomo/' + section, section === 'connections' ? 5000 : 0)
  const [query, setQuery] = useState('')
  const data = resource.data
  const title = sections[section]
  const matches = row => JSON.stringify(row).toLowerCase().includes(query.toLowerCase().trim())
  return <section className="card">
    <CardTitle title={title}>
      <div className="actions">
        {section === 'connections' && data?.total > 0 && <Button variant="danger" onClick={() => confirm('断开全部连接', '正在通过 Mihomo 的连接会断开。', '/connections', 'DELETE', resource.reload)}>断开全部</Button>}
        <Button icon={RefreshCw} onClick={resource.reload}>刷新</Button>
      </div>
    </CardTitle>
    <ErrorNotice>{resource.error}</ErrorNotice>
    {!data && !resource.error && <Loading/>}
    {data && (section === 'logs' ? <div className="proxy-log"><pre className="output">{data.text || '暂无日志。'}</pre></div> : <>
      <div className="proxy-filter proxy-toolbar">
        <input aria-label={'搜索' + title} placeholder={'搜索' + title} value={query} onChange={e => setQuery(e.target.value)}/>
        <span>{data.total} 条{data.items.length < data.total && ' · 显示前 ' + data.items.length}{section === 'connections' && ' · ↑ ' + bytes(data.upload) + ' · ↓ ' + bytes(data.download)}</span>
      </div>
      <div className="table-scroll"><table>
        <thead><tr>{(section === 'rules' ? ['类型', '目标', '策略'] : ['目标', '路由', '上传 / 下载', '操作']).map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{data.items.filter(matches).map((row, index) => section === 'rules' ?
          <tr key={index}><td>{row.type}</td><td>{row.payload || '—'}</td><td>{row.proxy}</td></tr> :
          <tr key={row.id}><td>{row.metadata?.host || row.metadata?.destinationIP || '—'}:{row.metadata?.destinationPort}</td>
            <td>{row.chains?.slice().reverse().join(' → ')}</td><td>{bytes(row.upload)} / {bytes(row.download)}</td>
            <td><Button variant="danger" onClick={() => confirm('断开连接', '此连接正在传输的数据会中断。', '/connections/' + row.id, 'DELETE', resource.reload)}>断开</Button></td></tr>)}
        </tbody>
      </table></div>
      {!data.items.some(matches) && <Empty compact title={'没有匹配的' + title}/>}
    </>)}
  </section>
}

export default function ProxyPage() {
  const {data, error, reload} = useResource('/mihomo', 8000)
  const profiles = useResource('/mihomo/profiles')
  const [section, setSection] = useState('profiles')
  const [editing, setEditing] = useState(null)
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState('')
  const [problem, setProblem] = useState('')
  const [message, setMessage] = useState('')
  const running = data?.state === 'running'
  const independent = data?.owner === 'service'
  const engineReady = running || data?.engine_ready
  const migration = data?.state === 'migration'
  const active = profiles.data?.items.find(item => item.active)

  function refresh() {
    reload()
    profiles.reload()
  }

  async function operate(path, method = 'POST', body) {
    setBusy(path)
    setProblem('')
    setMessage('')
    try {
      const result = await api('/mihomo' + path, {method, body})
      if (path.endsWith('/refresh')) setMessage('订阅已更新')
      if (path.endsWith('/activate')) setMessage(running ? '配置已切换' : '配置已选中，启动 Mihomo 后生效')
      if (path === '/start') setMessage('Mihomo 已启动')
      if (path === '/engine') setMessage(result.updated ? '已安装 Mihomo ' + result.version : 'Mihomo 已是最新版本 ' + result.version)
      if (!path.startsWith('/check/') && !path.startsWith('/connections')) refresh()
      return result
    } catch (e) {setProblem(e.message)}
    finally {setBusy('')}
  }

  function confirm(title, description, path, method, done) {
    setProblem('')
    setPending({title, description, path, method, done})
  }

  return <div className="page">
    <PageTitle title="网络代理"><Button icon={RefreshCw} onClick={refresh}>刷新</Button></PageTitle>
    <ErrorNotice>{error || profiles.error || data?.error || (!pending && problem)}</ErrorNotice>
    {message && <div className="notice success" role="status">{message}</div>}
    {!data && !error && <Loading/>}
    {data && <section className="card">
      <CardTitle icon={Cable} title="Mihomo"><Badge tone={running ? 'good' : data.state === 'error' ? 'bad' : 'neutral'}>
        {running ? '运行中' : migration ? '待迁移' : data.state === 'error' ? '服务异常' : data.available ? '已停止' : '未安装'}
      </Badge></CardTitle>
      {data.available ? <div className="entry">
        <div className="entry-text"><strong className="mono">{data.port ? '127.0.0.1:' + data.port : '本机 Mihomo'}</strong>
          <small>{[independent && '独立服务', data.pid && 'PID ' + data.pid, active?.name, data.version, modes[data.mode]].filter(Boolean).join(' · ') || '本机 HTTP / SOCKS5'}</small></div>
        <div className="row-actions">
          {!independent && <Button icon={Download} disabled={!!busy} onClick={() => confirm('检查 Mihomo 更新', '若有新版本，将下载安装内核。运行中的代理会短暂重启。', '/engine')}>检查更新</Button>}
          {running ? <>
            <Button icon={RefreshCw} disabled={!!busy} onClick={() => confirm('重启 Mihomo', '代理连接会中断并重新建立。', '/restart')}>重启</Button>
            <Button icon={Square} variant="danger" disabled={!!busy} onClick={() => confirm(data.system_tun ? '停用系统代理' : '停止 Mihomo', '正在使用代理的连接会断开。', '/stop')}>{data.system_tun ? '停用系统代理' : '停止'}</Button>
          </> : <Button icon={Play} variant="primary" disabled={!!busy || (independent && !data.service_online)}
            onClick={() => operate('/start')}>{data.system_tun ? '启用系统代理' : '启动'}</Button>}
        </div>
      </div> : migration ? <Empty icon={Cable} title="等待迁移 Mac 上已有的 Mihomo">
        <p>现有代理仍在工作。完成系统服务迁移后，即可在这里管理当前配置。</p>
      </Empty> : <Empty icon={Cable} title="主机未找到 Mihomo">
        <Button icon={Download} variant="primary" disabled={!!busy} onClick={() => operate('/engine')}>{busy === '/engine' ? '正在下载安装…' : '安装 Mihomo'}</Button>
        <a className="text-link" href="https://github.com/MetaCubeX/mihomo/releases" target="_blank" rel="noreferrer">Mihomo 发行版</a>
      </Empty>}
    </section>}
    {data?.available && <>
      <nav className="proxy-tabs" aria-label="Mihomo 功能">{Object.entries(sections).map(([key, label]) =>
        <button key={key} type="button" className={'button' + (section === key ? ' selected' : '')}
          aria-pressed={section === key} onClick={() => setSection(key)}>{label}</button>)}</nav>
      {section === 'profiles' ? <Profiles items={profiles.data?.items || []} running={running} busy={busy}
          add={() => setEditing({})} edit={setEditing} operate={operate} confirm={confirm}/>
        : section === 'logs' || (engineReady && ['rules', 'connections'].includes(section)) ?
          <Inspection key={section} section={section} confirm={confirm}/>
        : engineReady ? <Nodes key={active?.updated || 'default'} data={data} busy={busy} operate={operate}/>
        : <section className="card"><Empty title="启动 Mihomo 后可使用此功能"/></section>}
    </>}
    {editing && <ProfileEditor item={editing.id ? editing : null} close={() => setEditing(null)} saved={() => {
      setMessage(editing.id ? '配置已保存' : '已导入到配置库，点击「使用配置」即可切换')
      setEditing(null)
      refresh()
    }}/>}
    {pending && <Modal title={pending.title} onClose={() => {if (!busy) setPending(null)}}>
      <p>{pending.description}</p><ErrorNotice>{problem}</ErrorNotice>
      <div className="form-actions"><Button onClick={() => setPending(null)} disabled={!!busy}>取消</Button>
        <Button variant="danger solid" disabled={!!busy} onClick={async () => {
          if (await operate(pending.path, pending.method)) {pending.done?.(); setPending(null)}
        }}>确认</Button></div>
    </Modal>}
  </div>
}
