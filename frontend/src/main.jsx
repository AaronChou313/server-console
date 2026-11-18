import React, {lazy, Suspense, useEffect, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ArrowRight, Box, Cable, ChevronDown, Command, LayoutDashboard, LayoutGrid, LogOut, Menu, Monitor, Moon, Network, Server, Settings, Sparkles, SquareTerminal, Sun, KeyRound, X} from 'lucide-react'
import {api, useResource} from './api'
import {Badge, Button, CardTitle, ErrorNotice, Loading, Modal, PageTitle} from './ui'
import Overview from './Overview'
import AppsPage from './AppsPage'
import DshPage from './DshPage'
import ProxyPage from './ProxyPage'
import {DockerPage, NetworkPage, SSHPage} from './Management'
import './style.css'

const TerminalPage = lazy(() => import('./Terminal'))

const pages = {
  overview: [LayoutDashboard, '概览'],
  apps: [LayoutGrid, '我的应用'],
  terminal: [SquareTerminal, '终端'],
  docker: [Box, 'Docker'],
  ssh: [KeyRound, 'SSH'],
  network: [Network, '网络'],
  proxy: [Cable, '网络代理'],
  codex: [Command, 'Codex'],
  dsh: [Sparkles, 'DeepSeek Harness'],
  settings: [Settings, '设置'],
}
const fonts = {
  system: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
  classic: 'Consolas, "Liberation Mono", monospace',
  courier: '"Courier New", monospace',
}
const terminalFamily = font => '"Console Powerline", ' + (fonts[font] || fonts.system)
const isAI = page => page === 'codex' || page === 'dsh'
const isTerminal = page => page === 'terminal' || page === 'codex'
function readPage() {
  const page = location.hash.slice(1)
  return Object.hasOwn(pages, page) ? page : 'overview'
}

function storeValue(key, value) {
  localStorage.setItem(key, JSON.stringify(value))
}
function readValue(key, fallback) {
  try {return JSON.parse(localStorage.getItem(key)) ?? fallback}
  catch {return fallback}
}

function Brand() {
  return <div className="brand"><span className="brand-icon"><SquareTerminal size={23} strokeWidth={1.9}/></span><strong>Server Console</strong></div>
}

function SettingsPage({theme, setTheme, font, setFont, fontFamily, setFontFamily, tools}) {
  return <div className="page">
    <PageTitle title="设置"/>
    <section className="card settings-card">
      <CardTitle icon={Monitor} title="外观"/>
      <div className="settings-row"><div><strong>主题</strong><p>跟随设备设置，或使用固定主题。</p></div>
        <select aria-label="主题" value={theme} onChange={e => setTheme(e.target.value)}><option value="system">跟随系统</option><option value="light">亮色</option><option value="dark">暗色</option></select>
      </div>
    </section>
    <section className="card settings-card">
      <CardTitle icon={SquareTerminal} title="终端偏好"/>
      <div className="settings-row"><div><strong>字体</strong><p>使用浏览器设备上可用的等宽字体。</p></div>
        <select aria-label="终端字体" value={fontFamily} onChange={e => setFontFamily(e.target.value)}><option value="system">系统等宽</option><option value="classic">Consolas</option><option value="courier">Courier New</option></select>
      </div>
      <div className="settings-row"><div><strong>字号</strong><p>适用于 Web 终端、Codex 和 DSH CLI。</p></div>
        <select aria-label="终端字号" value={font} onChange={e => setFont(Number(e.target.value))}>{[11,12,13,14,15,16,17,18,19,20,22,24].map(n => <option key={n} value={n}>{n} px</option>)}</select>
      </div>
      <div className="font-preview" style={{fontFamily: fonts[fontFamily], fontSize: font}}><span>❯</span> server-console<br/>Aa Bb Cc 01 23 45</div>
    </section>
    <section className="card settings-card">
      <CardTitle icon={Server} title="主机工具"/>
      {Object.entries(tools || {}).map(([name, installed]) => <div className="settings-row" key={name}><strong className="mono">{name}</strong><Badge tone={installed ? 'good' : 'warning'}>{installed ? '已检测到' : '未安装'}</Badge></div>)}
    </section>
  </div>
}

function Dashboard({onLogout, theme, setTheme, dark}) {
  const [page, setPage] = useState(readPage)
  const [aiOpen, setAiOpen] = useState(true)
  const [mobileNav, setMobileNav] = useState(false)
  const [hidden, setHidden] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState('')
  const [tabs, setTabs] = useState(() => readValue('console-tabs', []))
  const [active, setActive] = useState(() => readValue('console-active', ''))
  const [font, setFont] = useState(() => readValue('console-font', 14))
  const [fontFamily, setFontFamily] = useState(() => readValue('console-font-family', 'system'))
  const overview = useResource('/overview', 3000)
  const tools = useResource('/tools', 30000)
  useEffect(() => {
    function route() {
      const next = readPage()
      setPage(next)
      if (isAI(next)) setAiOpen(true)
      if (!isTerminal(next)) setHidden(false)
      setMobileNav(false)
    }
    window.addEventListener('hashchange', route)
    return () => window.removeEventListener('hashchange', route)
  }, [])
  useEffect(() => {storeValue('console-tabs', tabs); storeValue('console-active', active)}, [tabs, active])
  useEffect(() => {storeValue('console-font', font); storeValue('console-font-family', fontFamily)}, [font, fontFamily])

  function go(next) {
    if (next === page) setMobileNav(false)
    if (next === 'overview') location.hash = 'overview'
    else location.hash = next
  }
  function openTerminal(item) {
    setTabs(list => list.some(tab => tab.id === item.id) ? list : [...list, item])
    setActive(item.id)
    go('terminal')
  }
  async function logout() {
    setLogoutError('')
    try {await api('/logout', {method: 'POST'}); onLogout()}
    catch (e) {setLogoutError(e.message)}
  }
  function navLink(key) {
    const [Icon, text] = pages[key]
    return <a className={'nav-link' + (page === key ? ' active' : '')} key={key} href={'#' + key}
      aria-current={page === key ? 'page' : undefined} onClick={() => setMobileNav(false)}><Icon size={17} strokeWidth={1.75}/><span>{text}</span></a>
  }
  return <div className={'shell' + (hidden ? ' nav-hidden' : '') + (mobileNav ? ' nav-open' : '')}>
    {mobileNav && <button className="mobile-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)}/>}
    <aside className="sidebar" aria-label="侧栏">
      <div className="sidebar-brand"><Brand/><Button icon={X} className="mobile-only" aria-label="关闭导航" onClick={() => setMobileNav(false)}/></div>
      <nav className="navigation" aria-label="主导航">
        <div className="nav-label">工作区</div>
        {['overview', 'apps', 'terminal', 'docker', 'ssh', 'network', 'proxy'].map(navLink)}
        <div className="nav-rule"/>
        <button className={'nav-link ai-group' + (isAI(page) ? ' ai-active' : '')} aria-expanded={aiOpen} onClick={() => setAiOpen(!aiOpen)}>
          <Sparkles size={17} strokeWidth={1.75}/><span>AI 工作台</span><ChevronDown className={aiOpen ? '' : 'closed'} size={14}/>
        </button>
        {aiOpen && <div className="subnav">{['codex', 'dsh'].map(navLink)}</div>}
      </nav>
      <div className="sidebar-bottom">
        {navLink('settings')}
        <div className="profile"><span className="avatar">A</span><div><strong>管理员</strong><small>本地账户</small></div>
          <Button icon={LogOut} aria-label="退出登录" title="退出登录" onClick={() => {setLogoutError(''); setLoggingOut(true)}}/>
        </div>
      </div>
    </aside>
    <main className={isTerminal(page) || page === 'dsh' ? 'main workspace-main' : 'main'}>
      {!hidden && <header className="topbar">
        <div className="topbar-left">
          <Button icon={Menu} className="mobile-only" aria-label="打开导航" onClick={() => setMobileNav(true)}/>
          <div className="breadcrumb"><Server size={15}/><span>{overview.data?.hostname || '主机'}</span><span className="slash">/</span><strong>{pages[page][1]}</strong></div>
        </div>
        <div className="topbar-right">
          <span className="topbar-platform">{overview.data?.os || ''}</span>
          <Badge tone={overview.error ? 'bad' : overview.data ? 'good' : 'neutral'}>{overview.error ? '连接中断' : overview.data ? '主机在线' : '连接中'}</Badge>
          <div className="topbar-divider"/>
          <Button icon={dark ? Sun : Moon} aria-label={dark ? '切换为亮色' : '切换为暗色'} title="切换主题" onClick={() => setTheme(dark ? 'light' : 'dark')}/>
        </div>
      </header>}
      {page === 'overview' && <Overview data={overview.data} error={overview.error} go={go}/>}
      {page === 'apps' && <AppsPage/>}
      {isTerminal(page) && <Suspense fallback={<Loading/>}><TerminalPage key={page} kind={page === 'terminal' ? 'shell' : page} font={font} family={terminalFamily(fontFamily)}
        hidden={hidden} setHidden={setHidden} tabs={tabs} setTabs={setTabs} active={active} select={setActive} tools={tools.data}/></Suspense>}
      {page === 'dsh' && <DshPage/>}
      {page === 'docker' && <DockerPage openTerminal={openTerminal}/>}
      {page === 'ssh' && <SSHPage openTerminal={openTerminal}/>}
      {page === 'network' && <NetworkPage/>}
      {page === 'proxy' && <ProxyPage/>}
      {page === 'settings' && <SettingsPage {...{theme, setTheme, font, setFont, fontFamily, setFontFamily, tools: tools.data}}/>}
    </main>
    {loggingOut && <Modal title="退出登录" onClose={() => setLoggingOut(false)}><p>Zellij 会话会继续在主机上运行。</p><ErrorNotice>{logoutError}</ErrorNotice><div className="form-actions"><Button onClick={() => setLoggingOut(false)}>取消</Button><Button variant="primary" onClick={logout}>退出登录</Button></div></Modal>}
  </div>
}

function Login({configured, done}) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {await api('/login', {method: 'POST', body: {password}}); done()}
    catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }
  return <main className="login-screen">
    <div className="login-box">
      <Brand/>
      <section className="card login-card">
        <h1>连接到主机</h1>
        {!configured ? <div className="notice warning">请先在主机上执行 <code>./console password</code>，设置后刷新页面。</div> :
          <form className="form" onSubmit={submit}>
            <label>管理员密码<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required autoFocus maxLength={256}/></label>
            <ErrorNotice>{error}</ErrorNotice><button className="button primary" disabled={busy}>{busy ? '正在登录…' : '登录'}<ArrowRight size={16}/></button>
          </form>}
      </section>
    </div>
  </main>
}

function App() {
  const resource = useResource('/auth')
  const [user, setUser] = useState(null)
  const [theme, setTheme] = useState(() => readValue('console-theme', 'system'))
  const [systemDark, setSystemDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches)
  const dark = theme === 'dark' || (theme === 'system' && systemDark)
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)')
    const update = event => setSystemDark(event.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    storeValue('console-theme', theme)
  }, [dark, theme])
  useEffect(() => {
    const logout = () => setUser(false)
    window.addEventListener('console-logout', logout)
    return () => window.removeEventListener('console-logout', logout)
  }, [])
  if (!resource.data) return <main className="login-screen"><div className="login-box"><Brand/>{resource.error ? <><ErrorNotice>{resource.error}</ErrorNotice><Button onClick={resource.reload}>重试</Button></> : <p role="status">正在连接…</p>}</div></main>
  if (user ?? resource.data.authenticated) return <Dashboard onLogout={() => setUser(false)} {...{theme, setTheme, dark}}/>
  return <Login configured={resource.data.configured} done={() => setUser(true)}/>
}

createRoot(document.getElementById('root')).render(<App/>)
