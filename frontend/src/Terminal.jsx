import React, {useEffect, useRef, useState} from 'react'
import {ArrowDown, ArrowUp, ClipboardPaste, Copy, ExternalLink, FileUp, History, Keyboard, Maximize, Minimize, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, SquareTerminal, X} from 'lucide-react'
import {Terminal} from '@xterm/xterm'
import {FitAddon} from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import {api, useResource} from './api'
import {Badge, Button, Empty, ErrorNotice, Modal, PageTitle} from './ui'

const encoder = new TextEncoder()
const terminalColors = {
  background: '#10131a', foreground: '#e9edf6', cursor: '#a9baff', selectionBackground: '#3e5689',
  black: '#171c26', red: '#f17983', green: '#8acfaa', yellow: '#f1ca83', blue: '#82abff',
  magenta: '#cda0f0', cyan: '#75d0dd', white: '#e2e8f0', brightBlack: '#7f899e', brightWhite: '#f8fafc',
}

function TerminalView({id, font, family, connectRef, status}) {
  const mount = useRef(null)
  useEffect(() => {
    const el = mount.current
    const term = new Terminal({
      fontFamily: family, fontSize: font, lineHeight: 1.18, theme: terminalColors,
      cursorBlink: true, scrollback: 8000, allowProposedApi: false, macOptionIsMeta: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    let socket, retry, frame, waiting = 400, disposed = false, reconnectable = true
    let received = false
    const send = input => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(encoder.encode(input))
    }
    const size = () => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({cols: term.cols, rows: term.rows}))
    }
    const refreshSize = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {if (el.clientWidth >= 20 && el.clientHeight >= 20) fit.fit()})
    }
    const observer = new ResizeObserver(refreshSize)
    observer.observe(el)
    const resize = term.onResize(size)
    const data = term.onData(send)
    const binary = term.onBinary(input => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(Uint8Array.from(input, char => char.charCodeAt(0)))
    })
    term.attachCustomKeyEventHandler(event => {
      if (event.type !== 'keydown') return true
      const key = event.key.toLowerCase()
      if ((event.metaKey || (event.ctrlKey && event.shiftKey)) && ['c', 'v'].includes(key)) {
        if (key === 'c' && term.hasSelection()) copyText(term.getSelection())
        return false
      }
      return true
    })
    function connect() {
      if (disposed || !reconnectable || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return
      clearTimeout(retry)
      if (el.clientWidth >= 20 && el.clientHeight >= 20) fit.fit()
      status(received ? '正在重连' : '正在连接')
      socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host +
        '/ws/terminal/' + encodeURIComponent(id) + '?cols=' + term.cols + '&rows=' + term.rows)
      socket.binaryType = 'arraybuffer'
      socket.onopen = () => {if (!disposed) size()}
      socket.onmessage = event => {
        if (disposed) return
        if (!received) {
          term.reset()
          received = true
          waiting = 400
          status('已连接')
        }
        term.write(new Uint8Array(event.data))
      }
      socket.onclose = event => {
        if (disposed) return
        if ([4400, 4404].includes(event.code)) {
          reconnectable = false
          status(event.code === 4404 ? '会话已结束' : '连接失败')
          return
        }
        received = false
        status('连接中断 · 正在重试')
        if (navigator.onLine) {
          retry = setTimeout(connect, waiting)
          waiting = Math.max(400, Math.min(waiting * 2, 5000))
        }
        if (event.code === 1006) api('/auth').then(value => {
          if (!value.authenticated && !disposed) window.dispatchEvent(new Event('console-logout'))
        }).catch(() => {})
      }
    }
    connectRef.current = {
      focus: () => term.focus(),
      selection: () => term.getSelection(),
      send: value => {send(value); term.focus()},
      paste: value => {term.paste(value); term.focus()},
      retry: () => {
        reconnectable = true
        waiting = 0
        if (socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) socket.close()
        else connect()
      },
      font: (size, fontFamily) => {
        term.options.fontSize = size
        term.options.fontFamily = fontFamily
        refreshSize()
      },
    }
    connect()
    window.addEventListener('online', connect)
    document.fonts?.ready.then(() => {if (!disposed) refreshSize()})
    return () => {
      disposed = true
      connectRef.current = null
      window.removeEventListener('online', connect)
      observer.disconnect()
      clearTimeout(retry)
      cancelAnimationFrame(frame)
      socket?.close()
      data.dispose()
      binary.dispose()
      resize.dispose()
      term.dispose()
    }
  }, [id, connectRef, status])
  useEffect(() => {connectRef.current?.font(font, family)}, [font, family, connectRef])
  return <div ref={mount} className="terminal-screen" aria-label="交互式终端"/>
}

async function copyText(value) {
  if (!value) return false
  if (navigator.clipboard?.writeText) {
    try {await navigator.clipboard.writeText(value); return true} catch {}
  }
  const input = document.createElement('textarea')
  input.value = value
  input.style.cssText = 'position:fixed;left:0;top:0;opacity:0'
  document.body.appendChild(input)
  input.select()
  const result = document.execCommand('copy')
  input.remove()
  return result
}

function SessionForm({kind, mode, onClose, onCreate}) {
  const projects = useResource('/projects')
  const [cwd, setCwd] = useState(localStorage.getItem('console-project') || '~')
  const [name, setName] = useState('')
  const [resume, setResume] = useState('project')
  const [sessionId, setSessionId] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const title = mode === 'resume' ? '恢复 Codex 历史会话' : kind === 'codex' ? '新建 Codex 会话' : kind === 'dsh' ? '新建 DSH 终端' : '新建终端会话'
  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const item = await api('/terminal/' + kind, {method: 'POST', body: {
        name, cwd, resume: mode === 'resume' ? resume === 'id' ? sessionId.trim() : resume : '',
      }})
      localStorage.setItem('console-project', cwd)
      onCreate(item)
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }
  return <Modal title={title} onClose={onClose}>
    <form className="form" onSubmit={submit}>
      <label>项目目录<input list="projects" value={cwd} required onChange={e => setCwd(e.target.value)} placeholder="/path/to/project" autoFocus/></label>
      <datalist id="projects">{projects.data?.map(path => <option key={path} value={path}/>)}</datalist>
      {projects.data?.length > 0 && <label>常用目录<select value={projects.data.includes(cwd) ? cwd : ''} onChange={e => e.target.value && setCwd(e.target.value)}>
        <option value="">选择主机目录</option>{projects.data.map(path => <option key={path} value={path}>{path}</option>)}
      </select></label>}
      <label>标签名称<input value={name} maxLength={100} onChange={e => setName(e.target.value)} placeholder="可选"/></label>
      {mode === 'resume' && <>
        <label>恢复方式<select value={resume} onChange={e => setResume(e.target.value)}>
          <option value="project">选择当前项目的历史会话</option><option value="picker">查找所有项目的历史会话</option><option value="last">恢复当前项目最近一次</option><option value="id">输入会话 UUID</option>
        </select></label>
        {resume === 'id' && <label>Codex 会话 UUID<input value={sessionId} required onChange={e => setSessionId(e.target.value)}/></label>}
      </>}
      <ErrorNotice>{error}</ErrorNotice>
      <div className="form-actions"><Button onClick={onClose}>取消</Button><button className="button primary" disabled={busy}>{busy ? '正在启动…' : mode === 'resume' ? '打开历史会话' : '创建会话'}</button></div>
    </form>
  </Modal>
}

export default function TerminalPage({kind, font, family, hidden, setHidden, tabs, setTabs, active, select, tools}) {
  const resource = useResource('/terminal', tools?.zellij ? 9000 : 0)
  const [status, setStatus] = useState('正在连接')
  const [dialog, setDialog] = useState('')
  const [end, setEnd] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attachment, setAttachment] = useState('')
  const [pasteBox, setPasteBox] = useState(false)
  const [pasteValue, setPasteValue] = useState('')
  const [full, setFull] = useState(false)
  const [keys, setKeys] = useState(false)
  const [dshFrame, setDshFrame] = useState('')
  const [dshBusy, setDshBusy] = useState(false)
  const [copyNotice, setCopyNotice] = useState('')
  const terminal = useRef(null), file = useRef(null), workspace = useRef(null)
  const attachmentsAllowed = kind === 'codex' || kind === 'shell'
  const title = kind === 'codex' ? 'Codex' : kind === 'dsh' ? 'DeepSeek Harness' : 'Web 终端'
  const matching = (resource.data || []).filter(item => kind === 'shell' || item.kind === kind)
  const tabList = tabs.filter(item => kind === 'shell' || item.kind === kind)
  const current = tabList.find(item => item.id === active) || tabList.at(-1)
  const installed = tools?.[kind === 'shell' ? 'zellij' : kind]
  const canCreate = installed && tools?.zellij
  const focusMode = full || hidden

  useEffect(() => {
    const sync = () => {if (!document.fullscreenElement) setFull(false)}
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  }, [])
  useEffect(() => {
    if (!copyNotice) return
    const timer = setTimeout(() => setCopyNotice(''), 2900)
    return () => clearTimeout(timer)
  }, [copyNotice])

  function open(item) {
    setTabs(list => list.some(tab => tab.id === item.id) ? list : [...list, item])
    select(item.id)
    setDshFrame('')
    setDialog('')
    setError('')
    resource.reload()
  }
  function close(id) {
    const visible = tabList.filter(item => item.id !== id)
    setTabs(list => list.filter(item => item.id !== id))
    if (current?.id === id) select(visible.at(-1)?.id || '')
  }
  async function stop() {
    setBusy(true)
    try {
      await api('/terminal/' + encodeURIComponent(end.id), {method: 'DELETE'})
      close(end.id)
      setEnd(null)
      resource.reload()
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }
  async function toggleFull() {
    if (document.fullscreenElement) return document.exitFullscreen()
    if (full) return setFull(false)
    setFull(true)
    try {await workspace.current?.requestFullscreen?.()} catch {}
  }
  async function upload(blob) {
    if (!blob) return
    setError('')
    try {
      const result = await api('/uploads?name=' + encodeURIComponent(blob.name || 'pasted-image.png'), {method: 'POST', body: blob})
      setAttachment(result.path)
    } catch (e) {setError(e.message)}
  }
  async function copy(value) {
    setCopyNotice(await copyText(value) ? '已复制' : value ? '复制失败，请手动复制' : '请先选择终端文字')
  }
  async function paste() {
    try {
      const value = await navigator.clipboard.readText()
      terminal.current?.paste(value)
    } catch {setPasteBox(true)}
  }
  async function startDsh(external) {
    const popup = external ? window.open('about:blank', '_blank') : null
    if (popup) popup.opener = null
    setDshBusy(true)
    setError('')
    try {
      const result = await api('/dsh/web', {method: 'POST', body: {}})
      if (popup) popup.location.replace(result.url)
      else setDshFrame(result.url)
    } catch (e) {
      popup?.close()
      setError(e.message)
    } finally {setDshBusy(false)}
  }
  return <div className={'page terminal-page' + (focusMode ? ' terminal-focused' : '')}>
    {!focusMode && <PageTitle title={title} description={kind === 'codex' ? '在主机项目中运行原生 Codex CLI。' : kind === 'dsh' ? '使用官方 Web 界面，或在 Zellij 中运行原生 CLI。' : '连接主机 Shell 与持久化 Zellij 会话。'}>
      {kind === 'dsh' && <Button icon={ExternalLink} disabled={!installed || dshBusy} onClick={() => startDsh(true)}>新标签页打开</Button>}
      {kind === 'codex' && <Button icon={History} disabled={!canCreate} onClick={() => setDialog('resume')}>恢复历史</Button>}
      <Button icon={Plus} variant="primary" disabled={!canCreate} onClick={() => setDialog('new')}>{kind === 'dsh' ? '新建 DSH 终端' : '新建会话'}</Button>
    </PageTitle>}
    <ErrorNotice>{error || (tools?.zellij && resource.error)}</ErrorNotice>
    {tools && !installed && <div className="notice warning">{kind === 'shell' ? '未检测到 Zellij。请安装 Zellij 0.45 或更新版本。' : '当前主机未检测到 ' + title + '。安装官方 CLI 后刷新本页。'}</div>}
    {tools && installed && !tools.zellij && <div className="notice warning">持久终端需要 Zellij 0.45 或更新版本。</div>}
    {kind === 'dsh' && !dshFrame && !focusMode && <div className="card dsh-launch"><div><strong>DSH Web</strong><p>沿用主机用户的 DSH 配置与历史。</p></div><Button variant="primary" disabled={!installed || dshBusy} onClick={() => startDsh(false)}>{dshBusy ? '正在启动…' : '打开 DSH Web'}</Button></div>}
    {dshFrame && <section className="card dsh-frame"><div className="card-title"><h2>DSH Web</h2><Button icon={X} aria-label="关闭 DSH Web" onClick={() => setDshFrame('')}/></div><iframe src={dshFrame} title="DeepSeek Harness 官方 Web"/></section>}
    {!dshFrame && <section ref={workspace} className={'terminal-workspace' + (full ? ' full' : '') + (current ? '' : ' idle')} onPasteCapture={event => {
      if (!attachmentsAllowed) return
      const image = [...(event.clipboardData?.files || [])].find(blob => blob.type.startsWith('image/'))
      if (image) {event.preventDefault(); event.stopPropagation(); upload(image)}
    }}>
      <div className="terminal-header">
        <Button icon={hidden ? PanelLeftOpen : PanelLeftClose} aria-label={hidden ? '显示导航栏' : '隐藏导航栏'} title={hidden ? '显示导航栏' : '隐藏导航栏'} onClick={() => setHidden(!hidden)}/>
        <div className="terminal-tabs" role="tablist" aria-label="已打开的会话">
          {tabList.map(item => <div className={'term-tab' + (current?.id === item.id ? ' selected' : '')} key={item.id}>
            <button role="tab" aria-selected={current?.id === item.id} onClick={() => select(item.id)} title={item.cwd || item.name}><SquareTerminal size={14}/>{item.name}</button>
            <button className="close-tab" aria-label={'关闭标签 ' + item.name} title="关闭标签，保留会话" onClick={() => close(item.id)}><X size={14}/></button>
          </div>)}
          <Button icon={Plus} disabled={!canCreate} title="新建会话" aria-label="新建终端标签" onClick={() => setDialog('new')}/>
        </div>
        <Button icon={RefreshCw} disabled={!current} aria-label="重连" title="重连" onClick={() => terminal.current?.retry()}/>
        {attachmentsAllowed && <Button icon={FileUp} title="上传文件或图片" aria-label="上传文件或图片" onClick={() => file.current.click()}/>}
        <Button icon={full ? Minimize : Maximize} title={full ? '退出全屏' : '全屏终端'} aria-label={full ? '退出全屏' : '全屏终端'} onClick={toggleFull}/>
        <input ref={file} hidden type="file" onChange={e => {upload(e.target.files[0]); e.target.value = ''}}/>
      </div>
      {current ? <TerminalView key={current.id} id={current.id} font={font} family={family} connectRef={terminal} status={setStatus}/> :
        <Empty icon={SquareTerminal} title="打开一个终端会话">
          <p>{matching.length ? '从运行中会话连接，或打开一个新会话。' : '新建会话后可以在这里进行交互。'}</p>
          {canCreate && <Button icon={Plus} onClick={() => setDialog('new')}>新建会话</Button>}
        </Empty>}
      <div className="terminal-footer">
        <span className={'connection ' + (current && status === '已连接' ? 'connected' : '')}><i/>{current ? status : '未连接'}</span>
        {current && <><span className="terminal-cwd" title={current.cwd}>{current.cwd || current.id}</span><div className="terminal-actions">
          {copyNotice && <span role="status">{copyNotice}</span>}
          <Button icon={Keyboard} title="终端快捷键" aria-label="终端快捷键" onClick={() => setKeys(!keys)}/>
          <Button icon={Copy} title="复制选择" aria-label="复制选择" onClick={() => copy(terminal.current?.selection())}/>
          <Button icon={ClipboardPaste} title="粘贴" aria-label="粘贴" onClick={paste}/>
        </div></>}
      </div>
      {current && <div className={'terminal-keys' + (keys ? ' show' : '')}>{[['Esc','\x1b'], ['Tab','\t'], ['Ctrl+C','\x03'], ['Ctrl+D','\x04'], ['Ctrl+P','\x10'], ['Ctrl+N','\x0e'], ['↑','\x1b[A', ArrowUp], ['↓','\x1b[B', ArrowDown], ['←','\x1b[D'], ['→','\x1b[C'], ['Enter','\r']].map(([label, value, Icon]) =>
        <Button key={label} icon={Icon} aria-label={label} onMouseDown={e => e.preventDefault()} onClick={() => terminal.current?.send(value)}>{Icon ? '' : label}</Button>)}</div>}
      {attachment && <div className="terminal-attachment"><label>已上传<input aria-label="服务器文件路径" readOnly value={attachment} onFocus={e => e.target.select()}/></label>
        <Button icon={Copy} aria-label="复制文件路径" onClick={() => copy(attachment)}/><Button disabled={!current} onClick={() => terminal.current?.paste(attachment)}>插入路径</Button><Button icon={X} aria-label="关闭文件路径" onClick={() => setAttachment('')}/>
      </div>}
    </section>}
    {!focusMode && !dshFrame && <section className="card sessions">
      <div className="card-title"><h2>运行中会话 <span className="count">{matching.length}</span></h2><Button icon={RefreshCw} aria-label="刷新会话" onClick={resource.reload}/></div>
      {kind === 'codex' && <p className="section-note">连接会回到仍在运行的 Zellij 会话。恢复历史会在新的 Zellij 会话里执行 <code>codex resume</code>。</p>}
      {matching.length ? matching.map(item => <div className="entry" key={item.id}>
        <span className="metric-icon slate"><SquareTerminal size={16}/></span><div className="entry-text"><strong>{item.name}</strong><small>{item.cwd || item.id}</small></div>
        <Badge tone="good">运行中</Badge><div className="row-actions"><Button onClick={() => open(item)}>连接</Button><Button variant="danger" onClick={() => {setError(''); setEnd(item)}}>结束</Button></div>
      </div>) : <div className="empty compact">{resource.data ? '暂无运行中的会话' : '等待读取 Zellij 会话'}</div>}
    </section>}
    {dialog && <SessionForm kind={kind} mode={dialog} onClose={() => setDialog('')} onCreate={open}/>}
    {end && <Modal title="结束 Zellij 会话" onClose={() => setEnd(null)}><p>结束「{end.name}」将终止其中正在运行的进程。</p><ErrorNotice>{error}</ErrorNotice><div className="form-actions"><Button onClick={() => setEnd(null)}>取消</Button><Button variant="danger solid" disabled={busy} onClick={stop}>结束会话</Button></div></Modal>}
    {pasteBox && <Modal title="粘贴到终端" onClose={() => setPasteBox(false)}><form className="form" onSubmit={event => {event.preventDefault(); setPasteBox(false); terminal.current?.paste(pasteValue); setPasteValue('')}}><textarea aria-label="粘贴内容" rows={5} placeholder="在此粘贴文字" autoFocus value={pasteValue} onChange={e => setPasteValue(e.target.value)}/><div className="form-actions"><button className="button primary">发送到终端</button></div></form></Modal>}
  </div>
}
