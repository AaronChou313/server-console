import React, {useCallback, useEffect, useRef, useState} from 'react'
import {ArrowDown, ArrowUp, ClipboardPaste, Copy, FileUp, History, Keyboard, List, Maximize, Minimize, PanelLeftClose, PanelLeftOpen, Plus, RefreshCw, SquareTerminal, X} from 'lucide-react'
import {Terminal} from '@xterm/xterm'
import {FitAddon} from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import {api, useResource} from './api'
import {Button, Empty, ErrorNotice, Modal, PageTitle} from './ui'

const encoder = new TextEncoder()
const clipboardDecoder = new TextDecoder('utf-8', {fatal: true})

function decodeClipboard(data) {
  const separator = data.indexOf(';')
  if (separator < 0 || !/^[cp]?$/.test(data.slice(0, separator))) return ''
  const encoded = data.slice(separator + 1)
  if (!encoded || encoded === '?' || encoded.length > 1_400_000) return ''
  try {
    return clipboardDecoder.decode(Uint8Array.from(atob(encoded), byte => byte.charCodeAt(0)))
  } catch {
    return ''
  }
}
const terminalColors = {
  background: '#10131a', foreground: '#e9edf6', cursor: '#a9baff', selectionBackground: '#3e5689',
  black: '#171c26', red: '#f17983', green: '#8acfaa', yellow: '#f1ca83', blue: '#82abff',
  magenta: '#cda0f0', cyan: '#75d0dd', white: '#e2e8f0', brightBlack: '#7f899e', brightWhite: '#f8fafc',
}

function TerminalView({id, font, family, connectRef, status, onCopy}) {
  const mount = useRef(null)
  useEffect(() => {
    const el = mount.current
    const term = new Terminal({
      fontFamily: family, fontSize: font, lineHeight: 1.18, theme: terminalColors,
      cursorBlink: true, scrollback: 8000, allowProposedApi: false, macOptionIsMeta: true,
      macOptionClickForcesSelection: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    if (matchMedia('(pointer: fine)').matches) term.focus()
    let socket, retry, frame, waiting = 400, disposed = false, reconnectable = true
    let ready = false, lastActivity = 0
    const isReady = () => ready && socket?.readyState === WebSocket.OPEN
    const send = input => {
      if (isReady()) socket.send(encoder.encode(input))
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
      if (isReady()) socket.send(Uint8Array.from(input, char => char.charCodeAt(0)))
    })
    let dragStart, clipboardGestureUntil = 0, zellijSelection = ''
    function startSelection(event) {
      if (!event.isTrusted || event.button !== 0) return
      dragStart = {x: event.clientX, y: event.clientY}
      zellijSelection = ''
      clipboardGestureUntil = 0
    }
    function endSelection(event) {
      if (!event.isTrusted || event.button !== 0 || !dragStart) return
      if (Math.hypot(event.clientX - dragStart.x, event.clientY - dragStart.y) >= 3) {
        clipboardGestureUntil = performance.now() + 2500
      }
      dragStart = null
    }
    const clipboard = term.parser.registerOscHandler(52, data => {
      // OSC 52 can also come from ordinary program output. Require a fresh
      // local drag and only accept a single clipboard write for that gesture.
      if (!clipboardGestureUntil || performance.now() > clipboardGestureUntil) return true
      const value = decodeClipboard(data)
      if (!value) return true
      clipboardGestureUntil = 0
      zellijSelection = value
      onCopy(value, true)
      return true
    })
    el.addEventListener('mousedown', startSelection, true)
    document.addEventListener('mouseup', endSelection, true)
    const selection = () => term.getSelection() || zellijSelection
    term.attachCustomKeyEventHandler(event => {
      if (event.type !== 'keydown') return true
      const key = event.key.toLowerCase()
      if ((event.metaKey || (event.ctrlKey && event.shiftKey)) && ['c', 'v'].includes(key)) {
        if (key === 'c' && selection()) {
          event.preventDefault()
          onCopy(selection())
        }
        return false
      }
      return true
    })
    function dropSocket() {
      const stale = socket
      socket = null
      ready = false
      stale?.close()
    }
    function reconnect() {
      status(navigator.onLine ? '连接中断 · 正在重试' : '网络离线 · 等待重连')
      if (!navigator.onLine) return
      retry = setTimeout(connect, waiting)
      waiting = Math.min(waiting * 2, 5000)
    }
    function connect() {
      if (disposed || !reconnectable || socket || !navigator.onLine) return
      clearTimeout(retry)
      if (el.clientWidth >= 20 && el.clientHeight >= 20) fit.fit()
      status('正在连接')
      const connection = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host +
        '/ws/terminal/' + encodeURIComponent(id) + '?cols=' + term.cols + '&rows=' + term.rows)
      socket = connection
      connection.binaryType = 'arraybuffer'
      lastActivity = Date.now()
      connection.onopen = () => {if (socket === connection) size()}
      connection.onmessage = event => {
        if (socket !== connection) return
        lastActivity = Date.now()
        if (typeof event.data === 'string') return
        if (!ready) {
          term.reset()
          ready = true
          waiting = 400
          status('已连接')
        }
        term.write(new Uint8Array(event.data))
      }
      connection.onclose = event => {
        if (socket !== connection) return
        socket = null
        ready = false
        if ([4400, 4402, 4404].includes(event.code)) {
          reconnectable = false
          status(event.reason || (event.code === 4404 ? '会话已结束' : '连接失败'))
          return
        }
        reconnect()
        if (event.code === 1006) api('/auth').then(value => {
          if (!value.authenticated && !disposed) window.dispatchEvent(new Event('console-logout'))
        }).catch(() => {})
      }
    }
    function checkConnection() {
      if (!socket) return
      if (!document.hidden && Date.now() - lastActivity > 30000) {
        dropSocket()
        reconnect()
      } else if (socket.readyState === WebSocket.OPEN) {
        socket.send('ping')
      }
    }
    function offline() {
      if (!reconnectable) return
      clearTimeout(retry)
      dropSocket()
      status('网络离线 · 等待重连')
    }
    function online() {
      if (reconnectable) connect()
    }
    function visible() {
      if (!document.hidden) {
        lastActivity = Date.now()
        checkConnection()
        if (!socket && reconnectable && navigator.onLine) connect()
      }
    }
    connectRef.current = {
      focus: () => term.focus(),
      selection,
      isReady,
      send: value => {send(value); term.focus()},
      paste: value => {if (isReady()) {term.paste(value); term.focus()}},
      retry: () => {
        reconnectable = true
        waiting = 400
        dropSocket()
        if (navigator.onLine) connect()
        else offline()
      },
      font: (size, fontFamily) => {
        term.options.fontSize = size
        term.options.fontFamily = fontFamily
        refreshSize()
      },
    }
    if (navigator.onLine) connect()
    else offline()
    const heartbeat = setInterval(checkConnection, 10000)
    window.addEventListener('online', online)
    window.addEventListener('offline', offline)
    document.addEventListener('visibilitychange', visible)
    document.fonts?.ready.then(() => {if (!disposed) refreshSize()})
    return () => {
      disposed = true
      connectRef.current = null
      window.removeEventListener('online', online)
      window.removeEventListener('offline', offline)
      document.removeEventListener('visibilitychange', visible)
      clearInterval(heartbeat)
      observer.disconnect()
      el.removeEventListener('mousedown', startSelection, true)
      document.removeEventListener('mouseup', endSelection, true)
      clipboard.dispose()
      clearTimeout(retry)
      cancelAnimationFrame(frame)
      dropSocket()
      data.dispose()
      binary.dispose()
      resize.dispose()
      term.dispose()
    }
  }, [id, connectRef, status, onCopy])
  useEffect(() => {connectRef.current?.font(font, family)}, [font, family, connectRef])
  return <div ref={mount} className="terminal-screen" aria-label="交互式终端"/>
}

async function copyText(value) {
  if (!value) return false
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {await navigator.clipboard.writeText(value); return true} catch {}
  }
  const input = document.createElement('textarea')
  input.value = value
  input.setAttribute('readonly', '')
  input.style.cssText = 'position:fixed;left:0;top:-1000px;opacity:0.01'
  const focused = document.activeElement
  const container = document.fullscreenElement || document.body
  container.appendChild(input)
  input.focus()
  input.select()
  input.setSelectionRange(0, value.length)
  let result = false
  try {result = document.execCommand('copy')} catch {}
  input.remove()
  focused?.focus({preventScroll: true})
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
  const title = mode === 'resume' ? '恢复 Codex 历史会话' : kind === 'codex' ? '新建 Codex 会话' : '新建终端会话'
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
  return <Modal title={title} onClose={() => {if (!busy) onClose()}}>
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
      <div className="form-actions"><Button onClick={onClose} disabled={busy}>取消</Button><button className="button primary" disabled={busy}>{busy ? '正在启动…' : mode === 'resume' ? '打开历史会话' : '创建会话'}</button></div>
    </form>
  </Modal>
}

export default function TerminalPage({kind, font, family, hidden, setHidden, tabs, setTabs, active, select, tools}) {
  const resource = useResource('/terminal', tools?.zellij ? 9000 : 0)
  const [status, setStatus] = useState('正在连接')
  const [dialog, setDialog] = useState('')
  const [end, setEnd] = useState(null)
  const [canForce, setCanForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [attachment, setAttachment] = useState('')
  const [pasteBox, setPasteBox] = useState(false)
  const [pasteValue, setPasteValue] = useState('')
  const [full, setFull] = useState(false)
  const [keys, setKeys] = useState(false)
  const [showSessions, setShowSessions] = useState(false)
  const [copyNotice, setCopyNotice] = useState('')
  const [copyFallback, setCopyFallback] = useState('')
  const terminal = useRef(null), file = useRef(null), workspace = useRef(null)
  const copyField = useRef(null)
  const sessionPanel = useRef(null), sessionToggle = useRef(null)
  const title = kind === 'codex' ? 'Codex' : 'Web 终端'
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
    if (!showSessions) return
    sessionPanel.current?.focus()
    const onPointerDown = event => {
      if (!sessionPanel.current?.contains(event.target) && !sessionToggle.current?.contains(event.target)) {
        setShowSessions(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [showSessions])
  useEffect(() => {
    if (!copyNotice) return
    const timer = setTimeout(() => setCopyNotice(''), 2900)
    return () => clearTimeout(timer)
  }, [copyNotice])

  useEffect(() => {
    if (!copyFallback) return
    copyField.current.focus()
    copyField.current.select()
  }, [copyFallback])

  function open(item) {
    setTabs(list => list.some(tab => tab.id === item.id) ? list : [...list, item])
    select(item.id)
    setShowSessions(false)
    if (current?.id === item.id) terminal.current?.focus()
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
    setError('')
    const id = end.id
    try {
      await api('/terminal/' + encodeURIComponent(id) + (canForce ? '?force=true' : ''), {method: 'DELETE'})
      close(id)
      setEnd(null)
      resource.reload()
    } catch (e) {
      setCanForce(e.status === 409)
      setError(e.message)
    } finally {setBusy(false)}
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
  const copy = useCallback(async (value, automatic = false) => {
    if (!value) {
      setCopyNotice('请先选择终端文字')
      return
    }
    if (await copyText(value)) {
      setCopyNotice('已复制到本机')
    } else if (automatic) {
      setCopyNotice('已选中，点击复制')
    } else {
      setCopyFallback(value)
    }
  }, [])
  async function paste() {
    const target = terminal.current
    try {
      const value = await navigator.clipboard.readText()
      if (target === terminal.current && target?.isReady()) target.paste(value)
      else {setPasteValue(value); setPasteBox(true)}
    } catch {setPasteBox(true)}
  }
  function submitPaste(event) {
    event.preventDefault()
    const target = terminal.current
    if (!target?.isReady()) return
    event.currentTarget.closest('dialog').close()
    setPasteBox(false)
    target.paste(pasteValue)
    setPasteValue('')
  }
  function closeSessions() {
    setShowSessions(false)
    sessionToggle.current?.focus()
  }
  return <div className={'page terminal-page' + (focusMode ? ' terminal-focused' : '')}>
    {!focusMode && <PageTitle title={title}>
      {kind === 'codex' && <Button icon={History} disabled={!canCreate} onClick={() => setDialog('resume')}>恢复历史</Button>}
      <Button icon={Plus} variant="primary" disabled={!canCreate} onClick={() => setDialog('new')}>新建会话</Button>
    </PageTitle>}
    <ErrorNotice>{(!end && error) || (tools?.zellij && resource.error)}</ErrorNotice>
    {tools && !installed && <div className="notice warning">{kind === 'shell' ? '未检测到 Zellij。请安装 Zellij 0.45 或更新版本。' : '当前主机未检测到 ' + title + '。安装官方 CLI 后刷新本页。'}</div>}
    {tools && installed && !tools.zellij && <div className="notice warning">持久终端需要 Zellij 0.45 或更新版本。</div>}
    <section ref={workspace} className={'terminal-workspace' + (full ? ' full' : '')} onPasteCapture={event => {
      const image = [...(event.clipboardData?.files || [])].find(blob => blob.type.startsWith('image/'))
      if (image) {event.preventDefault(); event.stopPropagation(); upload(image)}
    }}>
      <div className="terminal-header">
        <Button icon={hidden ? PanelLeftOpen : PanelLeftClose} aria-label={hidden ? '显示导航栏' : '隐藏导航栏'} title={hidden ? '显示导航栏' : '隐藏导航栏'} onClick={() => setHidden(!hidden)}/>
        <div className="terminal-tabs" role="tablist" aria-label="已打开的会话">
          {tabList.map(item => <div className={'term-tab' + (current?.id === item.id ? ' selected' : '')} key={item.id}>
            <button role="tab" aria-selected={current?.id === item.id} onClick={() => {select(item.id); if (current?.id === item.id) terminal.current?.focus()}} title={item.cwd || item.name}><SquareTerminal size={14}/><span>{item.name}</span></button>
            <button className="close-tab" aria-label={'关闭标签 ' + item.name} title="关闭标签，保留会话" onClick={() => close(item.id)}><X size={14}/></button>
          </div>)}
          {(current || focusMode) && <Button icon={Plus} disabled={!canCreate} title="新建会话" aria-label="新建终端标签" onClick={() => setDialog('new')}/>}
        </div>
        <button ref={sessionToggle} type="button" className="button sessions-toggle" title="运行中的会话"
          aria-label="运行中的会话" aria-expanded={showSessions} aria-controls="terminal-sessions"
          onClick={() => showSessions ? closeSessions() : setShowSessions(true)}>
          <List size={16}/><span>会话</span>{resource.data && <span className="session-count">{matching.length}</span>}
        </button>
        <Button icon={RefreshCw} disabled={!current} aria-label="重连" title="重连" onClick={() => terminal.current?.retry()}/>
        <Button icon={FileUp} title="上传文件或图片" aria-label="上传文件或图片" onClick={() => file.current.click()}/>
        <Button icon={full ? Minimize : Maximize} title={full ? '退出全屏' : '全屏终端'} aria-label={full ? '退出全屏' : '全屏终端'} onClick={toggleFull}/>
        <input ref={file} hidden type="file" onChange={e => {upload(e.target.files[0]); e.target.value = ''}}/>
      </div>
      {current ? <TerminalView key={current.id} id={current.id} font={font} family={family} connectRef={terminal} status={setStatus} onCopy={copy}/> :
        <Empty icon={SquareTerminal} title="没有打开的会话">
          {matching.length > 0 ? <Button icon={List} onClick={() => setShowSessions(true)}>查看会话</Button>
            : focusMode && canCreate && <Button icon={Plus} onClick={() => setDialog('new')}>新建会话</Button>}
        </Empty>}
      <div className="terminal-footer">
        <span role="status" className={'connection ' + (current && status === '已连接' ? 'connected' : '')}><i/>{current ? status : '未连接'}</span>
        {current && <><span className="terminal-cwd" title={current.cwd}>{current.cwd || current.id}</span><div className="terminal-actions">
          {copyNotice && <span role="status">{copyNotice}</span>}
          {current.managed === false && <Button title="发送 Esc，退出 Zellij 的滚动或快捷键模式"
            disabled={status !== '已连接'} onClick={() => terminal.current?.send('\x1b')}>恢复输入</Button>}
          <Button icon={Keyboard} title="终端快捷键" aria-label="终端快捷键" onClick={() => setKeys(!keys)}/>
          <Button icon={Copy} title="复制选中文字（鼠标模式下按住 Shift，Mac 上按住 Option 再拖选）" aria-label="复制选择" onMouseDown={event => event.preventDefault()}
            onClick={() => copy(terminal.current?.selection())}/>
          <Button icon={ClipboardPaste} title="粘贴" aria-label="粘贴" disabled={status !== '已连接'} onMouseDown={event => event.preventDefault()} onClick={paste}/>
        </div></>}
      </div>
      {current && <div className={'terminal-keys' + (keys ? ' show' : '')}>{[['Esc','\x1b'], ['Tab','\t'], ['Ctrl+C','\x03'], ['Ctrl+D','\x04'], ['Ctrl+P','\x10'], ['Ctrl+N','\x0e'], ['↑','\x1b[A', ArrowUp], ['↓','\x1b[B', ArrowDown], ['←','\x1b[D'], ['→','\x1b[C'], ['Enter','\r']].map(([label, value, Icon]) =>
        <Button key={label} icon={Icon} aria-label={label} onMouseDown={e => e.preventDefault()} onClick={() => terminal.current?.send(value)}>{Icon ? '' : label}</Button>)}</div>}
      {attachment && <div className="terminal-attachment"><label>已上传<input aria-label="服务器文件路径" readOnly value={attachment} onFocus={e => e.target.select()}/></label>
        <Button icon={Copy} aria-label="复制文件路径" onMouseDown={event => event.preventDefault()} onClick={() => copy(attachment)}/><Button disabled={!current || status !== '已连接'} onClick={() => terminal.current?.paste(attachment)}>插入路径</Button><Button icon={X} aria-label="关闭文件路径" onClick={() => setAttachment('')}/>
      </div>}
      {showSessions && <section id="terminal-sessions" className="card sessions" ref={sessionPanel} tabIndex={-1}
        aria-labelledby="terminal-sessions-title" onKeyDown={event => {
          if (event.key === 'Escape') {event.stopPropagation(); closeSessions()}
        }}>
        <div className="card-title">
          <h2 id="terminal-sessions-title">运行中的会话</h2>
          <div className="actions"><Button icon={RefreshCw} aria-label="刷新会话" onClick={resource.reload}/><Button icon={X} aria-label="关闭会话列表" onClick={closeSessions}/></div>
        </div>
        <div className="sessions-list">
          {matching.length ? matching.map(item => <div className="entry" key={item.id}>
            <div className="entry-text"><strong title={item.name}>{item.name}</strong>{item.cwd && <small title={item.cwd}>{item.cwd}</small>}</div>
            <div className="row-actions">
              <Button variant={current?.id === item.id ? '' : 'primary'} onClick={() => open(item)}>{current?.id === item.id ? '返回' : '连接'}</Button>
              <Button variant="danger" onClick={() => {setError(''); setCanForce(false); setEnd(item)}}>结束</Button>
            </div>
          </div>) : <div className="empty compact">{resource.error || (resource.data ? '暂无运行中的会话' : '正在读取 Zellij 会话…')}</div>}
        </div>
      </section>}
    </section>
    {dialog && <SessionForm kind={kind} mode={dialog} onClose={() => setDialog('')} onCreate={open}/>}
    {end && <Modal title="结束 Zellij 会话" onClose={() => {if (!busy) setEnd(null)}}><p>结束「{end.name}」将终止其中正在运行的进程。</p><ErrorNotice>{error}</ErrorNotice><div className="form-actions"><Button disabled={busy} onClick={() => setEnd(null)}>取消</Button><Button variant="danger solid" disabled={busy} onClick={stop}>{busy ? '正在结束…' : canForce ? '强制结束' : '结束会话'}</Button></div></Modal>}
    {copyFallback && <Modal title="复制终端文字" onClose={() => setCopyFallback('')}>
      <div className="form">
        <p>浏览器限制了网页写入剪贴板。文字已选中，可以用系统的复制快捷键或长按复制。</p>
        <textarea ref={copyField} aria-label="待复制文字" rows={6} readOnly value={copyFallback}/>
      </div>
    </Modal>}
    {pasteBox && <Modal title="粘贴到终端" onClose={() => setPasteBox(false)}><form className="form" onSubmit={submitPaste}><textarea aria-label="粘贴内容" rows={5} placeholder="在此粘贴文字" autoFocus value={pasteValue} onChange={e => setPasteValue(e.target.value)}/><div className="form-actions"><button className="button primary" disabled={status !== '已连接'}>发送到终端</button></div></form></Modal>}
  </div>
}
