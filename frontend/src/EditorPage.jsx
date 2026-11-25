import React, {useState} from 'react'
import {FolderCode} from 'lucide-react'
import {api, useResource} from './api'
import {CardTitle, ErrorNotice, Loading, PageTitle} from './ui'

export default function EditorPage({installed}) {
  const projects = useResource(installed ? '/projects' : null)
  const [folder, setFolder] = useState(() => localStorage.getItem('console-editor-folder') || '~')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const needsHttps = !window.isSecureContext

  async function open(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const {url} = await api('/editor/open', {method: 'POST', body: {folder: folder.trim()}})
      localStorage.setItem('console-editor-folder', folder.trim())
      window.location.assign(url)
    } catch (failure) {
      setError(failure.message)
      setBusy(false)
    }
  }

  return <div className="page">
    <PageTitle title="代码编辑器"/>
    {installed === undefined ? <Loading>正在检测 code-server…</Loading> :
      installed ? <section className="card editor-card">
        <CardTitle icon={FolderCode} title="打开主机目录"/>
        <form className="form" onSubmit={open}>
          {!!projects.data?.length && <label>常用目录
            <select value={projects.data.includes(folder) ? folder : ''} onChange={event => setFolder(event.target.value)}>
              <option value="" disabled>选择目录</option>
              {projects.data.map(path => <option value={path} key={path}>{path}</option>)}
            </select>
          </label>}
          <label>主机上的目录
            <input value={folder} onChange={event => setFolder(event.target.value)} maxLength={4096}
              required placeholder="/home/user/project" autoComplete="off" autoCapitalize="none" spellCheck={false}/>
          </label>
          {needsHttps && <p className="section-note">平板、剪贴板和 Webview 建议使用控制台的 HTTPS 地址。</p>}
          <ErrorNotice>{error}</ErrorNotice>
          <div className="form-actions">
            <button className="button primary" disabled={busy || !folder.trim()}>
              <FolderCode size={16} aria-hidden="true"/> {busy ? '正在打开…' : '打开编辑器'}
            </button>
          </div>
        </form>
      </section> : <section className="card">
        <div className="empty">
          <strong>主机未安装 code-server</strong>
          <p>在主机安装后刷新此页。macOS 可运行 <code>brew install code-server</code>。</p>
          <a className="text-link" href="https://coder.com/docs/code-server/install" target="_blank" rel="noreferrer">查看 macOS 和 Linux 安装说明</a>
        </div>
      </section>}
  </div>
}
