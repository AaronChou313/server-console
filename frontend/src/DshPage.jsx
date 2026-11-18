import React, {useEffect, useState} from 'react'
import {ExternalLink, KeyRound, RefreshCw} from 'lucide-react'
import {api} from './api'
import {Button, ErrorNotice, Loading, Modal, PageTitle} from './ui'

export default function DshPage({installed}) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [frame, setFrame] = useState(0)
  const [loginOpen, setLoginOpen] = useState(false)
  const [login, setLogin] = useState(null)
  const [loginError, setLoginError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (installed !== true) return
    const controller = new AbortController()
    api('/dsh/web', {method: 'POST', signal: controller.signal})
      .then(result => setUrl(result.url))
      .catch(error => {if (!controller.signal.aborted) setError(error.message)})
    return () => controller.abort()
  }, [attempt, installed])

  async function loginAction(path) {
    setBusy(true)
    setLoginError('')
    try {
      const result = await api('/dsh/' + path, {method: 'POST'})
      if (path === 'login') {
        setLogin(result)
      } else {
        setLogin(null)
        setLoginOpen(false)
        if (path === 'restart') {
          setError('')
          setUrl(result.url)
          setFrame(value => value + 1)
        }
      }
    } catch (e) {setLoginError(e.message)}
    finally {setBusy(false)}
  }

  function closeLogin() {
    if (busy) return
    if (login) loginAction('login/stop')
    else setLoginOpen(false)
  }

  return <div className="page dsh-page">
    <PageTitle title="DeepSeek Harness">
      {installed && <Button icon={KeyRound} onClick={() => {setLoginError(''); setLoginOpen(true)}}>登录与配置</Button>}
      {url && <a className="button" href={url} target="_blank" rel="noreferrer"><ExternalLink size={16}/> 新标签页打开</a>}
    </PageTitle>
    {installed === false ? <section className="card"><div className="empty">
        <strong>主机未安装 DeepSeek Harness</strong>
        <p>在服务器运行 <code>./console bootstrap --with-ai</code> 安装，然后刷新此页。</p>
      </div></section>
      : error ? <div className="dsh-start-error"><ErrorNotice>{error}</ErrorNotice><Button icon={RefreshCw} onClick={() => {setError(''); setAttempt(value => value + 1)}}>重试</Button></div>
      : url ? <iframe key={frame} className="card dsh-frame" src={url} title="DeepSeek Harness 官方 Web"/>
        : <Loading>正在启动 DeepSeek Harness…</Loading>}
    {loginOpen && <Modal title="DSH 登录与配置" onClose={closeLogin}>
      <div className="form">
        {!login ? <>
          <p>使用 API Key 时，在 DSH 的「设置 → 模型」中配置。使用 DeepSeek 账号时，请通过独立的本机回环地址登录。</p>
          <div className="form-actions"><Button disabled={busy} icon={KeyRound} onClick={() => loginAction('login')}>
            {busy ? '正在启动登录页…' : '准备账号登录'}
          </Button></div>
        </> : <>
          <p>在打开浏览器的电脑上运行此命令。需要时，把末尾换成你平时使用的 SSH 用户、服务器地址或别名。</p>
          <code className="dsh-command">{login.command}</code>
          <a className="text-link" href={login.url} target="_blank" rel="noreferrer">打开 DSH 登录页</a>
          <p>完成账号登录后刷新 DSH 实例以读取凭据。刷新会中断正在运行的 DSH 任务。临时登录页约 {Math.ceil(login.seconds / 60)} 分钟后关闭。</p>
          <div className="form-actions">
            <Button disabled={busy} onClick={closeLogin}>结束登录</Button>
            <Button disabled={busy} icon={RefreshCw} onClick={() => loginAction('restart')}>刷新 DSH 实例</Button>
          </div>
        </>}
        <ErrorNotice>{loginError}</ErrorNotice>
      </div>
    </Modal>}
  </div>
}
